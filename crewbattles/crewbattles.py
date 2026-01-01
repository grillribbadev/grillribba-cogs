import asyncio
import copy
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import discord
from redbot.core import commands, Config, bank
from redbot.core.data_manager import cog_data_path

from .constants import DEFAULT_GUILD, DEFAULT_USER, BASE_HP, MAX_LEVEL
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .embeds import battle_embed
from .utils import exp_to_next

HAKI_TRAIN_COST = 500
HAKI_TRAIN_COOLDOWN = 60 * 60

DEFAULT_BATTLE_COOLDOWN = 60
MIN_BATTLE_COOLDOWN = 10
MAX_BATTLE_COOLDOWN = 3600


class CrewBattles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC0A55EE, force_registration=True)

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
        # existing maintenance logic first
        ok = await super().cog_check(ctx) if hasattr(super(), "cog_check") else True

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

        # starter fruit (does not consume stock)
        fruit_name = None
        try:
            items = self.fruits.pool_all() or []
            if items:
                pick = random.choice(items)
                if isinstance(pick, dict):
                    fruit_name = pick.get("name")
        except Exception:
            fruit_name = None

        p["fruit"] = fruit_name
        await self.players.save(ctx.author, p)

        if fruit_name:
            return await ctx.reply(f"Started Crew Battles. Starter fruit: {fruit_name}")
        return await ctx.reply("Started Crew Battles.")

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

    # -----------------------------
    # Small helpers
    # -----------------------------
    def _now(self) -> int:
        return int(time.time())

    def _parse_duration_seconds(self, s: str) -> int:
        """
        Accepts:
          - "3600"
          - "10m", "2h", "3d"
          - "1h30m", "2d6h"
        """
        s = (s or "").strip().lower()
        if not s:
            raise ValueError("duration required")
        if s.isdigit():
            return int(s)

        parts = re.findall(r"(\d+)\s*([smhd])", s.replace(" ", ""))
        if not parts:
            raise ValueError("invalid duration")

        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        total = 0
        for n, u in parts:
            total += int(n) * mult[u]
        return total

    def _parse_stock_token(self, token: str):
        t = (token or "").strip().lower()
        if t in ("unlimited", "inf", "infinite", "∞", "none"):
            return None
        return int(token)

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

        # starter fruit (does not consume stock)
        fruit_name = None
        try:
            items = self.fruits.pool_all() or []
            if items:
                pick = random.choice(items)
                if isinstance(pick, dict):
                    fruit_name = pick.get("name")
        except Exception:
            fruit_name = None

        p["fruit"] = fruit_name
        await self.players.save(ctx.author, p)

        if fruit_name:
            return await ctx.reply(f"Started Crew Battles. Starter fruit: {fruit_name}")
        return await ctx.reply("Started Crew Battles.")

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

    @commands.command(name="cbtrain")
    async def cbtrain(self, ctx, haki_type: str, points: int = 1):
        return await self.cbtrainhaki(ctx, haki_type, points)

    @commands.command(name="pbprofile")
    async def pbprofile(self, ctx, member: discord.Member = None):
        # alias requested: .pbprofile -> .cbprofile
        return await self.cbprofile(ctx, member)

    @commands.command()
    async def cbunlockconqueror(self, ctx):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must start Crew Battles first (.startcb).")

        haki = p.get("haki", {}) or {}
        if bool(haki.get("conquerors")):
            return await ctx.reply("Conqueror is already unlocked.")

        lvl = int(p.get("level", 1) or 1)
        if lvl < 10:
            return await ctx.reply("You must reach level 10 to unlock Conqueror.")

        g = await self.config.guild(ctx.guild).all()
        cost = int(g.get("conqueror_unlock_cost", 5000) or 5000)

        ok = await self._spend_money(ctx.author, cost, reason="crew_battles:unlock_conqueror")
        if not ok:
            bal = await self._get_money(ctx.author)
            return await ctx.reply(f"Need {cost:,} Beri. You have {bal:,}.")

        # ensure both keys exist
        haki["conquerors"] = True
        haki["conqueror"] = int(haki.get("conqueror", 0) or 0)

        p["haki"] = haki
        await self.players.save(ctx.author, p)
        await ctx.reply(f"👑 Unlocked Conqueror's Haki for **{cost:,}** Beri. Train it with `.cbtrain conqueror <points>`.")

    @commands.command()
    async def cbtutorial(self, ctx):
        embed = discord.Embed(
            title="Crew Battles Tutorial",
            color=discord.Color.teal(),
            description="Player commands",
        )
        embed.add_field(name="Start", value="`.startcb` | `.cbprofile [@member]` | `.cbhaki [@member]`", inline=False)
        embed.add_field(
            name="Battle",
            value="`.battle @user` | `.cbbattlecd [seconds]` | `.cbleaderboard [wins|winrate|level|exp] [limit]`",
            inline=False,
        )
        embed.add_field(name="Fruits", value="`.cbshop [page]` | `.cbbuy <fruit name>` | `.cbremovefruit`", inline=False)
        embed.add_field(
            name="Haki",
            value="`.cbtrainhaki <armament|observation|conqueror> [points]` | `.cbunlockconqueror`",
            inline=False,
        )
        embed.set_footer(text=f"Battle HP is flat {int(BASE_HP)}. Armament=crit, Observation=dodge, Conqueror=counter crit.")
        await ctx.reply(embed=embed)

    @commands.command(name="cbbattlecd", aliases=["cbcd", "cdbcooldown"])
    async def cbbattlecd(self, ctx: commands.Context, seconds: int = None):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must start Crew Battles first (.startcb).")

        current = int(p.get("battle_cd", DEFAULT_BATTLE_COOLDOWN) or DEFAULT_BATTLE_COOLDOWN)
        if seconds is None:
            return await ctx.reply(f"Your battle cooldown is {current} seconds.")

        seconds = int(seconds)
        if seconds < MIN_BATTLE_COOLDOWN or seconds > MAX_BATTLE_COOLDOWN:
            return await ctx.reply(f"Cooldown must be between {MIN_BATTLE_COOLDOWN} and {MAX_BATTLE_COOLDOWN} seconds.")

        p["battle_cd"] = seconds
        await self.players.save(ctx.author, p)
        await ctx.reply(f"Battle cooldown set to {seconds} seconds.")

    @commands.command()
    async def battle(self, ctx, opponent: discord.Member):
        if ctx.author == opponent:
            return await ctx.reply("You cannot battle yourself.")
        if opponent.bot:
            return await ctx.reply("You cannot battle bots.")

        now = int(time.time())
        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(opponent)
        if not p1.get("started") or not p2.get("started"):
            return await ctx.reply("Both players must `.startcb` first.")

        cd1 = int(p1.get("battle_cd", DEFAULT_BATTLE_COOLDOWN) or DEFAULT_BATTLE_COOLDOWN)
        cd2 = int(p2.get("battle_cd", DEFAULT_BATTLE_COOLDOWN) or DEFAULT_BATTLE_COOLDOWN)
        last1 = int(p1.get("last_battle", 0) or 0)
        last2 = int(p2.get("last_battle", 0) or 0)

        rem1 = (last1 + cd1) - now
        if rem1 > 0:
            return await ctx.reply(f"You must wait {rem1}s before starting another battle.")
        rem2 = (last2 + cd2) - now
        if rem2 > 0:
            return await ctx.reply(f"{opponent.display_name} is on battle cooldown for {rem2}s.")

        if ctx.channel.id in self._active_battles:
            return await ctx.reply("A battle is already in progress in this channel.")

        # optional team restriction
        try:
            t1 = await self._team_of(ctx.guild, ctx.author)
            t2 = await self._team_of(ctx.guild, opponent)
            if t1 is not None and t2 is not None and t1 == t2:
                return await ctx.reply("You can only challenge players from other teams.")
        except Exception:
            pass

        self._active_battles.add(ctx.channel.id)

        # stamp cooldown immediately
        p1["last_battle"] = now
        p2["last_battle"] = now
        await self.players.save(ctx.author, p1)
        await self.players.save(opponent, p2)

        max_hp1 = int(BASE_HP)
        max_hp2 = int(BASE_HP)
        hp1 = int(BASE_HP)
        hp2 = int(BASE_HP)

        msg = await ctx.reply(embed=battle_embed(ctx.author, opponent, hp1, hp2, max_hp1, max_hp2, "Battle started."))

        try:
            winner, turns, _final_hp1, _final_hp2 = simulate(p1, p2, self.fruits)
            delay = float(await self.config.guild(ctx.guild).turn_delay())

            log = []
            for turn in turns:
                side = str(turn[0]) if len(turn) > 0 else "p1"
                dmg = int(turn[1]) if len(turn) > 1 else 0
                hp_after = int(turn[2]) if len(turn) > 2 else 0
                attack = str(turn[3]) if len(turn) > 3 else "Attack"
                crit = bool(turn[4]) if len(turn) > 4 else False

                await asyncio.sleep(max(0.1, delay))

                if side == "p1":
                    hp2 = hp_after
                    actor = ctx.author
                    defender = opponent
                else:
                    hp1 = hp_after
                    actor = opponent
                    defender = ctx.author

                if attack == "Dodged":
                    line = f"{defender.display_name} dodged."
                else:
                    line = f"{actor.display_name} used {attack} for {dmg} damage" + (" (CRIT)" if crit else "") + "."
                log.append(line)

                await msg.edit(
                    embed=battle_embed(
                        ctx.author,
                        opponent,
                        hp1,
                        hp2,
                        max_hp1,
                        max_hp2,
                        "\n".join(log[-6:]) or "—",
                    )
                )

            g = await self.config.guild(ctx.guild).all()

            winner_user = ctx.author if winner == "p1" else opponent
            loser_user = opponent if winner == "p1" else ctx.author
            winner_p = p1 if winner == "p1" else p2
            loser_p = p2 if winner == "p1" else p1

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

            await self.players.save(winner_user, winner_p)
            await self.players.save(loser_user, loser_p)

            # Rewards
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

            # ---------- Updated Result Embed ----------
            def _who(m: discord.Member) -> str:
                # "nickname (username)" style
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

            # Winner summary
            winner_level = int(winner_p.get("level", 1) or 1)
            winner_exp = int(winner_p.get("exp", 0) or 0)

            winner_lines = [
                f"💰 **Beri:** `+{beri_win:,}`" if beri_win else "💰 **Beri:** `+0`",
                f"⭐ **EXP Gained:** `+{win_gain}`",
                f"📈 **Level:** `{winner_level}`" + (f" *(+{leveled_w})*" if leveled_w else ""),
                f"✨ **Current EXP:** `{winner_exp}`",
                f"🏴‍☠️ **Crew Points:** `+{points_added}`" if points_added else "🏴‍☠️ **Crew Points:** `+0`",
            ]
            res.add_field(name=f"🏆 Winner — {winner_who}", value="\n".join(winner_lines), inline=False)

            # Loser rewards (visually smaller/secondary via blockquote + italics)
            loser_level = int(loser_p.get("level", 1) or 1)
            loser_exp = int(loser_p.get("exp", 0) or 0)

            loser_lines = [
                f"💰 Beri: `+{beri_loss:,}`" if beri_loss else "💰 Beri: `+0`",
                f"⭐ EXP Gained: `+{loss_gain}`",
                f"📉 Level: `{loser_level}`" + (f" *(+{leveled_l})*" if leveled_l else ""),
                f"✨ Current EXP: `{loser_exp}`",
            ]
            loser_value = "\n".join(f"> *{line}*" for line in loser_lines)
            res.add_field(name=f"☠️ Loser — {loser_who}", value=loser_value, inline=False)

            res.set_footer(text="⚡ Armament=CRIT • 👁️ Observation=DODGE • 👑 Conqueror=COUNTER CRIT")
            await ctx.reply(embed=res)

        finally:
            self._active_battles.discard(ctx.channel.id)

    @commands.command()
    async def cbleaderboard(self, ctx, metric: str = "wins", limit: int = 10):
        metric = (metric or "wins").lower()
        if metric not in ("wins", "winrate", "level", "exp"):
            return await ctx.reply("Metric must be: wins, winrate, level, exp")

        limit = max(1, min(25, int(limit or 10)))

        raw = await self.players.all()
        entries = []
        for k, v in (raw or {}).items():
            try:
                uid = int(k)
            except Exception:
                continue
            if isinstance(v, dict) and v.get("started"):
                entries.append((uid, v))

        if not entries:
            return await ctx.reply("No player data found.")

        rows = []
        for uid, pdata in entries:
            wins = int(pdata.get("wins", 0) or 0)
            losses = int(pdata.get("losses", 0) or 0)
            total = wins + losses
            winrate = (wins / total * 100) if total else 0.0
            level = int(pdata.get("level", 1) or 1)
            exp = int(pdata.get("exp", 0) or 0)

            if metric == "wins":
                score = wins
                txt = f"{wins} wins"
            elif metric == "winrate":
                score = winrate
                txt = f"{winrate:.1f}% ({wins}/{total})"
            elif metric == "level":
                score = level
                txt = f"Level {level} (EXP {exp})"
            else:
                score = exp
                txt = f"{exp} EXP (Level {level})"

            rows.append((score, uid, txt))

        rows.sort(key=lambda r: r[0], reverse=True)
        top = rows[:limit]

        metric_titles = {
            "wins": "🏆 Most Wins",
            "winrate": "🎯 Best Win Rate",
            "level": "📈 Highest Level",
            "exp": "✨ Most EXP",
        }
        metric_title = metric_titles.get(metric, metric)

        emb = discord.Embed(
            title=f"📊 Crew Battles Leaderboard — {metric_title}",
            color=discord.Color.gold(),
        )

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for idx, (_score, uid, txt) in enumerate(top, start=1):
            medal = medals.get(idx, "🔸")
            lines.append(f"{medal} **#{idx}** <@{uid}> — {txt}")

        emb.description = "\n".join(lines) if lines else "—"
        emb.set_footer(text="Climb the ranks: battle, train haki, and earn wins.")
        await ctx.reply(embed=emb)

    @commands.command(name="cbmaintenance")
    async def cbmaintenance(self, ctx, mode: str = None):
        is_owner = False
        try:
            is_owner = await self.bot.is_owner(ctx.author)
        except Exception:
            is_owner = False

        is_admin = bool(
            ctx.guild
            and getattr(ctx.author, "guild_permissions", None)
            and ctx.author.guild_permissions.administrator
        )
        if not (is_owner or is_admin):
            return await ctx.reply("Owner/admin only.")

        current = bool(await self.config.maintenance())
        if not mode:
            return await ctx.reply(f"Maintenance is {'ON' if current else 'OFF'}.")

        m = mode.lower().strip()
        if m in ("on", "true", "enable"):
            await self.config.maintenance.set(True)
            return await ctx.reply("Maintenance enabled.")
        if m in ("off", "false", "disable"):
            await self.config.maintenance.set(False)
            return await ctx.reply("Maintenance disabled.")
        await ctx.reply("Usage: `.cbmaintenance on|off`")

    # =========================================================
    # FRUIT ADMIN (pool vs shop)
    # =========================================================
    @cbadmin.group(name="fruits", invoke_without_command=True)
    async def cbadmin_fruits(self, ctx: commands.Context):
        """Manage fruit pool (catalog) and shop stock."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @cbadmin_fruits.command(name="pool")
    async def cbadmin_fruits_pool(self, ctx: commands.Context, page: int = 1):
        """List fruits currently in the pool (available to stock)."""
        items = self.fruits.pool_all() or []
        if not items:
            return await ctx.reply("Pool is empty. Import fruits first.")
        page = max(1, int(page or 1))
        per = 15
        start = (page - 1) * per
        chunk = items[start : start + per]
        if not chunk:
            return await ctx.reply("That page is empty.")

        lines = [f"- **{f.get('name','?')}** | {int(f.get('price',0) or 0):,} Beri | +{int(f.get('bonus',0) or 0)} | {str(f.get('type','paramecia')).title()}" for f in chunk]
        e = discord.Embed(title="Fruit Pool (Catalog)", color=discord.Color.blurple(), description="\n".join(lines))
        e.set_footer(text=f"Page {page} • Stock into shop: .cbadmin fruits shopadd <name> <stock>")
        await ctx.reply(embed=e)

    @cbadmin_fruits.command(name="shop")
    async def cbadmin_fruits_shop(self, ctx: commands.Context, page: int = 1):
        """List fruits currently in the shop (stocked)."""
        items = self.fruits.all() or []
        if not items:
            return await ctx.reply("Shop is empty (no stocked fruits).")
        page = max(1, int(page or 1))
        per = 15
        start = (page - 1) * per
        chunk = items[start : start + per]
        if not chunk:
            return await ctx.reply("That page is empty.")

        def _stock_txt(s):
            return "∞" if s is None else str(int(s))

        lines = [
            f"- **{f.get('name','?')}** | {int(f.get('price',0) or 0):,} Beri | +{int(f.get('bonus',0) or 0)} | Stock: {_stock_txt(f.get('stock', None))}"
            for f in chunk
        ]
        e = discord.Embed(title="Fruit Shop (Stocked)", color=discord.Color.gold(), description="\n".join(lines))
        e.set_footer(text=f"Page {page} • Set stock: .cbadmin fruits setstock <name> <number|unlimited>")
        await ctx.reply(embed=e)

    @cbadmin_fruits.command(name="pooladd")
    async def cbadmin_fruits_pooladd(self, ctx: commands.Context, name: str, price: int, bonus: int = 0, ftype: str = "paramecia"):
        """Add or update a fruit in the pool."""
        fruit = {"name": name, "price": int(price), "bonus": int(bonus), "type": str(ftype or "paramecia").lower()}
        try:
            out = self.fruits.pool_upsert(fruit)
        except Exception as e:
            return await ctx.reply(f"Failed to save fruit: {e}")
        await ctx.reply(f"Saved to pool: **{out['name']}** | {out['price']:,} Beri | +{out['bonus']} | {out.get('type','paramecia')}")

    @cbadmin_fruits.command(name="shopadd")
    async def cbadmin_fruits_shopadd(self, ctx: commands.Context, stock: str, *, name: str):
        """
        Add a fruit from the pool into the shop with stock.

        Works with:
          - .cbadmin fruits shopadd 5 Quake Quake
          - .cbadmin fruits shopadd unlimited Quake Quake
          - .cbadmin fruits shopadd Quake Quake 5   (auto-detects)
        """
        # Allow old order: "name ... <stock>" even without quotes
        actual_stock = stock
        actual_name = name

        try:
            st = self._parse_stock_token(actual_stock)
        except Exception:
            # try: last token of name is stock
            parts = (name or "").split()
            if not parts:
                return await ctx.reply("Usage: `.cbadmin fruits shopadd <stock|unlimited> <fruit name>`")
            maybe_stock = parts[-1]
            maybe_name = " ".join([stock] + parts[:-1])
            try:
                st = self._parse_stock_token(maybe_stock)
                actual_stock = maybe_stock
                actual_name = maybe_name
            except Exception:
                return await ctx.reply(
                    "Invalid stock. Use a number or `unlimited`.\n"
                    "Example: `.cbadmin fruits shopadd 5 Quake Quake`"
                )

        try:
            self.fruits.shop_add(actual_name, st)
        except Exception as e:
            return await ctx.reply(f"Failed: {e}")

        await ctx.reply(f"Stocked in shop: **{actual_name}** (stock: {'∞' if st is None else st})")

    @cbadmin_fruits.command(name="setstock")
    async def cbadmin_fruits_setstock(self, ctx: commands.Context, stock: str, *, name: str):
        """
        Set stock for a shop item.

        Works with:
          - .cbadmin fruits setstock 3 Quake Quake
          - .cbadmin fruits setstock unlimited Quake Quake
          - .cbadmin fruits setstock Quake Quake 3   (auto-detects)
        """
        actual_stock = stock
        actual_name = name

        try:
            st = self._parse_stock_token(actual_stock)
        except Exception:
            parts = (name or "").split()
            if not parts:
                return await ctx.reply("Usage: `.cbadmin fruits setstock <stock|unlimited> <fruit name>`")
            maybe_stock = parts[-1]
            maybe_name = " ".join([stock] + parts[:-1])
            try:
                st = self._parse_stock_token(maybe_stock)
                actual_stock = maybe_stock
                actual_name = maybe_name
            except Exception:
                return await ctx.reply(
                    "Invalid stock. Use a number or `unlimited`.\n"
                    "Example: `.cbadmin fruits setstock 3 Quake Quake`"
                )

        try:
            self.fruits.shop_set_stock(actual_name, st)
        except Exception as e:
            return await ctx.reply(f"Failed: {e}")

        await ctx.reply(f"Stock updated: **{actual_name}** => {'∞' if st is None else st}")

    @cbadmin_fruits.command(name="shopremove")
    async def cbadmin_fruits_shopremove(self, ctx: commands.Context, *, name: str):
        """Remove a fruit from the shop (does not delete it from pool)."""
        try:
            self.fruits.shop_remove(name)
        except Exception as e:
            return await ctx.reply(f"Failed: {e}")
        await ctx.reply(f"Removed from shop: **{name}**")

    @cbadmin_fruits.command(name="export")
    async def cbadmin_fruits_export(self, ctx: commands.Context, which: str = "pool"):
        """Export pool or shop to a JSON file in the backups folder."""
        which = (which or "pool").lower().strip()
        if which not in ("pool", "shop"):
            return await ctx.reply("Usage: `.cbadmin fruits export pool|shop`")

        data = {"fruits": self.fruits.pool_all()} if which == "pool" else {"shop": self.fruits.shop_list()}
        fname = f"fruits_{which}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        path = self._backup_dir() / fname

        def _sync_write():
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        await asyncio.to_thread(_sync_write)
        await ctx.reply(f"Exported **{which}** to `{path.name}`")

    @cbadmin_fruits.command(name="import")
    async def cbadmin_fruits_import(self, ctx: commands.Context):
        """
        Import fruits JSON into the POOL (catalog) ONLY.
        Provide a .json attachment containing either:
          - [ {name, price, bonus, type}, ... ]
          - { "fruits": [ ... ] }
          - { "<name>": { ... }, ... }
        """
        if not ctx.message.attachments:
            return await ctx.reply("Attach a `.json` file to import into the pool.")
        att = ctx.message.attachments[0]
        if not (att.filename or "").lower().endswith(".json"):
            return await ctx.reply("Attachment must be a `.json` file.")

        try:
            raw = await att.read()
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return await ctx.reply(f"Could not read JSON: {e}")

        try:
            ok, bad = self.fruits.pool_import(payload)
        except Exception as e:
            return await ctx.reply(f"Import failed: {e}")

        await ctx.reply(f"Imported into pool. Added/updated: **{ok}**, Skipped: **{bad}**")

    # =========================================================
    # Admin test commands (players/haki/fruit/tempban)
    # =========================================================
    @cbadmin.command(name="resetuser")
    async def cbadmin_resetuser(self, ctx: commands.Context, member: discord.Member, confirm: str = None):
        """Completely reset a user's CrewBattles data."""
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin resetuser @member confirm`")

        try:
            await self._write_backup(note=f"pre-resetuser {member.id} by {ctx.author.id}")
        except Exception:
            pass

        await self.config.user(member).set(copy.deepcopy(DEFAULT_USER))
        await ctx.reply(f"✅ Reset CrewBattles data for **{member.display_name}**.")

    @cbadmin.command(name="addhaki")
    async def cbadmin_addhaki(self, ctx: commands.Context, member: discord.Member, haki_type: str, delta: int):
        """Add/remove Haki points (delta can be negative)."""
        haki_type = (haki_type or "").lower().strip()
        if haki_type in ("conquerors", "conq"):
            haki_type = "conqueror"
        if haki_type not in ("armament", "observation", "conqueror"):
            return await ctx.reply("Haki type must be: armament, observation, conqueror")

        p = await self.players.get(member)
        if not p.get("started"):
            return await ctx.reply("That user has not started Crew Battles.")

        haki = p.get("haki", {}) or {}

        # If admin is modifying conqueror, ensure it is unlocked
        if haki_type == "conqueror":
            haki["conquerors"] = True

        key = "conqueror" if haki_type == "conqueror" else haki_type
        cur = int(haki.get(key, 0) or 0)
        haki[key] = max(0, min(100, cur + int(delta)))

        p["haki"] = haki
        await self.players.save(member, p)

        await ctx.reply(f"✅ Updated **{member.display_name}** {haki_type}: `{cur}` → `{haki[key]}`.")