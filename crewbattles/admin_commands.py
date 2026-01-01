import copy
import io
import json
import discord
from datetime import datetime, timezone
from pathlib import Path

from redbot.core import commands
from redbot.core.data_manager import cog_data_path

from .constants import DEFAULT_PRICE_RULES, DEFAULT_USER, MAX_LEVEL
from .utils import exp_to_next


class AdminCommandsMixin:
    # -----------------------------
    # Backups (users)
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

        await self.bot.loop.run_in_executor(None, _sync_write)
        return path

    async def _restore_backup(self, backup_path: Path) -> int:
        def _sync_read():
            with open(backup_path, "r", encoding="utf-8") as f:
                return json.load(f)

        data = await self.bot.loop.run_in_executor(None, _sync_read)
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
    # Helpers
    # -----------------------------
    def _parse_stock_token(self, token: str):
        t = (token or "").strip().lower()
        if t in ("unlimited", "inf", "infinite", "∞", "none"):
            return None
        return int(token)

    def _norm_fruit_type(self, t: str) -> str:
        t = " ".join((t or "").strip().lower().split())
        aliases = {
            "mythical": "mythical zoan",
            "mythic zoan": "mythical zoan",
            "conquerors": "conqueror",
        }
        return aliases.get(t, t)

    def _clamp(self, n: int, lo: int, hi: int) -> int:
        return max(int(lo), min(int(hi), int(n)))

    async def _get_price_rules(self, guild: discord.Guild) -> dict:
        rules = await self.config.guild(guild).price_rules()
        if not isinstance(rules, dict):
            rules = DEFAULT_PRICE_RULES
        # ensure required keys exist
        rules.setdefault("min", DEFAULT_PRICE_RULES["min"])
        rules.setdefault("max", DEFAULT_PRICE_RULES["max"])
        rules.setdefault("base", DEFAULT_PRICE_RULES["base"])
        rules.setdefault("per_bonus", DEFAULT_PRICE_RULES["per_bonus"])
        return rules

    # =========================================================
    # Admin commands
    # =========================================================
    @commands.group(name="cbadmin", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def cbadmin(self, ctx: commands.Context):
        """CrewBattles admin commands."""
        await ctx.send_help()

    @cbadmin.command(name="backup")
    async def cbadmin_backup(self, ctx: commands.Context):
        async with ctx.typing():
            path = await self._write_backup(note=f"manual by {ctx.author} in {ctx.guild}")
        await ctx.reply(f"Backup written: `{path.name}`")

    @cbadmin.command(name="restore")
    async def cbadmin_restore(self, ctx: commands.Context, filename: str = None, confirm: str = None):
        if not filename:
            return await ctx.reply("Usage: `.cbadmin restore <filename> confirm`")
        if confirm != "confirm":
            return await ctx.reply("Add `confirm` to proceed: `.cbadmin restore <filename> confirm`")

        bp = self._backup_dir() / filename
        if not bp.exists():
            return await ctx.reply("That backup file does not exist in the backups folder.")

        async with ctx.typing():
            restored = await self._restore_backup(bp)
        await ctx.reply(f"Restored {restored} user record(s) from `{bp.name}`")

    @cbadmin.command(name="storedcounts")
    async def cbadmin_storedcounts(self, ctx: commands.Context):
        all_users = await self.config.all_users()
        total = len(all_users or {})
        started = sum(1 for _, v in (all_users or {}).items() if isinstance(v, dict) and v.get("started"))
        await ctx.reply(f"Stored user records: {total} | started=True: {started}")

    @cbadmin.command(name="resetall", aliases=["resetstarted", "resetplayers"])
    async def cbadmin_resetall(self, ctx: commands.Context, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin resetall confirm`")

        all_users = await self.config.all_users()
        reset = 0
        async with ctx.typing():
            for uid, pdata in (all_users or {}).items():
                try:
                    uid_int = int(uid)
                except Exception:
                    continue
                if not isinstance(pdata, dict) or not pdata.get("started"):
                    continue
                await self.config.user_from_id(uid_int).set(copy.deepcopy(DEFAULT_USER))
                reset += 1

        await ctx.reply(f"Reset data for {reset} started player(s).")

    @cbadmin.command(name="wipeall", aliases=["wipeusers"])
    async def cbadmin_wipeall(self, ctx: commands.Context, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin wipeall confirm`")

        async with ctx.typing():
            try:
                await self.config.clear_all_users()
            except Exception:
                # fallback: reset known users to defaults
                all_users = await self.config.all_users()
                for uid in (all_users or {}).keys():
                    try:
                        await self.config.user_from_id(int(uid)).set(copy.deepcopy(DEFAULT_USER))
                    except Exception:
                        pass

        await ctx.reply("HARD WIPE complete.")

    @cbadmin.command(name="setberi")
    async def cbadmin_setberi(self, ctx: commands.Context, win: int, loss: int = 0):
        await self.config.guild(ctx.guild).beri_win.set(int(win))
        await self.config.guild(ctx.guild).beri_loss.set(int(loss))
        await ctx.reply(f"Beri rewards updated. Win={int(win)} Loss={int(loss)}")

    @cbadmin.command(name="setturn_delay")
    async def cbadmin_setturn_delay(self, ctx: commands.Context, delay: float):
        await self.config.guild(ctx.guild).turn_delay.set(float(delay))
        await ctx.reply(f"Turn delay set to {float(delay)}s")

    @cbadmin.command(name="sethakicost")
    async def cbadmin_sethakicost(self, ctx: commands.Context, cost: int):
        await self.config.guild(ctx.guild).haki_cost.set(int(cost))
        await ctx.reply(f"Haki training cost set to {int(cost)} per point")

    @cbadmin.command(name="sethakicooldown")
    async def cbadmin_sethakicooldown(self, ctx: commands.Context, seconds: int):
        await self.config.guild(ctx.guild).haki_cooldown.set(int(seconds))
        await ctx.reply(f"Haki training cooldown set to {int(seconds)} seconds")

    @cbadmin.command(name="setcrewpointswin", aliases=["setcrewwinpoints", "setcrewpoints"])
    async def cbadmin_setcrewpointswin(self, ctx: commands.Context, points: int):
        points = int(points)
        if points < 0:
            return await ctx.reply("Points must be >= 0.")
        await self.config.guild(ctx.guild).crew_points_win.set(points)
        await ctx.reply(f"Crew points per win set to {points} (0 disables).")

    @cbadmin.command(name="setexpwin")
    async def cbadmin_setexpwin(self, ctx: commands.Context, min_exp: int, max_exp: int = None):
        if max_exp is None:
            max_exp = min_exp
        if min_exp < 0 or max_exp < 0 or max_exp < min_exp:
            return await ctx.reply("Invalid range.")
        await self.config.guild(ctx.guild).exp_win_min.set(int(min_exp))
        await self.config.guild(ctx.guild).exp_win_max.set(int(max_exp))
        await ctx.reply(f"Winner EXP set to {min_exp}–{max_exp} per win.")

    @cbadmin.command(name="setexploss")
    async def cbadmin_setexploss(self, ctx: commands.Context, min_exp: int, max_exp: int = None):
        if max_exp is None:
            max_exp = min_exp
        if min_exp < 0 or max_exp < 0 or max_exp < min_exp:
            return await ctx.reply("Invalid range.")
        await self.config.guild(ctx.guild).exp_loss_min.set(int(min_exp))
        await self.config.guild(ctx.guild).exp_loss_max.set(int(max_exp))
        await ctx.reply(f"Loser EXP set to {min_exp}–{max_exp} per loss.")

    @cbadmin.command(name="fixlevels", aliases=["recalclevels", "recalcexp"])
    async def cbadmin_fixlevels(self, ctx: commands.Context):
        all_users = await self.config.all_users()
        total = 0
        changed = 0

        def recalc_level(level: int, exp: int):
            lvl = max(1, int(level or 1))
            xp = max(0, int(exp or 0))
            ups = 0
            while lvl < MAX_LEVEL:
                need = exp_to_next(lvl)
                if need <= 0:
                    break
                if xp >= need:
                    xp -= need
                    lvl += 1
                    ups += 1
                else:
                    break
            if lvl >= MAX_LEVEL:
                lvl = MAX_LEVEL
                xp = 0
            return lvl, xp, ups

        async with ctx.typing():
            for uid, pdata in (all_users or {}).items():
                try:
                    uid_int = int(uid)
                except Exception:
                    continue
                if not isinstance(pdata, dict):
                    continue

                total += 1
                old_lvl = int(pdata.get("level", 1) or 1)
                old_xp = int(pdata.get("exp", 0) or 0)
                new_lvl, new_xp, _ = recalc_level(old_lvl, old_xp)

                if new_lvl != old_lvl or new_xp != old_xp:
                    pdata["level"] = new_lvl
                    pdata["exp"] = new_xp
                    await self.config.user_from_id(uid_int).set(pdata)
                    changed += 1

        await ctx.reply(f"Recalculated levels. Updated {changed} / {total} records.")

    @cbadmin.command(name="setconquerorcost", aliases=["setconqcost", "setconquerorscost"])
    async def cbadmin_setconquerorcost(self, ctx: commands.Context, cost: int):
        cost = int(cost)
        if cost < 0:
            return await ctx.reply("Cost must be >= 0.")
        await self.config.guild(ctx.guild).conqueror_unlock_cost.set(cost)
        await ctx.reply(f"Conqueror unlock cost set to {cost:,} Beri.")

    @cbadmin.command(name="resetuser")
    async def cbadmin_resetuser(self, ctx: commands.Context, member: discord.Member, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin resetuser @member confirm`")
        async with ctx.typing():
            await self.config.user(member).set(copy.deepcopy(DEFAULT_USER))
        await ctx.reply(f"✅ Reset Crew Battles data for **{member.display_name}**.")

    @cbadmin.command(name="sethaki")
    async def cbadmin_sethaki(self, ctx: commands.Context, member: discord.Member, haki_type: str, level: int):
        """
        Set a user's Haki level.
        Usage: .cbadmin sethaki @user armament|observation|conqueror <level>
        """
        haki_type = (haki_type or "").lower().strip()
        if haki_type in ("conq", "conquerors"):
            haki_type = "conqueror"
        if haki_type not in ("armament", "observation", "conqueror"):
            return await ctx.reply("Type must be: `armament`, `observation`, or `conqueror`.")

        level = int(level)
        if level < 0:
            level = 0
        if level > 100:
            level = 100

        # Prefer PlayerManager if your cog has it; otherwise fall back to Config user dict
        if hasattr(self, "players") and getattr(self.players, "get", None) and getattr(self.players, "save", None):
            pdata = await self.players.get(member)
            haki = pdata.get("haki", {}) or {}
            haki[haki_type] = level
            if haki_type == "conqueror" and level > 0:
                haki["conquerors"] = True
            pdata["haki"] = haki
            await self.players.save(member, pdata)
        else:
            pdata = await self.config.user(member).all()
            haki = pdata.get("haki", {}) or {}
            haki[haki_type] = level
            if haki_type == "conqueror" and level > 0:
                haki["conquerors"] = True
            pdata["haki"] = haki
            await self.config.user(member).set(pdata)

        extra = ""
        if haki_type == "conqueror" and level > 0:
            extra = " (unlock enabled)"
        await ctx.reply(f"✅ Set **{member.display_name}** {haki_type} to `{level}`{extra}.")

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
        raw = await att.read()
        payload = json.loads(raw.decode("utf-8"))

        ok, bad = self.fruits.pool_import(payload)
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

    @cbadmin_fruits.command(name="pooladd")
    async def cbadmin_fruits_pooladd(
        self,
        ctx: commands.Context,
        name: str,
        ftype: str,
        bonus: int,
        price: int,
        *,
        ability: str = "",
    ):
        """Add/update a fruit in the POOL."""
        fruit = {
            "name": name,
            "type": ftype,
            "bonus": int(bonus),
            "price": int(price),
            "ability": (ability or "").strip(),
        }
        saved = self.fruits.pool_upsert(fruit)
        await ctx.reply(
            f"✅ Pool updated: **{saved['name']}** ({str(saved.get('type','?')).title()}) "
            f"`+{int(saved.get('bonus',0) or 0)}` | `{int(saved.get('price',0) or 0):,}` | "
            f"Ability: **{saved.get('ability') or 'None'}**"
        )

    @cbadmin_fruits.command(name="shopadd")
    async def cbadmin_fruits_shopadd(self, ctx: commands.Context, stock: str, *, name: str):
        """Stock a fruit (must exist in pool)."""
        st = self._parse_stock_token(stock)
        self.fruits.shop_add(name, st)
        await ctx.reply(f"✅ Stocked: **{name}** (stock: {'∞' if st is None else st})")

    @cbadmin_fruits.command(name="setstock")
    async def cbadmin_fruits_setstock(self, ctx: commands.Context, stock: str, *, name: str):
        """Set stock for an existing shop item."""
        st = self._parse_stock_token(stock)
        self.fruits.shop_set_stock(name, st)
        await ctx.reply(f"✅ Stock updated: **{name}** => {'∞' if st is None else st}")

    @cbadmin_fruits.command(name="shopremove")
    async def cbadmin_fruits_shopremove(self, ctx: commands.Context, *, name: str):
        """Remove a fruit from the shop (does not delete it from pool)."""
        self.fruits.shop_remove(name)
        await ctx.reply(f"✅ Removed from shop: **{name}**")

    @cbadmin_fruits.command(name="setbase")
    async def cbadmin_fruits_pricing_setbase(self, ctx: commands.Context, fruit_type: str, amount: int):
        rules = await self._get_price_rules(ctx.guild)
        t = self._norm_fruit_type(fruit_type)
        rules["base"][t] = int(amount)
        await self.config.guild(ctx.guild).price_rules.set(rules)
        await ctx.reply(f"✅ Base price for `{t}` set to `{int(amount):,}`")

    @cbadmin_fruits.command(name="setperbonus", aliases=["setper"])
    async def cbadmin_fruits_pricing_setperbonus(self, ctx: commands.Context, fruit_type: str, amount: int):
        rules = await self._get_price_rules(ctx.guild)
        t = self._norm_fruit_type(fruit_type)
        rules["per_bonus"][t] = int(amount)
        await self.config.guild(ctx.guild).price_rules.set(rules)
        await ctx.reply(f"✅ Per-bonus price for `{t}` set to `{int(amount):,}` per +1 bonus")

    @cbadmin_fruits.command(name="setbounds")
    async def cbadmin_fruits_pricing_setbounds(self, ctx: commands.Context, min_price: int, max_price: int):
        if max_price < min_price:
            return await ctx.reply("Max must be >= min.")
        rules = await self._get_price_rules(ctx.guild)
        rules["min"] = int(min_price)
        rules["max"] = int(max_price)
        await self.config.guild(ctx.guild).price_rules.set(rules)
        await ctx.reply(f"✅ Bounds set: min=`{int(min_price):,}` max=`{int(max_price):,}`")

    @cbadmin_fruits.command(name="reprice")
    async def cbadmin_fruits_reprice(self, ctx: commands.Context, force: str = None):
        """
        Recompute prices for ALL pool fruits using current pricing rules.
        Skips fruits with price_locked=True unless `force` is provided.

        Usage:
          .cbadmin fruits reprice
          .cbadmin fruits reprice force
        """
        rules = await self._get_price_rules(ctx.guild)
        lo = int(rules["min"])
        hi = int(rules["max"])
        base = rules.get("base", {}) or {}
        perb = rules.get("per_bonus", {}) or {}
        do_force = (force or "").strip().lower() == "force"

        pool = self.fruits.pool_all() or []
        if not pool:
            return await ctx.reply("Pool is empty.")

        changed = 0
        skipped_locked = 0
        async with ctx.typing():
            for f in pool:
                if not isinstance(f, dict):
                    continue
                if not do_force and bool(f.get("price_locked")):
                    skipped_locked += 1
                    continue

                t = self._norm_fruit_type(f.get("type", "paramecia"))
                b = int(base.get(t, base.get("paramecia", DEFAULT_PRICE_RULES["base"]["paramecia"])))
                p = int(perb.get(t, perb.get("paramecia", DEFAULT_PRICE_RULES["per_bonus"]["paramecia"])))
                bonus = int(f.get("bonus", 0) or 0)
                new_price = self._clamp(b + (bonus * p), lo, hi)

                old_price = int(f.get("price", 0) or 0)
                if old_price != new_price:
                    f["price"] = new_price
                    # repriced values are not "manual"
                    if "price_locked" in f:
                        f["price_locked"] = False
                    self.fruits.pool_upsert(f)
                    changed += 1

        await ctx.reply(f"✅ Repriced pool. Updated `{changed}` fruit(s). Skipped locked: `{skipped_locked}`.")

    @cbadmin_fruits.command(name="setprice", aliases=["price"])
    async def cbadmin_fruits_setprice(self, ctx: commands.Context, price: int, *, name: str):
        """Set a single fruit price and lock it against automatic repricing."""
        price = int(price)
        if price < 0:
            return await ctx.reply("Price must be >= 0.")

        f = self.fruits.pool_get(name)
        if not isinstance(f, dict):
            return await ctx.reply("That fruit is not in the pool. Add/import it first.")

        old = int(f.get("price", 0) or 0)
        f["price"] = price
        f["price_locked"] = True
        saved = self.fruits.pool_upsert(f)
        await ctx.reply(f"✅ Price updated (locked): **{saved['name']}** `{old:,}` → `{price:,}`")

    @cbadmin_fruits.command(name="unlockprice", aliases=["pricereset"])
    async def cbadmin_fruits_unlockprice(self, ctx: commands.Context, *, name: str):
        """Allow a fruit to be affected by `.cbadmin fruits reprice` again."""
        f = self.fruits.pool_get(name)
        if not isinstance(f, dict):
            return await ctx.reply("That fruit is not in the pool.")
        f["price_locked"] = False
        self.fruits.pool_upsert(f)
        await ctx.reply(f"✅ Unlocked price for **{f.get('name', name)}**")

    @cbadmin.command(name="maintenance")
    async def cbadmin_maintenance(self, ctx: commands.Context, mode: str):
        """
        Toggle maintenance mode for this cog.
        Usage: .cbadmin maintenance on|off
        When ON: only admins can use commands from this cog (requires cog_check enforcement).
        """
        mode = (mode or "").strip().lower()
        if mode in ("on", "true", "1", "enable", "enabled", "yes", "y"):
            state = True
        elif mode in ("off", "false", "0", "disable", "disabled", "no", "n"):
            state = False
        else:
            return await ctx.reply("Usage: `.cbadmin maintenance on|off`")

        # Prefer a registered Config key if it exists; otherwise fall back to raw.
        try:
            await self.config.maintenance.set(state)  # type: ignore[attr-defined]
        except Exception:
            await self.config.set_raw("maintenance", value=state)

        await ctx.reply(f"✅ Maintenance mode is now **{'ON' if state else 'OFF'}**.")

    @cbadmin_fruits.command(name="addall")
    async def cbadmin_fruits_addall(self, ctx: commands.Context, stock: str = "1"):
        """
        Add ALL fruits from an attached fruits.json into the SHOP at once.
        Also upserts them into the POOL first.

        Usage:
          .cbadmin fruits addall              (default stock=1)
          .cbadmin fruits addall 5            (stock=5 for every fruit)
          .cbadmin fruits addall unlimited    (unlimited stock)
        (Attach fruits.json)
        """
        if not ctx.message.attachments:
            return await ctx.reply("Attach `fruits.json`: `.cbadmin fruits addall [stock]` with the file attached.")

        try:
            st = self._parse_stock_token(stock)  # None = unlimited
        except Exception:
            return await ctx.reply("Invalid stock. Use a number or `unlimited`.")

        att = ctx.message.attachments[0]
        try:
            raw = await att.read()
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return await ctx.reply(f"Failed to read JSON attachment: {e}")

        fruits_list = (payload or {}).get("fruits")
        if not isinstance(fruits_list, list) or not fruits_list:
            return await ctx.reply("JSON must look like: `{ \"fruits\": [ {..}, {..} ] }`")

        # 1) Upsert into pool
        try:
            ok_pool, bad_pool = self.fruits.pool_import(payload)
        except Exception as e:
            return await ctx.reply(f"Pool import failed: {e}")

        # 2) Stock all into shop
        ok_shop = 0
        bad_shop = 0
        for item in fruits_list:
            if not isinstance(item, dict):
                bad_shop += 1
                continue
            name = (item.get("name") or "").strip()
            if not name:
                bad_shop += 1
                continue
            try:
                self.fruits.shop_add(name, st)
                ok_shop += 1
            except Exception:
                bad_shop += 1

        stock_txt = "∞" if st is None else str(st)
        await ctx.reply(
            f"✅ addall complete.\n"
            f"POOL: {ok_pool} OK, {bad_pool} failed\n"
            f"SHOP: {ok_shop} stocked (stock={stock_txt}), {bad_shop} failed"
        )

    @cbadmin.command(name="removefruit")
    async def cbadmin_removefruit(self, ctx: commands.Context, member: discord.Member, confirm: str = None):
        """Remove a user's equipped fruit. Usage: .cbadmin removefruit @user confirm"""
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin removefruit @user confirm`")

        # Prefer PlayerManager if available
        if hasattr(self, "players") and getattr(self.players, "get", None) and getattr(self.players, "save", None):
            pdata = await self.players.get(member)
            old = pdata.get("fruit")
            if not old:
                return await ctx.reply("That user has no fruit equipped.")
            pdata["fruit"] = None
            await self.players.save(member, pdata)
        else:
            pdata = await self.config.user(member).all()
            old = pdata.get("fruit")
            if not old:
                return await ctx.reply("That user has no fruit equipped.")
            pdata["fruit"] = None
            await self.config.user(member).set(pdata)

        await ctx.reply(f"✅ Removed **{member.display_name}**'s fruit: **{old}**")