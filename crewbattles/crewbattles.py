import asyncio
import copy
import io
import json
import random
import re
import time
import inspect 
from datetime import datetime, timezone
from pathlib import Path

import discord
from redbot.core import commands, Config
from redbot.core.data_manager import cog_data_path

from .constants import BASE_HP, DEFAULT_USER, DEFAULT_PRICE_RULES, MAX_LEVEL
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .embeds import battle_embed
from .utils import exp_to_next
from .admin_commands import AdminCommandsMixin
from .player_commands import PlayerCommandsMixin  # already present in your file

HAKI_TRAIN_COST = 500
HAKI_TRAIN_COOLDOWN = 60 * 60

DEFAULT_BATTLE_COOLDOWN = 60
MIN_BATTLE_COOLDOWN = 10
MAX_BATTLE_COOLDOWN = 3600


class CrewBattles(AdminCommandsMixin, PlayerCommandsMixin, commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        self.config.register_user(**DEFAULT_USER)

        self.config.register_guild(
            maintenance=False,
            beri_win=0,
            beri_loss=0,
            turn_delay=1.0,
            haki_cost=HAKI_TRAIN_COST,
            haki_cooldown=HAKI_TRAIN_COOLDOWN,
            crew_points_win=1,
            exp_win_min=0,
            exp_win_max=0,
            exp_loss_min=0,
            exp_loss_max=0,
            price_rules=DEFAULT_PRICE_RULES,
        )
        # ...existing code...

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

    def _parse_stock_token(self, token: str) -> int | None:
        """Parse stock token: a number or 'unlimited' -> int or None."""
        token = (token or "").strip().lower()
        if token in ("unlimited", "infinite", "inf", "∞"):
            return None
        try:
            return max(0, int(token))
        except Exception:
            raise ValueError(f"Invalid stock token: {token}")

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

    # --- REMOVE/disable legacy cbtutorial command (decorator makes it override the mixin)
    # @commands.command(name="cbtutorial", aliases=["cbguide", "cbhelp"])
    async def _legacy_cbtutorial(self, ctx: commands.Context):

        return await ctx.send("Legacy tutorial is disabled; use the mixin `.cbtutorial`.")
