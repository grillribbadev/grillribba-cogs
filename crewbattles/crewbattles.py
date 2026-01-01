import asyncio
import copy
import io
import json
import random
import re
import time
import inspect  # <-- ADD THIS
from datetime import datetime, timezone
from pathlib import Path

import discord
from redbot.core import commands, Config, bank
from redbot.core.data_manager import cog_data_path

from .constants import DEFAULT_GUILD, DEFAULT_USER, BASE_HP, MAX_LEVEL, DEFAULT_PRICE_RULES
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .embeds import battle_embed
from .utils import exp_to_next
from .admin_commands import AdminCommandsMixin
from .player_commands import PlayerCommandsMixin

HAKI_TRAIN_COST = 500
HAKI_TRAIN_COOLDOWN = 60 * 60

DEFAULT_BATTLE_COOLDOWN = 60
MIN_BATTLE_COOLDOWN = 10
MAX_BATTLE_COOLDOWN = 3600


class CrewBattles(AdminCommandsMixin, PlayerCommandsMixin, commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        self.config.register_global(maintenance=False)
        self.config.register_guild(**DEFAULT_GUILD)
        self.config.register_user(**DEFAULT_USER)

        self.players = PlayerManager(self)
        data_dir = cog_data_path(self) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.fruits = FruitManager(data_dir)
        self.teams = TeamsBridge(bot)

        self._active_battles = set()
        self._backup_task = self.bot.loop.create_task(self._periodic_backup())

    def cog_unload(self):
        try:
            self._backup_task.cancel()
        except Exception:
            pass

    # -----------------------------
    # Backups
    # -----------------------------
    def _backup_dir(self) -> Path:
        d = cog_data_path(self) / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _write_backup(self, *, note: str = "") -> Path:
        all_users = await self.config.all_users()
        payload = {
            "meta": {
                "cog": "crewbattles",
                "ts": datetime.now(timezone.utc).isoformat(),
                "note": note,
                "count": len(all_users or {}),
            },
            "users": all_users or {},
        }
        fname = f"users_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        path = self._backup_dir() / fname

        def _sync_write():
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        await asyncio.to_thread(_sync_write)
        return path

    async def _periodic_backup(self):
        await asyncio.sleep(30)
        while True:
            try:
                await self._write_backup(note="periodic")
            except Exception as e:
                print(f"[CrewBattles] periodic backup failed: {e}")
            await asyncio.sleep(6 * 60 * 60)

    async def _restore_backup(self, backup_path: Path) -> int:
        def _sync_read():
            with open(backup_path, "r", encoding="utf-8") as f:
                return json.load(f)

        data = await asyncio.to_thread(_sync_read)
        users = (data or {}).get("users") or {}
        if not isinstance(users, dict):
            raise ValueError("Backup file format invalid: users is not a dict")

        restored = 0
        for uid, pdata in users.items():
            try:
                uid_int = int(uid)
            except Exception:
                continue
            if not isinstance(pdata, dict):
                continue
            await self.config.user_from_id(uid_int).set(pdata)
            restored += 1
        return restored

    # -----------------------------
    # Economy helpers (BeriCore or bank)
    # -----------------------------
    def _beri(self):
        return self.bot.get_cog("BeriCore")

    async def _get_money(self, member: discord.abc.User) -> int:
        core = self._beri()
        if core:
            try:
                return int(await core.get_beri(member))
            except Exception:
                pass
        try:
            return int(await bank.get_balance(member))
        except Exception:
            return 0

    async def _add_money(self, member: discord.abc.User, amount: int, *, reason: str = "") -> bool:
        amount = int(amount or 0)
        if amount == 0:
            return True

        core = self._beri()
        if core:
            try:
                await core.add_beri(member, amount, reason=reason or "crew_battles:add", bypass_cap=True)
                return True
            except Exception:
                pass

        try:
            if amount > 0:
                await bank.deposit_credits(member, amount)
                return True
            await bank.withdraw_credits(member, abs(amount))
            return True
        except Exception:
            return False

    async def _spend_money(self, member: discord.abc.User, amount: int, *, reason: str = "") -> bool:
        amount = int(amount or 0)
        if amount <= 0:
            return True
        bal = await self._get_money(member)
        if bal < amount:
            return False
        return await self._add_money(member, -amount, reason=reason or "crew_battles:spend")

    async def _add_beri(self, member: discord.abc.User, amount: int, *, reason: str = "") -> bool:
        # backwards-compatible alias used elsewhere in this file
        return await self._add_money(member, amount, reason=reason)

    async def _team_of(self, guild: discord.Guild, member: discord.Member):
        try:
            if hasattr(self.teams, "team_of"):
                return await self.teams.team_of(guild, member)
        except Exception:
            pass
        return None

    # -----------------------------
    # Maintenance check
    # -----------------------------
    async def cog_check(self, ctx: commands.Context) -> bool:
        # DO NOT await a bool. Only await if the base returns an awaitable.
        base_check = getattr(super(), "cog_check", None)
        if callable(base_check):
            try:
                res = base_check(ctx)
                if inspect.isawaitable(res):
                    res = await res
                if res is False:
                    return False
            except Exception:
                # don't let base check crash help/commands
                pass

        try:
            maintenance = bool(await self.config.maintenance())
        except Exception:
            maintenance = False

        if maintenance:
            try:
                if await self.bot.is_owner(ctx.author):
                    return True
            except Exception:
                pass
            if ctx.guild and getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator:
                return True
            try:
                await ctx.reply("Crew Battles is in maintenance mode (admins/owner only).")
            except Exception:
                pass
            return False

        # tempban enforcement (admins/owner bypass; cbadmin bypass)
        try:
            if ctx.command and ctx.command.qualified_name.startswith("cbadmin"):
                return True
        except Exception:
            pass

        try:
            if await self.bot.is_owner(ctx.author):
                return True
        except Exception:
            pass
        if ctx.guild and getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator:
            return True

        try:
            pdata = await self.players.get(ctx.author)
            until = int(pdata.get("tempban_until", 0) or 0)
            now = self._now()
            if until > now:
                remaining = until - now
                await ctx.reply(f"⛔ You are temporarily banned from Crew Battles for {remaining}s.")
                return False
        except Exception:
            pass

        return True

    # -----------------------------
    # EXP logic
    # -----------------------------
    def _apply_exp(self, player: dict, gain: int) -> int:
        try:
            gain = int(gain or 0)
        except Exception:
            gain = 0

        try:
            cur_level = int(player.get("level", 1) or 1)
        except Exception:
            cur_level = 1
        try:
            cur_exp = int(player.get("exp", 0) or 0)
        except Exception:
            cur_exp = 0

        cur_level = max(1, cur_level)
        cur_exp = max(0, cur_exp) + gain

        leveled = 0
        while cur_level < MAX_LEVEL:
            needed = exp_to_next(cur_level)
            try:
                needed = int(needed)
            except Exception:
                break
            if needed <= 0:
                break
            if cur_exp >= needed:
                cur_exp -= needed
                cur_level += 1
                leveled += 1
            else:
                break

        if cur_level >= MAX_LEVEL:
            cur_level = MAX_LEVEL
            cur_exp = 0

        player["level"] = int(cur_level)
        player["exp"] = int(cur_exp)
        return leveled

    def _now(self) -> int:
        """Unix timestamp (seconds). Used for cooldowns/tempbans."""
        return int(time.time())

    # =========================================================
    # Admin commands
    # =========================================================
    @commands.group(name="cbadmin", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def cbadmin(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @cbadmin.command(name="backup")
    async def cbadmin_backup(self, ctx: commands.Context):
        async with ctx.typing():
            path = await self._write_backup(note=f"manual by {ctx.author.id}")
        await ctx.reply(f"Backup written: `{path.name}`")

    @cbadmin.command(name="restore")
    async def cbadmin_restore(self, ctx: commands.Context, filename: str = None, confirm: str = None):
        if not filename:
            files = sorted([p.name for p in self._backup_dir().glob("users_*.json")])[-10:]
            if not files:
                return await ctx.reply("No backup files found.")
            return await ctx.reply("Available backups (latest 10):\n" + "\n".join(f"- `{n}`" for n in files))

        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin restore <filename> confirm`")

        bp = self._backup_dir() / filename
        if not bp.exists():
            return await ctx.reply("Backup file not found.")

        async with ctx.typing():
            restored = await self._restore_backup(bp)
        await ctx.reply(f"Restored {restored} user record(s) from `{bp.name}`")

    @cbadmin.command(name="storedcounts")
    async def cbadmin_storedcounts(self, ctx: commands.Context):
        try:
            all_users = await self.config.all_users()
        except Exception as e:
            return await ctx.reply(f"Could not read storage: {e}")
        total = len(all_users or {})
        started = sum(1 for _, v in (all_users or {}).items() if isinstance(v, dict) and v.get("started"))
        await ctx.reply(f"Stored user records: {total} | started=True: {started}")

    @cbadmin.command(name="resetall", aliases=["resetstarted", "resetplayers"])
    async def cbadmin_resetall(self, ctx: commands.Context, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin resetall confirm`")

        async with ctx.typing():
            try:
                await self._write_backup(note=f"pre-resetall by {ctx.author.id}")
            except Exception as e:
                return await ctx.reply(f"Backup failed; aborting reset: {e}")

            all_users = await self.players.all()
            started_ids = []
            for uid, pdata in (all_users or {}).items():
                if isinstance(pdata, dict) and pdata.get("started"):
                    try:
                        started_ids.append(int(uid))
                    except Exception:
                        pass

            reset = 0
            for uid in started_ids:
                try:
                    await self.config.user_from_id(uid).set(copy.deepcopy(DEFAULT_USER))
                    reset += 1
                except Exception:
                    pass

        await ctx.reply(f"Reset data for {reset} started player(s).")

    @cbadmin.command(name="wipeall", aliases=["wipeusers"])
    async def cbadmin_wipeall(self, ctx: commands.Context, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin wipeall confirm`")

        async with ctx.typing():
            try:
                await self._write_backup(note=f"pre-wipeall by {ctx.author.id}")
            except Exception as e:
                return await ctx.reply(f"Backup failed; aborting wipe: {e}")

            all_users = await self.players.all()
            uids = []
            for uid in (all_users or {}).keys():
                try:
                    uids.append(int(uid))
                except Exception:
                    pass

            wiped = 0
            for uid in uids:
                try:
                    await self.config.user_from_id(uid).clear()
                    wiped += 1
                except Exception:
                    pass

        await ctx.reply(f"HARD WIPE complete. Cleared {wiped} stored user record(s).")

    @cbadmin.command()
    async def setberi(self, ctx, win: int, loss: int = 0):
        await self.config.guild(ctx.guild).beri_win.set(int(win))
        await self.config.guild(ctx.guild).beri_loss.set(int(loss))
        await ctx.reply(f"Beri rewards updated. Win={int(win)} Loss={int(loss)}")

    @cbadmin.command()
    async def setturn_delay(self, ctx, delay: float):
        await self.config.guild(ctx.guild).turn_delay.set(float(delay))
        await ctx.reply(f"Turn delay set to {delay}s")

    @cbadmin.command()
    async def sethakicost(self, ctx, cost: int):
        await self.config.guild(ctx.guild).haki_cost.set(int(cost))
        await ctx.reply(f"Haki training cost set to {int(cost)} per point")

    @cbadmin.command()
    async def sethakicooldown(self, ctx, seconds: int):
        await self.config.guild(ctx.guild).haki_cooldown.set(int(seconds))
        await ctx.reply(f"Haki training cooldown set to {int(seconds)} seconds")

    @cbadmin.command(name="setcrewpointswin", aliases=["setcrewwinpoints", "setcrewpoints"])
    async def setcrewpointswin(self, ctx, points: int):
        points = int(points)
        if points < 0:
            return await ctx.reply("Points cannot be negative.")
        await self.config.guild(ctx.guild).crew_points_win.set(points)
        await ctx.reply(f"Crew points per win set to {points} (0 disables).")

    @cbadmin.command(name="setexpwin")
    async def cbadmin_setexpwin(self, ctx, min_exp: int, max_exp: int = None):
        if max_exp is None:
            max_exp = min_exp
        if min_exp < 0 or max_exp < 0 or max_exp < min_exp:
            return await ctx.reply("Invalid range.")
        await self.config.guild(ctx.guild).exp_win_min.set(int(min_exp))
        await self.config.guild(ctx.guild).exp_win_max.set(int(max_exp))
        await ctx.reply(f"Winner EXP set to {min_exp}–{max_exp} per win.")

    @cbadmin.command(name="setexploss")
    async def cbadmin_setexploss(self, ctx, min_exp: int, max_exp: int = None):
        if max_exp is None:
            max_exp = min_exp
        if min_exp < 0 or max_exp < 0 or max_exp < min_exp:
            return await ctx.reply("Invalid range.")
        await self.config.guild(ctx.guild).exp_loss_min.set(int(min_exp))
        await self.config.guild(ctx.guild).exp_loss_max.set(int(max_exp))
        await ctx.reply(f"Loser EXP set to {min_exp}–{max_exp} per loss.")

    @cbadmin.command(name="fixlevels", aliases=["recalclevels", "recalcexp"])
    async def cbadmin_fixlevels(self, ctx: commands.Context):
        async with ctx.typing():
            all_users = await self.players.all()
            changed = 0
            total = 0
            for uid, pdata in (all_users or {}).items():
                total += 1
                if not isinstance(pdata, dict):
                    continue
                before_lvl = int(pdata.get("level", 1) or 1)
                before_exp = int(pdata.get("exp", 0) or 0)

                self._apply_exp(pdata, 0)

                after_lvl = int(pdata.get("level", 1) or 1)
                after_exp = int(pdata.get("exp", 0) or 0)

                if after_lvl != before_lvl or after_exp != before_exp:
                    try:
                        await self.config.user_from_id(int(uid)).set(pdata)
                        changed += 1
                    except Exception:
                        pass
        await ctx.reply(f"Recalculated levels. Updated {changed} / {total} records.")

    @cbadmin.command(name="setconquerorcost", aliases=["setconqcost", "setconquerorscost"])
    async def cbadmin_setconquerorcost(self, ctx, cost: int):
        """Set the Beri cost to unlock Conqueror's Haki."""
        cost = int(cost)
        if cost < 0:
            return await ctx.reply("Cost cannot be negative.")
        await self.config.guild(ctx.guild).conqueror_unlock_cost.set(cost)
        await ctx.reply(f"Conqueror unlock cost set to {cost:,} Beri.")

    @cbadmin.command(name="resetuser")
    async def cbadmin_resetuser(self, ctx: commands.Context, member: discord.Member, confirm: str = None):
        """Reset a single user's CrewBattles data."""
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin resetuser @member confirm`")

        async with ctx.typing():
            try:
                await self.config.user(member).set(copy.deepcopy(DEFAULT_USER))
            except Exception as e:
                return await ctx.reply(f"Reset failed: {e}")

        await ctx.reply(f"✅ Reset Crew Battles data for **{member.display_name}**.")

    # =========================================================
    # Player commands
    # =========================================================

    @commands.command(name="startcb")
    async def startcb(self, ctx: commands.Context):
        p = await self.players.get(ctx.author)
        if p.get("started"):
            return await ctx.reply("Already started. Use `.cbprofile`.")

        p = copy.deepcopy(DEFAULT_USER)
        p["started"] = True

        # starter fruit (5% chance, does not consume shop stock)
        fruit_name = None
        try:
            if random.random() < 0.05:  # 5% chance
                pool = self.fruits.pool_all() or []
                if pool:
                    pick = random.choice(pool)
                    if isinstance(pick, dict):
                        fruit_name = pick.get("name")
        except Exception:
            fruit_name = None

        p["fruit"] = fruit_name

        await self.players.save(ctx.author, p)

        # If you have the new startcb embed:
        # make sure it displays "None" if fruit_name is None

        # NEW: starter embed (replaces plain text)
        fruit_name = p.get("fruit") or "None"
        lvl = int(p.get("level", 1) or 1)
        exp = int(p.get("exp", 0) or 0)

        e = discord.Embed(
            title="🏴‍☠️ Crew Battles Activated!",
            description=(
                "Welcome aboard. Your pirate record has been created.\n\n"
                "**Next steps:**\n"
                "• 📘 Run **`.cbtutorial`** to learn the basics\n"
                "• 👤 View your profile with **`.cbprofile`**\n"
                "• 🛒 Browse fruits with **`.cbshop`**\n"
                "• ⚔️ Challenge someone with **`.battle @user`**"
            ),
            color=discord.Color.blurple(),
        )
        try:
            e.set_thumbnail(url=ctx.author.display_avatar.url)
        except Exception:
            pass

        e.add_field(
            name="🎒 Starting Loadout",
            value=f"🍈 **Fruit:** `{fruit_name}`\n❤️ **Battle HP:** `{int(BASE_HP)}`",
            inline=False,
        )
        e.add_field(
            name="📈 Progress",
            value=f"Level: `{lvl}` • EXP: `{exp}`\nTrain Haki: **`.cbtrain armament|observation|conqueror <points>`**",
            inline=False,
        )
        e.set_footer(text="Tip: Use .cbhaki to see your Haki bonuses (crit/dodge/counter).")
        return await ctx.reply(embed=e)

    # REMOVE / DISABLE THIS LEGACY COMMAND (it overrides the mixin cbshop)
    # @commands.command()
    async def _cbshop_legacy(self, ctx: commands.Context, page: int = 1):
        ""
        # ...existing code...
        # (keep body if you want, but it is no longer registered as a command)
        return await ctx.send("Legacy cbshop disabled. Use the mixin cbshop command.")

    @commands.command()
    async def cbshop(self, ctx: commands.Context, page: int = 1):
        items = self.fruits.all() or []
        if not items:
            return await ctx.send("Shop is empty.")

        page = max(1, int(page or 1))
        per = 8
        start = (page - 1) * per
        chunk = items[start : start + per]
        if not chunk:
            return await ctx.send("That page is empty.")

        e = discord.Embed(title="Devil Fruit Shop", color=discord.Color.gold())
        lines = []
        for f in chunk:
            name = f.get("name", "Unknown")
            price = int(f.get("price", 0) or 0)
            bonus = int(f.get("bonus", 0) or 0)
            stock = f.get("stock", None)
            stock_txt = "∞" if stock is None else str(stock)
            lines.append(f"- {name} | {price:,} Beri | Bonus +{bonus} | Stock: {stock_txt}")
        e.description = "\n".join(lines)
        e.set_footer(text=f"Page {page} | Buy: .cbbuy <fruit name>")
        await ctx.send(embed=e)

    @commands.command()
    async def cbbuy(self, ctx: commands.Context, *, fruit_name: str):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.send("You must `.startcb` first.")
        if p.get("fruit"):
            return await ctx.send("You already have a fruit. Use `.cbremovefruit` first.")

        fruit = self.fruits.get(fruit_name)
        if not fruit:
            return await ctx.send("That fruit does not exist.")

        stock = fruit.get("stock", None)
        if stock is not None and int(stock) <= 0:
            return await ctx.send("That fruit is out of stock.")

        price = int(fruit.get("price", 0) or 0)
        ok = await self._spend_money(ctx.author, price, reason="crew_battles:buy_fruit")
        if not ok:
            bal = await self._get_money(ctx.author)
            return await ctx.send(f"Not enough Beri. Price {price:,}, you have {bal:,}.")

        p["fruit"] = fruit["name"]
        await self.players.save(ctx.author, p)

        if stock is not None:
            try:
                fruit["stock"] = max(0, int(stock) - 1)
                self.fruits.update(fruit)
            except Exception:
                pass

        await ctx.send(f"Bought {fruit['name']} for {price:,} Beri.")

    @commands.command()
    async def cbremovefruit(self, ctx: commands.Context):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.send("You must `.startcb` first.")
        if not p.get("fruit"):
            return await ctx.send("You do not have a fruit equipped.")

        g = await self.config.guild(ctx.guild).all()
        cost = int(g.get("remove_fruit_cost", 0) or 0)
        if cost > 0:
            ok = await self._spend_money(ctx.author, cost, reason="crew_battles:remove_fruit")
            if not ok:
                bal = await self._get_money(ctx.author)
                return await ctx.send(f"Not enough Beri to remove fruit. Cost {cost:,}, you have {bal:,}.")

        old = p.get("fruit")
        p["fruit"] = None
        await self.players.save(ctx.author, p)
        await ctx.send(f"Removed fruit ({old}).")

    @commands.command()
    async def cbprofile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        p = await self.players.get(member)
        if not p.get("started"):
            return await ctx.reply("This player has not started Crew Battles.")

        wins = int(p.get("wins", 0) or 0)
        losses = int(p.get("losses", 0) or 0)
        total = wins + losses
        winrate = (wins / total * 100) if total else 0.0

        haki = p.get("haki", {}) or {}
        arm = int(haki.get("armament", 0) or 0)
        obs = int(haki.get("observation", 0) or 0)
        conq = bool(haki.get("conquerors"))
        conq_lvl = int(haki.get("conqueror", 0) or 0)

        fruit_name = p.get("fruit") or "None"

        # show fruit even if it's not currently stocked in the shop
        fruit_detail = None
        if p.get("fruit"):
            try:
                fruit_detail = self.fruits.get(fruit_name)  # shop lookup
            except Exception:
                fruit_detail = None
            if not fruit_detail:
                try:
                    fruit_detail = self.fruits.pool_get(fruit_name)  # pool lookup fallback
                except Exception:
                    fruit_detail = None

        fruit_txt = fruit_name
        if isinstance(fruit_detail, dict):
            fruit_txt = f"{fruit_name} | {str(fruit_detail.get('type','')).title()} | +{int(fruit_detail.get('bonus',0) or 0)}"

        embed = discord.Embed(
            title=f"{member.display_name}'s Crew Profile",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass

        embed.add_field(
            name="Progress",
            value=(
                f"Level: {int(p.get('level', 1) or 1)} | EXP: {int(p.get('exp', 0) or 0)}\n"
                f"Wins: {wins} | Losses: {losses} | Win Rate: {winrate:.1f}%"
            ),
            inline=False,
        )
        embed.add_field(name="Devil Fruit", value=fruit_txt, inline=False)

        conq_line = "Locked"
        if conq:
            conq_line = f"Unlocked | {conq_lvl}/100 (counter crit)"

        embed.add_field(
            name="Haki",
            value=(
                f"Armament: {arm}/100 (crit chance)\n"
                f"Observation: {obs}/100 (dodge chance)\n"
                f"Conqueror: {conq_line}"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Battle HP is flat: {int(BASE_HP)}")
        await ctx.reply(embed=embed)

    @commands.command()
    async def cbhaki(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        p = await self.players.get(member)
        if not p.get("started"):
            return await ctx.reply("This player has not started Crew Battles.")

        haki = p.get("haki", {}) or {}
        arm = int(haki.get("armament", 0) or 0)
        obs = int(haki.get("observation", 0) or 0)

        # FIX: conqueror unlocked flag is 'conquerors'
        conq = bool(haki.get("conquerors"))
        conq_lvl = int(haki.get("conqueror", 0) or 0)

        def bar(val: int, maxv: int = 100, width: int = 12) -> str:
            val = max(0, min(maxv, int(val)))
            filled = int(round((val / maxv) * width))
            return "🟦" * filled + "⬛" * (width - filled)

        title = f"🌊 Haki Awakening — {member.display_name}"
        embed = discord.Embed(title=title, color=discord.Color.purple())

        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass

        embed.add_field(
            name="🛡️ Armament (CRIT)",
            value=f"`{arm}/100`\n{bar(arm)}\n🎯 Boosts **critical hit chance**",
            inline=False,
        )
        embed.add_field(
            name="👁️ Observation (DODGE)",
            value=f"`{obs}/100`\n{bar(obs)}\n💨 Boosts **dodge chance**",
            inline=False,
        )

        if conq:
            embed.add_field(
                name="👑 Conqueror (COUNTER CRIT)",
                value=f"`Unlocked` • `{conq_lvl}/100`\n{bar(conq_lvl)}\n⚡ Chance to **counter-attack** with **critical damage**",
                inline=False,
            )
        else:
            embed.add_field(
                name="👑 Conqueror (COUNTER CRIT)",
                value="`Locked`\n🔓 Unlock at **Level 10** with `.cbunlockconqueror`",
                inline=False,
            )

        embed.set_footer(text="Train: .cbtrain armament|observation|conqueror [points]")
        await ctx.reply(embed=embed)

    @commands.command(name="cbtrainhaki")
    async def cbtrainhaki(self, ctx: commands.Context, haki_type: str, points: int = 1):
        """
        Train Haki for Beri.
        Usage: .cbtrainhaki armament|observation|conqueror [points]
        """
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must start Crew Battles first (`.startcb`).")

        haki_type = (haki_type or "").lower().strip()
        if haki_type in ("conq", "conquerors"):
            haki_type = "conqueror"
        if haki_type not in ("armament", "observation", "conqueror"):
            return await ctx.reply("Type must be: `armament`, `observation`, or `conqueror`.")

        points = int(points or 1)
        if points <= 0:
            return await ctx.reply("Points must be a positive number.")

        haki = p.get("haki", {}) or {}

        # Conqueror requires unlock
        if haki_type == "conqueror" and not bool(haki.get("conquerors")):
            return await ctx.reply("👑 Conqueror is locked. Unlock it at level 10 with `.cbunlockconqueror`.")

        # cooldown (per haki type)
        now = self._now()
        ts_map = p.get("haki_train_ts") or {}
        if not isinstance(ts_map, dict):
            ts_map = {}

        g = await self.config.guild(ctx.guild).all()
        cooldown = int(g.get("haki_cooldown", HAKI_TRAIN_COOLDOWN) or HAKI_TRAIN_COOLDOWN)
        last = int(ts_map.get(haki_type, 0) or 0)
        remaining = (last + cooldown) - now
        if remaining > 0:
            return await ctx.reply(f"⏳ You must wait `{remaining}s` before training **{haki_type}** again.")

        # cost
        cost_per = int(g.get("haki_cost", HAKI_TRAIN_COST) or HAKI_TRAIN_COST)
        total_cost = max(0, cost_per * points)

        ok = await self._spend_money(ctx.author, total_cost, reason="crew_battles:train_haki")
        if not ok:
            bal = await self._get_money(ctx.author)
            return await ctx.reply(f"Not enough Beri. Cost `{total_cost:,}`, you have `{bal:,}`.")

        # apply training
        key = "conqueror" if haki_type == "conqueror" else haki_type
        cur = int(haki.get(key, 0) or 0)
        new = min(100, cur + points)
        haki[key] = new

        # ensure conqueror flag stays consistent
        if haki_type == "conqueror":
            haki["conquerors"] = True

        p["haki"] = haki
        ts_map[haki_type] = now
        p["haki_train_ts"] = ts_map
        await self.players.save(ctx.author, p)

        await ctx.reply(
            f"✅ Trained **{haki_type}**: `{cur}` → `{new}` (spent `{total_cost:,}` Beri)."
        )

    @commands.command(name="cbtrain")
    async def cbtrain(self, ctx, haki_type: str, points: int = 1):
        return await self.cbtrainhaki(ctx, haki_type, points)

    @commands.command(name="battle")
    async def battle(self, ctx: commands.Context, opponent: discord.Member):
        """Start a Crew Battle with animated embed + results screen."""
        if opponent.bot:
            return await ctx.reply("You can't battle bots.")
        if opponent.id == ctx.author.id:
            return await ctx.reply("You can't battle yourself.")

        # prevent concurrent battles in same channel
        if ctx.channel.id in self._active_battles:
            return await ctx.reply("A battle is already running in this channel.")
        self._active_battles.add(ctx.channel.id)

        try:
            p1 = await self.players.get(ctx.author)
            p2 = await self.players.get(opponent)
            if not p1.get("started"):
                return await ctx.reply("You must `.startcb` first.")
            if not p2.get("started"):
                return await ctx.reply("That user must `.startcb` first.")

            # tempban handled by cog_check; still guard opponent edge cases
            if int(p1.get("tempban_until", 0) or 0) > self._now():
                return await ctx.reply("You are temporarily banned from Crew Battles.")
            if int(p2.get("tempban_until", 0) or 0) > self._now():
                return await ctx.reply("That user is temporarily banned from Crew Battles.")

            # battle cooldown (per-player)
            now = self._now()
            cd = int(p1.get("battle_cd", DEFAULT_BATTLE_COOLDOWN) or DEFAULT_BATTLE_COOLDOWN)
            cd = max(MIN_BATTLE_COOLDOWN, min(MAX_BATTLE_COOLDOWN, cd))
            last = int(p1.get("last_battle", 0) or 0)
            rem = (last + cd) - now
            if rem > 0:
                return await ctx.reply(f"⏳ You must wait `{rem}s` before battling again.")

            # optional: block same team
            try:
                t1 = await self._team_of(ctx.guild, ctx.author)
                t2 = await self._team_of(ctx.guild, opponent)
                if t1 is not None and t2 is not None and str(t1) == str(t2):
                    return await ctx.reply("You can only battle members of other teams.")
            except Exception:
                pass

            # simulate battle
            winner_key, turns, final_hp1, final_hp2 = simulate(p1, p2, self.fruits)

            # animated embed
            g = await self.config.guild(ctx.guild).all()
            turn_delay = float(g.get("turn_delay", 1.0) or 1.0)
            turn_delay = max(0.0, min(5.0, turn_delay))  # hard clamp so it doesn't freeze channels

            hp1 = int(BASE_HP)
            hp2 = int(BASE_HP)

            log_lines = []
            msg = await ctx.send(embed=battle_embed(ctx.author, opponent, hp1, hp2, BASE_HP, BASE_HP, "—"))

            # Play turns (cap log lines so embed stays readable)
            for side, dmg, defender_hp_after, atk_name, crit in turns:
                if side == "p1":
                    hp2 = int(defender_hp_after)
                    actor = ctx.author.display_name
                    defender = opponent.display_name
                else:
                    hp1 = int(defender_hp_after)
                    actor = opponent.display_name
                    defender = ctx.author.display_name

                tag = ""
                if isinstance(atk_name, str) and atk_name.startswith("🍈 "):
                    tag = " **DEVIL FRUIT**"
                elif crit:
                    tag = " **CRIT**"

                if int(dmg) <= 0 and str(atk_name).lower() == "dodged":
                    line = f"💨 **{defender}** dodged **{actor}**'s attack!"
                else:
                    line = f"🗡️ **{actor}** used **{atk_name}** for `{int(dmg)}` damage.{tag}"

                log_lines.append(line)
                log_lines = log_lines[-10:]  # last 10 lines only
                log_text = "\n".join(log_lines)

                try:
                    await msg.edit(embed=battle_embed(ctx.author, opponent, hp1, hp2, BASE_HP, BASE_HP, log_text))
                except Exception:
                    pass

                if turn_delay > 0:
                    await asyncio.sleep(turn_delay)

            # determine winner/loser
            winner_user = ctx.author if winner_key == "p1" else opponent
            loser_user = opponent if winner_key == "p1" else ctx.author
            winner_p = p1 if winner_key == "p1" else p2
            loser_p = p2 if winner_key == "p1" else p1

            # rewards: exp + beri + crew points
            winner_p["wins"] = int(winner_p.get("wins", 0) or 0) + 1
            loser_p["losses"] = int(loser_p.get("losses", 0) or 0) + 1

            win_min = int(g.get("exp_win_min", 0) or 0)
            win_max = int(g.get("exp_win_max", 0) or 0)
            loss_min = int(g.get("exp_loss_min", 0) or 0)
            loss_max = int(g.get("exp_loss_max", 0) or 0)

            win_gain = random.randint(min(win_min, win_max), max(win_min, win_max)) if max(win_min, win_max) > 0 else 0
            loss_gain = random.randint(min(loss_min, loss_max), max(loss_min, loss_max)) if max(loss_min, loss_max) > 0 else 0

            leveled_w = self._apply_exp(winner_p, win_gain)
            leveled_l = self._apply_exp(loser_p, loss_gain)

            beri_win = int(g.get("beri_win", 0) or 0)
            beri_loss = int(g.get("beri_loss", 0) or 0)
            if beri_win:
                await self._add_beri(winner_user, beri_win, reason="crew_battle:win")
            if beri_loss:
                await self._add_beri(loser_user, beri_loss, reason="crew_battle:loss")

            crew_points = int(g.get("crew_points_win", 1) or 1)
            points_added = 0
            if crew_points > 0:
                try:
                    ok = await self.teams.award_win(ctx, winner_user, crew_points)
                    if ok:
                        points_added = crew_points
                except Exception:
                    points_added = 0

            # stamp battle cooldown
            p1["last_battle"] = now

            # persist
            await self.players.save(winner_user, winner_p)
            await self.players.save(loser_user, loser_p)
            # (also save challenger cooldown)
            await self.players.save(ctx.author, p1)

            # ---------------- Results Embed (flair) ----------------
            def _who(m: discord.Member) -> str:
                try:
                    if m.display_name and m.display_name != m.name:
                        return f"{m.display_name} ({m.name})"
                except Exception:
                    pass
                return getattr(m, "display_name", getattr(m, "name", "Unknown"))

            winner_who = _who(winner_user)
            loser_who = _who(loser_user)

            res = discord.Embed(
                title="🏁 Crew Battle Results",
                description=f"⚔️ **{winner_who}** defeated **{loser_who}**",
                color=discord.Color.green(),
            )
            try:
                res.set_thumbnail(url=winner_user.display_avatar.url)
            except Exception:
                pass

            winner_level = int(winner_p.get("level", 1) or 1)
            winner_exp = int(winner_p.get("exp", 0) or 0)
            loser_level = int(loser_p.get("level", 1) or 1)
            loser_exp = int(loser_p.get("exp", 0) or 0)

            winner_lines = [
                f"💰 **Beri:** `+{beri_win:,}`",
                f"⭐ **EXP Gained:** `+{win_gain}`",
                f"✨ **Current EXP:** `{winner_exp}`",
                f"📈 **Level:** `{winner_level}`" + (f" *(+{leveled_w})*" if leveled_w else ""),
                f"🏴‍☠️ **Crew Points Added:** `+{points_added}`",
            ]
            res.add_field(name=f"🏆 Winner — {winner_who}", value="\n".join(winner_lines), inline=False)

            # loser rewards in “lower/smaller” style using blockquote + italics
            loser_lines = [
                f"💰 Beri: `+{beri_loss:,}`",
                f"⭐ EXP Gained: `+{loss_gain}`",
                f"✨ Current EXP: `{loser_exp}`",
                f"📉 Level: `{loser_level}`" + (f" *(+{leveled_l})*" if leveled_l else ""),
            ]
            loser_value = "\n".join(f"> *{line}*" for line in loser_lines)
            res.add_field(name=f"☠️ Loser — {loser_who}", value=loser_value, inline=False)

            res.set_footer(text="✨ Armament=CRIT • Observation=DODGE • Conqueror=COUNTER CRIT • Fruits can proc abilities")
            await ctx.reply(embed=res)

        finally:
            try:
                self._active_battles.discard(ctx.channel.id)
            except Exception:
                pass

    @commands.command(name="cbleaderboard", aliases=["cblb", "cbtop"])
    async def cbleaderboard(self, ctx: commands.Context, page: int = 1, sort_by: str = "wins"):
        """
        Show Crew Battles leaderboard.
        Usage: .cbleaderboard [page] [wins|level|winrate]
        """
        sort_by = (sort_by or "wins").lower().strip()
        if sort_by not in ("wins", "level", "winrate"):
            sort_by = "wins"

        all_users = await self.players.all()
        entries = []
        for uid, pdata in (all_users or {}).items():
            if not isinstance(pdata, dict) or not pdata.get("started"):
                continue
            try:
                uid_int = int(uid)
            except Exception:
                continue

            wins = int(pdata.get("wins", 0) or 0)
            losses = int(pdata.get("losses", 0) or 0)
            lvl = int(pdata.get("level", 1) or 1)
            exp = int(pdata.get("exp", 0) or 0)
            total = wins + losses
            winrate = (wins / total * 100.0) if total else 0.0

            entries.append(
                {
                    "uid": uid_int,
                    "wins": wins,
                    "losses": losses,
                    "level": lvl,
                    "exp": exp,
                    "winrate": winrate,
                }
            )

        if not entries:
            return await ctx.reply("No Crew Battles players found yet. Use `.startcb` to begin.")

        if sort_by == "level":
            entries.sort(key=lambda x: (x["level"], x["exp"], x["wins"]), reverse=True)
        elif sort_by == "winrate":
            entries.sort(key=lambda x: (x["winrate"], x["wins"], x["level"]), reverse=True)
        else:
            entries.sort(key=lambda x: (x["wins"], x["winrate"], x["level"]), reverse=True)

        per = 10
        page = max(1, int(page or 1))
        start = (page - 1) * per
        chunk = entries[start : start + per]
        if not chunk:
            return await ctx.reply("That page is empty.")

        e = discord.Embed(
            title="🏆 Crew Battles Leaderboard",
            description=f"Sorted by **{sort_by}** • Page **{page}**",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )

        lines = []
        for i, row in enumerate(chunk, start=start + 1):
            m = ctx.guild.get_member(row["uid"]) if ctx.guild else None
            name = m.display_name if m else f"User {row['uid']}"

            medal = ""
            if i == 1:
                medal = "🥇 "
            elif i == 2:
                medal = "🥈 "
            elif i == 3:
                medal = "🥉 "

            lines.append(
                f"{medal}`#{i:>2}` **{name}** — "
                f"🏅 Wins: `{row['wins']}` | ☠️ Losses: `{row['losses']}` | "
                f"📈 Lvl: `{row['level']}` | 🎯 WR: `{row['winrate']:.1f}%`"
            )

        e.add_field(name="Top Pirates", value="\n".join(lines), inline=False)
        e.set_footer(text="Use: .cbleaderboard <page> <wins|level|winrate>")
        await ctx.reply(embed=e)

    @commands.is_owner()
    @commands.command(name="cbdebugbattle")
    async def cbdebugbattle(self, ctx: commands.Context, other: discord.Member):
        """Owner-only: dump battle-relevant stats for two users."""
        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(other)

        def fruit_info(p):
            fname = p.get("fruit")
            if not fname:
                return ("None", 0, "")
            f = None
            try:
                f = self.fruits.get(fname) or self.fruits.pool_get(fname)
            except Exception:
                f = None
            if not isinstance(f, dict):
                return (str(fname), 0, "")
            return (f.get("name", fname), int(f.get("bonus", 0) or 0), str(f.get("ability", "") or ""))

        def haki_info(p):
            h = p.get("haki", {}) or {}
            return (
                int(h.get("armament", 0) or 0),
                int(h.get("observation", 0) or 0),
                bool(h.get("conquerors")),
                int(h.get("conqueror", 0) or 0),
            )

        f1 = fruit_info(p1)
        f2 = fruit_info(p2)
        h1 = haki_info(p1)
        h2 = haki_info(p2)

        await ctx.reply(
            "**Battle inputs:**\n"
            f"**You:** fruit=`{f1[0]}` bonus=`{f1[1]}` ability=`{f1[2] or 'None'}` "
            f"haki(A/O/Cunlock/Clvl)=`{h1}`\n"
            f"**Other:** fruit=`{f2[0]}` bonus=`{f2[1]}` ability=`{f2[2] or 'None'}` "
            f"haki(A/O/Cunlock/Clvl)=`{h2}`"
        )

    # =========================================================
    # Admin: Fruits (pool + shop)
    # =========================================================
    @cbadmin.group(name="fruits", invoke_without_command=True)
    async def cbadmin_fruits(self, ctx: commands.Context):
        """Manage fruit pool/shop."""
        await ctx.send_help()

    @cbadmin_fruits.command(name="import")
    async def cbadmin_fruits_import(self, ctx: commands.Context):
        """Import fruits JSON into the POOL (catalog). Attach a JSON file."""
        if not ctx.message.attachments:
            return await ctx.reply("Attach a JSON file: `.cbadmin fruits import` with `fruits.json` attached.")

        att = ctx.message.attachments[0]
        try:
            raw = await att.read()
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return await ctx.reply(f"Failed to read JSON: {e}")

        try:
            ok, bad = self.fruits.pool_import(payload)
        except Exception as e:
            return await ctx.reply(f"Import failed: {e}")

        await ctx.reply(f"✅ Imported into pool: {ok} OK, {bad} failed.")

    @cbadmin_fruits.command(name="export")
    async def cbadmin_fruits_export(self, ctx: commands.Context, which: str = "pool"):
        """Export fruits JSON. `pool` or `shop`."""
        which = (which or "pool").lower().strip()
        if which not in ("pool", "shop"):
            return await ctx.reply("Use: `.cbadmin fruits export pool` or `.cbadmin fruits export shop`")

        if which == "pool":
            data = {"fruits": self.fruits.pool_all()}
            fname = "fruits_pool.json"
        else:
            data = {"shop": self.fruits.shop_list()}
            fname = "fruits_shop.json"

        b = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        await ctx.reply(file=discord.File(fp=io.BytesIO(b), filename=fname))

    @cbadmin_fruits.command(name="pool")
    async def cbadmin_fruits_pool(self, ctx: commands.Context, page: int = 1):
        items = self.fruits.pool_all() or []
        if not items:
            return await ctx.reply("Pool is empty.")

        page = max(1, int(page or 1))
        per = 10
        start = (page - 1) * per
        chunk = items[start : start + per]
        if not chunk:
            return await ctx.reply("That page is empty.")

        lines = []
        for f in chunk:
            name = f.get("name", "Unknown")
            ftype = str(f.get("type", "unknown")).title()
            bonus = int(f.get("bonus", 0) or 0)
            price = int(f.get("price", 0) or 0)
            ability = f.get("ability") or "None"
            lines.append(f"- **{name}** ({ftype}) `+{bonus}` | `{price:,}` | *{ability}*")

        e = discord.Embed(title="🍈 Fruit Pool (Catalog)", description="\n".join(lines), color=discord.Color.blurple())
        e.set_footer(text=f"Page {page} | Add: .cbadmin fruits pooladd <name> <type> <bonus> <price> <ability...>")
        await ctx.reply(embed=e)

    @cbadmin_fruits.command(name="shop")
    async def cbadmin_fruits_shop(self, ctx: commands.Context, page: int = 1):
        items = self.fruits.shop_list() or []
        if not items:
            return await ctx.reply("Shop is empty.")

        page = max(1, int(page or 1))
        per = 10
        start = (page - 1) * per
        chunk = items[start : start + per]
        if not chunk:
            return await ctx.reply("That page is empty.")

        lines = []
        for f in chunk:
            name = f.get("name", "Unknown")
            price = int(f.get("price", 0) or 0)
            bonus = int(f.get("bonus", 0) or 0)
            ability = f.get("ability") or "None"
            stock = f.get("stock", None)
            stock_txt = "∞" if stock is None else str(stock)
            lines.append(f"- **{name}** | `{price:,}` | `+{bonus}` | Stock: `{stock_txt}` | *{ability}*")

        e = discord.Embed(title="🛒 Fruit Shop (Stocked)", description="\n".join(lines), color=discord.Color.gold())
        e.set_footer(text=f"Page {page} | Stock: .cbadmin fruits shopadd <stock|unlimited> <fruit name...>")
        await ctx.reply(embed=e)

    @cbadmin_fruits.command(name="pooladd")
    async def cbadmin_fruits_pooladd(self, ctx: commands.Context, name: str, ftype: str, bonus: int, price: int, *, ability: str = ""):
        """Add/update a fruit in the POOL."""
        fruit = {"name": name, "type": ftype, "bonus": int(bonus), "price": int(price), "ability": (ability or "").strip()}
        try:
            saved = self.fruits.pool_upsert(fruit)
        except Exception as e:
            return await ctx.reply(f"Failed: {e}")

        await ctx.reply(
            f"✅ Pool updated: **{saved['name']}** ({str(saved.get('type','?')).title()}) "
            f"`+{int(saved.get('bonus',0) or 0)}` | `{int(saved.get('price',0) or 0):,}` | "
            f"Ability: **{saved.get('ability') or 'None'}**"
        )

    @cbadmin_fruits.command(name="shopadd")
    async def cbadmin_fruits_shopadd(self, ctx: commands.Context, stock: str, *, name: str):
        """Stock a fruit (must exist in pool)."""
        try:
            st = self._parse_stock_token(stock)
        except Exception:
            return await ctx.reply("Invalid stock. Use a number or `unlimited`.")

        try:
            self.fruits.shop_add(name, st)
        except Exception as e:
            return await ctx.reply(f"Failed: {e}")

        await ctx.reply(f"✅ Stocked: **{name}** (stock: {'∞' if st is None else st})")

    @cbadmin_fruits.command(name="setstock")
    async def cbadmin_fruits_setstock(self, ctx: commands.Context, stock: str, *, name: str):
        """Set stock for an existing shop item."""
        try:
            st = self._parse_stock_token(stock)
        except Exception:
            return await ctx.reply("Invalid stock. Use a number or `unlimited`.")

        try:
            self.fruits.shop_set_stock(name, st)
        except Exception as e:
            return await ctx.reply(f"Failed: {e}")

        await ctx.reply(f"✅ Stock updated: **{name}** => {'∞' if st is None else st}")

    @cbadmin_fruits.command(name="shopremove")
    async def cbadmin_fruits_shopremove(self, ctx: commands.Context, *, name: str):
        """Remove a fruit from the shop (does not delete it from pool)."""
        try:
            self.fruits.shop_remove(name)
        except Exception as e:
            return await ctx.reply(f"Failed: {e}")
        await ctx.reply(f"✅ Removed from shop: **{name}**")

    @commands.command(name="cbtutorial", aliases=["cbguide", "cbhelp"])
    async def cbtutorial(self, ctx: commands.Context):
        return await ctx.send("`cbtutorial` moved to `player_commands.py`. Reload the cog.")