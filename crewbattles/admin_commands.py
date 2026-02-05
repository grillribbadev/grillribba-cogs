import io
import json
import copy
import discord
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from redbot.core import commands
from redbot.core.data_manager import cog_data_path

from .constants import DEFAULT_PRICE_RULES, DEFAULT_USER, MAX_LEVEL
from .utils import exp_to_next


class AdminCommandsMixin:
            @cbadmin.command(name="setberilogchannel", aliases=["setberilog", "setberichan"])
            async def cbadmin_setberilogchannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
                """Set the channel where beri gain/spend logs will be sent. Use no argument to clear."""
                if channel is None:
                    await self.config.guild(ctx.guild).beri_log_channel.clear()
                    await ctx.send("✅ Beri log channel cleared.")
                    return
                await self.config.guild(ctx.guild).beri_log_channel.set(channel.id)
                await ctx.send(f"✅ Beri log channel set to {channel.mention}")
        @cbadmin.command(name="berilogs", aliases=["berilog", "berihistory"])
        async def cbadmin_berilogs(self, ctx: commands.Context, user: discord.User):
            """Retrieve and display beri transaction logs for a user, categorized by spent/gained."""
            import discord
            beri_cog = ctx.bot.get_cog("BeriCore")
            if not beri_cog:
                await ctx.send("❌ BeriCore cog not found. Make sure it's loaded.")
                return

            # Try to get transaction logs from BeriCore
            try:
                # Attempt to retrieve logs from BeriCore's config
                if hasattr(beri_cog, "config"):
                    user_logs = await beri_cog.config.user(user).ledger()
                else:
                    await ctx.send("❌ Could not access BeriCore logs.")
                    return

                if not user_logs:
                    await ctx.send(f"📊 No beri transaction logs found for {user.mention}.")
                    return

                # Categorize logs
                gained = []
                spent = []

                for log_entry in user_logs:
                    if isinstance(log_entry, dict):
                        amount = log_entry.get("amount", 0)
                        reason = log_entry.get("reason", "Unknown")
                        timestamp = log_entry.get("timestamp", "Unknown")
                    else:
                        continue

                    if amount > 0:
                        gained.append((amount, reason, timestamp))
                    elif amount < 0:
                        spent.append((abs(amount), reason, timestamp))

                # Build embed
                embed = discord.Embed(
                    title=f"💰 Beri Logs for {user.display_name}",
                    color=discord.Color.gold(),
                )

                # Gained section
                if gained:
                    gained_text = "\n".join(
                        f"• **+{amt:,}** beri - {reason}"
                        for amt, reason, _ in gained[:10]  # Show last 10
                    )
                    embed.add_field(
                        name=f"📈 Gained ({len(gained)} total)",
                        value=gained_text or "No entries",
                        inline=False,
                    )
                    total_gained = sum(amt for amt, _, _ in gained)
                    embed.add_field(
                        name="Total Gained",
                        value=f"**{total_gained:,}** beri",
                        inline=True,
                    )

                # Spent section
                if spent:
                    spent_text = "\n".join(
                        f"• **-{amt:,}** beri - {reason}"
                        for amt, reason, _ in spent[:10]  # Show last 10
                    )
                    embed.add_field(
                        name=f"📉 Spent ({len(spent)} total)",
                        value=spent_text or "No entries",
                        inline=False,
                    )
                    total_spent = sum(amt for amt, _, _ in spent)
                    embed.add_field(
                        name="Total Spent",
                        value=f"**{total_spent:,}** beri",
                        inline=True,
                    )

                # Net balance
                net = sum(amt for amt, _, _ in gained) - sum(amt for amt, _, _ in spent)
                embed.add_field(
                    name="Net Change",
                    value=f"**{net:+,}** beri",
                    inline=False,
                )

                await ctx.send(embed=embed)

            except Exception as e:
                await ctx.send(
                    f"❌ Error retrieving logs: {str(e)}\n"
                    f"(BeriCore may not store transaction history in the expected format)"
                )
    # -----------------------------
    # Backups (users)
    # -----------------------------
    def _backup_dir(self) -> Path:
        d = cog_data_path(self) / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _source_dir(self) -> Path:
        return Path(__file__).resolve().parent

    def _list_files_under(self, root: Path, *, max_items: int = 60) -> list[str]:
        root = Path(root)
        if not root.exists():
            return []

        out: list[str] = []
        try:
            for p in sorted(root.rglob("*")):
                if len(out) >= max_items:
                    break
                if not p.is_file():
                    continue
                parts = {x.lower() for x in p.parts}
                if "__pycache__" in parts:
                    continue
                if p.suffix.lower() in (".pyc", ".pyo"):
                    continue
                try:
                    rel = str(p.relative_to(root)).replace("\\", "/")
                except Exception:
                    rel = p.name
                out.append(rel)
        except Exception:
            return []
        return out

    def _resolve_scoped_path(self, scope: str, rel_path: str, guild: discord.Guild | None = None) -> Path | None:
        scope = (scope or "").strip().lower()
        rel_path = (rel_path or "").strip().lstrip("/")
        if not scope or not rel_path:
            return None

        roots: dict[str, Path] = {
            "source": self._source_dir(),
            "data": cog_data_path(self),
            "storage": cog_data_path(self),
            "backups": self._backup_dir(),
            "backup": self._backup_dir(),
        }
        root = roots.get(scope)
        if root is None:
            return None

        root = root.resolve()
        try:
            candidate = (root / rel_path).resolve()
        except Exception:
            return None

        if candidate == root or root not in candidate.parents:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    def _guild_backup_dir(self, guild: discord.Guild) -> Path:
        gname = self._safe_slug(str(getattr(guild, "name", "") or ""), limit=30) or "guild"
        try:
            hid = hashlib.sha1(str(int(getattr(guild, "id", 0) or 0)).encode("utf-8")).hexdigest()[:6]
        except Exception:
            hid = "000000"
        d = self._backup_dir() / f"guild_{gname}_{hid}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_slug(self, s: str, *, limit: int = 40) -> str:
        s = " ".join((s or "").strip().split())
        if not s:
            return ""
        out = []
        for ch in s:
            if ch.isalnum():
                out.append(ch.lower())
            elif ch in (" ", "-", "_"):
                out.append("_")
        slug = "".join(out)
        while "__" in slug:
            slug = slug.replace("__", "_")
        slug = slug.strip("_")
        return slug[:limit]

    def _note_slug_base(self, note: str) -> str:
        """Derive a short label for filenames from a longer meta note."""
        note = " ".join((note or "").strip().split())
        if not note:
            return ""
        low = note.lower()
        if low.startswith("periodic"):
            return "periodic"
        if low.startswith("manual by") and ":" in note:
            tail = note.split(":", 1)[1].strip()
            return tail or "manual"
        return note

    def _resolve_backup_path(self, filename: str, guild: discord.Guild) -> Path | None:
        if not filename:
            return None
        root = self._backup_dir().resolve()

        # Allow relative paths under backup root (e.g. guild_123/foo.json)
        try:
            candidate = (root / filename).resolve()
            if candidate != root and root in candidate.parents and candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            pass

        gp = self._guild_backup_dir(guild) / filename
        if gp.exists() and gp.is_file():
            return gp
        rp = self._backup_dir() / filename
        if rp.exists() and rp.is_file():
            return rp
        return None

    async def _write_backup(self, *, note: str = "", guild: discord.Guild = None) -> Path:
        """Write a backup of member-scoped data for this guild."""
        if guild is None:
            raise ValueError("Guild required for backup")

        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        note = " ".join((note or "").strip().split())

        members = await self.config.all_members(guild)
        payload = {
            "meta": {
                "cog": "crewbattles",
                "ts": datetime.now(timezone.utc).isoformat(),
                "note": note,
                "scope": "guild",
                "guild_id": int(guild.id),
                "guild_name": str(getattr(guild, "name", "")),
                "count": len(members or {}),
            },
            "members": members or {},
        }
        fname = f"users_{ts}.json"
        path = self._guild_backup_dir(guild) / fname

        def _sync_write():
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        await self.bot.loop.run_in_executor(None, _sync_write)
        return path

    async def _restore_backup(self, backup_path: Path, guild: discord.Guild = None) -> int:
        if guild is None:
            raise ValueError("Guild required for restore")

        def _sync_read():
            with open(backup_path, "r", encoding="utf-8") as f:
                return json.load(f)

        data = await self.bot.loop.run_in_executor(None, _sync_read)
        members = (data or {}).get("members")
        guilds_map = (data or {}).get("guilds")
        legacy_users = (data or {}).get("users") or (data or {}).get("users_legacy")

        if isinstance(members, dict):
            bucket = members
        elif isinstance(guilds_map, dict):
            bucket = guilds_map.get(str(int(guild.id)), {})
        elif isinstance(legacy_users, dict):
            # legacy backups (global) restored into THIS guild
            bucket = legacy_users
        else:
            raise ValueError("Backup file format invalid")

        restored = 0
        for uid, pdata in bucket.items():
            try:
                uid_int = int(uid)
            except Exception:
                continue
            if not isinstance(pdata, dict):
                continue
            await self.config.member_from_ids(guild.id, uid_int).set(pdata)
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
        # Backward-compatible: if not registered yet, fall back to raw/defaults
        try:
            rules = await self.config.guild(guild).price_rules()
        except AttributeError:
            rules = await self.config.guild(guild).get_raw("price_rules", default=DEFAULT_PRICE_RULES)

        if not isinstance(rules, dict):
            rules = DEFAULT_PRICE_RULES

        rules.setdefault("min", DEFAULT_PRICE_RULES["min"])
        rules.setdefault("max", DEFAULT_PRICE_RULES["max"])
        rules.setdefault("base", DEFAULT_PRICE_RULES["base"])
        rules.setdefault("per_bonus", DEFAULT_PRICE_RULES["per_bonus"])
        return rules

    def _compute_price_from_rules(self, fruit: dict, rules: dict) -> int:
        t = self._norm_fruit_type(fruit.get("type", "paramecia"))
        bonus = int(fruit.get("bonus", 0) or 0)

        lo = int(rules.get("min", DEFAULT_PRICE_RULES["min"]))
        hi = int(rules.get("max", DEFAULT_PRICE_RULES["max"]))

        base_map = (rules.get("base") or DEFAULT_PRICE_RULES["base"])
        per_map = (rules.get("per_bonus") or DEFAULT_PRICE_RULES["per_bonus"])

        base = int(base_map.get(t, base_map.get("paramecia", DEFAULT_PRICE_RULES["base"]["paramecia"])))
        perb = int(per_map.get(t, per_map.get("paramecia", DEFAULT_PRICE_RULES["per_bonus"]["paramecia"])))

        return self._clamp(base + (bonus * perb), lo, hi)

    # =========================================================
    # Admin commands
    # =========================================================

    @commands.group(name="cbadmin", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def cbadmin(self, ctx: commands.Context):
        """CrewBattles admin commands."""
        await ctx.send_help()

    @cbadmin.command(name="backup")
    async def cbadmin_backup(self, ctx: commands.Context, *, note: str = ""):
        note = " ".join((note or "").strip().split())
        note_line = f"manual by {ctx.author.id}" + (f": {note}" if note else "")
        async with ctx.typing():
            path = await self._write_backup(note=note_line, guild=ctx.guild)
        rel = None
        try:
            rel = str(path.relative_to(self._backup_dir()))
        except Exception:
            rel = path.name
        await ctx.reply(f"Backup written: `{rel}`")

    @cbadmin.command(name="restore")
    async def cbadmin_restore(self, ctx: commands.Context, filename: str = None, confirm: str = None):
        if not filename:
            root = self._backup_dir()
            gdir = self._guild_backup_dir(ctx.guild)
            paths = [p for p in (list(root.glob("*.json")) + list(gdir.glob("*.json"))) if p.exists() and p.is_file()]
            if not paths:
                return await ctx.reply("No backup files found.")

            def key(p: Path):
                try:
                    return p.stat().st_mtime
                except Exception:
                    return 0

            paths = sorted(paths, key=key, reverse=True)[:10]

            def display_note(note: str) -> str:
                note = " ".join((note or "").strip().split())
                if not note:
                    return "—"
                low = note.lower()
                if low.startswith("manual by") and ":" in note:
                    tail = note.split(":", 1)[1].strip()
                    return tail or note
                return note

            def read_meta(p: Path) -> dict:
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    meta = (data or {}).get("meta")
                    return meta if isinstance(meta, dict) else {}
                except Exception:
                    return {}

            metas = await self.bot.loop.run_in_executor(None, lambda: [read_meta(p) for p in paths])

            e = discord.Embed(
                title="CrewBattles Backups (latest 10)",
                description="Use `.cbadmin restore <path> confirm` to restore.",
                color=discord.Color.blurple(),
            )
            for i, (p, meta) in enumerate(zip(paths, metas), start=1):
                try:
                    rel = str(p.relative_to(root)).replace("\\", "/")
                except Exception:
                    rel = p.name

                ts = str((meta or {}).get("ts") or "")
                note = display_note(str((meta or {}).get("note") or ""))
                if len(note) > 180:
                    note = note[:177] + "..."

                value = "\n".join(
                    [
                        f"**File:** `{rel}`",
                        f"**Time (UTC):** `{ts or '—'}`",
                        f"**Description:** {note}",
                    ]
                )
                e.add_field(name=f"#{i}", value=value, inline=False)

            return await ctx.reply(embed=e)
        if confirm != "confirm":
            return await ctx.reply("Add `confirm` to proceed: `.cbadmin restore <filename> confirm`")

        bp = self._resolve_backup_path(filename, ctx.guild)
        if not bp:
            return await ctx.reply("That backup file does not exist in the backups folder.")

        async with ctx.typing():
            restored = await self._restore_backup(bp, guild=ctx.guild)
        await ctx.reply(f"Restored {restored} member record(s) for this server from `{bp.name}`")

    @cbadmin.command(name="files", aliases=["documents", "docs"])
    async def cbadmin_files(self, ctx: commands.Context, scope: str = ""):
        """List readable files bundled with this cog (source) and stored data/backups (data)."""
        scope = (scope or "").strip().lower()
        if scope and scope not in ("source", "data", "storage", "backups", "backup"):
            return await ctx.reply("Scopes: `source`, `data` (aka `storage`), `backups`. Example: `.cbadmin files data`")

        blocks: list[str] = []

        def add_block(title: str, root: Path, max_items: int = 40):
            items = self._list_files_under(root, max_items=max_items)
            if not items:
                return
            lines = [f"**{title}:**"]
            for x in items:
                lines.append(f"- {x}")
            blocks.append("\n".join(lines))

        if not scope or scope == "source":
            add_block("source (use: .cbadmin getfile source <path>)", self._source_dir())
        if not scope or scope in ("data", "storage"):
            add_block("data/storage (use: .cbadmin getfile data <path>)", cog_data_path(self))
        if not scope or scope in ("backups", "backup"):
            add_block("backups (use: .cbadmin getfile backups <path>)", self._backup_dir())

        if not blocks:
            return await ctx.reply("No files found for that scope.")

        msg = "\n\n".join(blocks)
        if len(msg) > 1800:
            msg = msg[:1800] + "\n…(truncated)"
        await ctx.reply(msg)

    @cbadmin.command(name="getfile", aliases=["file", "pullfile"])
    async def cbadmin_getfile(self, ctx: commands.Context, scope: str, *, path: str):
        """Send a file from this cog as an attachment for reading/safekeeping."""
        scope = (scope or "").strip().lower()
        path = (path or "").strip()
        if not scope or not path:
            return await ctx.reply("Usage: `.cbadmin getfile <source|data|backups> <path>`")

        # Special-case backups: allow passing just a filename (we'll search guild/root)
        if scope in ("backup", "backups") and "/" not in path and "\\" not in path:
            bp = self._resolve_backup_path(path, ctx.guild)
            if bp:
                file_path = bp
            else:
                file_path = None
        else:
            file_path = self._resolve_scoped_path(scope, path, guild=ctx.guild)

        if not file_path:
            return await ctx.reply("File not found (or path not allowed). Use `.cbadmin files` to browse.")

        try:
            size = file_path.stat().st_size
        except Exception:
            size = 0

        if size and size > 7_500_000:
            return await ctx.reply(f"That file is too large to send here ({size:,} bytes).")

        try:
            await ctx.reply(file=discord.File(str(file_path), filename=file_path.name))
        except Exception as e:
            await ctx.reply(f"Could not send file: {e}")

    @cbadmin.command(name="storedcounts")
    async def cbadmin_storedcounts(self, ctx: commands.Context):
        all_users = await self.config.all_members(ctx.guild)
        total = len(all_users or {})
        started = sum(1 for _, v in (all_users or {}).items() if isinstance(v, dict) and v.get("started"))
        await ctx.reply(f"Stored member records (this server): {total} | started=True: {started}")

    @cbadmin.command(name="resetall", aliases=["resetstarted", "resetplayers"])
    async def cbadmin_resetall(self, ctx: commands.Context, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin resetall confirm`")

        all_users = await self.config.all_members(ctx.guild)
        reset = 0
        async with ctx.typing():
            for uid, pdata in (all_users or {}).items():
                try:
                    uid_int = int(uid)
                except Exception:
                    continue
                if not isinstance(pdata, dict) or not pdata.get("started"):
                    continue
                await self.config.member_from_ids(ctx.guild.id, uid_int).set(copy.deepcopy(DEFAULT_USER))
                reset += 1

        await ctx.reply(f"Reset data for {reset} started player(s).")

    @cbadmin.command(name="wipeall", aliases=["wipeusers"])
    async def cbadmin_wipeall(self, ctx: commands.Context, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin wipeall confirm`")

        async with ctx.typing():
            try:
                await self.config.clear_all_members(ctx.guild)
            except Exception:
                # fallback: reset known members to defaults
                all_users = await self.config.all_members(ctx.guild)
                for uid in (all_users or {}).keys():
                    try:
                        await self.config.member_from_ids(ctx.guild.id, int(uid)).set(copy.deepcopy(DEFAULT_USER))
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

    @cbadmin.command(name="setbattlecooldown", aliases=["setbattlecd", "setbattlecooldownseconds"])
    async def cbadmin_setbattlecooldown(self, ctx: commands.Context, seconds: int):
        seconds = int(seconds)
        if seconds < 10 or seconds > 3600:
            return await ctx.reply("Battle cooldown must be between 10 and 3600 seconds.")
        await self.config.guild(ctx.guild).battle_cooldown.set(seconds)
        await ctx.reply(f"Battle cooldown set to {seconds}s")

    @cbadmin.command(name="sethakicost")
    async def cbadmin_sethakicost(self, ctx: commands.Context, cost: int):
        await self.config.guild(ctx.guild).haki_cost.set(int(cost))
        await ctx.reply(
            f"Default (fallback) Haki training cost set to {int(cost)} per train. "
            "Use `.cbadmin sethakicosttype <armament|observation|conqueror> <cost>` for per-type pricing."
        )

    @cbadmin.command(name="sethakicosttype")
    async def cbadmin_sethakicosttype(self, ctx: commands.Context, haki_type: str, cost: int):
        """Set per-type haki training cost (armament/observation/conqueror)."""
        t = " ".join((haki_type or "").strip().lower().split())
        if t in ("conq", "conquerors"):
            t = "conqueror"
        if t not in ("armament", "observation", "conqueror"):
            return await ctx.reply("Type must be: `armament`, `observation`, or `conqueror`.")

        cost = int(cost)
        if cost < 0:
            return await ctx.reply("Cost must be >= 0.")

        key = {
            "armament": "haki_cost_armament",
            "observation": "haki_cost_observation",
            "conqueror": "haki_cost_conqueror",
        }[t]
        await self.config.guild(ctx.guild).set_raw(key, value=cost)
        return await ctx.reply(f"Haki training cost for **{t}** set to `{cost}` per train.")

    @cbadmin.command(name="sethakicosts")
    async def cbadmin_sethakicosts(self, ctx: commands.Context, armament: int, observation: int, conqueror: int):
        """Set all per-type haki training costs in one command."""
        armament = int(armament)
        observation = int(observation)
        conqueror = int(conqueror)
        if armament < 0 or observation < 0 or conqueror < 0:
            return await ctx.reply("All costs must be >= 0.")

        gconf = self.config.guild(ctx.guild)
        await gconf.haki_cost_armament.set(armament)
        await gconf.haki_cost_observation.set(observation)
        await gconf.haki_cost_conqueror.set(conqueror)
        return await ctx.reply(
            "Haki training costs updated: "
            f"armament=`{armament}`, observation=`{observation}`, conqueror=`{conqueror}` (per train)."
        )

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
        all_users = await self.config.all_members(ctx.guild)
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
                    await self.config.member_from_ids(ctx.guild.id, uid_int).set(pdata)
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
            await self.config.member(member).set(copy.deepcopy(DEFAULT_USER))
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
            pdata = await self.config.member(member).all()
            haki = pdata.get("haki", {}) or {}
            haki[haki_type] = level
            if haki_type == "conqueror" and level > 0:
                haki["conquerors"] = True
            pdata["haki"] = haki
            await self.config.member(member).set(pdata)

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
    async def cbadmin_fruits_import(self, ctx: commands.Context, mode: str = "rules"):
        """
        Import fruits JSON into the POOL (catalog). Attach a JSON file.

        Modes:
          - rules (default): ignore JSON prices; compute from configured price_rules
          - json: keep the JSON prices as-is
        """
        if not ctx.message.attachments:
            return await ctx.reply("Attach `fruits.json`: `.cbadmin fruits import [rules|json]`")

        mode = (mode or "rules").strip().lower()
        if mode not in ("rules", "json"):
            return await ctx.reply("Mode must be `rules` (default) or `json`.")

        att = ctx.message.attachments[0]
        try:
            raw = await att.read()
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return await ctx.reply(f"Failed to read JSON attachment: {e}")

        fruits_list = (payload or {}).get("fruits")
        if not isinstance(fruits_list, list):
            return await ctx.reply('JSON must look like: `{ "fruits": [ ... ] }`')

        if mode == "rules":
            rules = await self._get_price_rules(ctx.guild)
            for f in fruits_list:
                if not isinstance(f, dict):
                    continue
                f["price"] = self._compute_price_from_rules(f, rules)
                f["price_locked"] = False

        ok, bad = self.fruits.pool_import(payload)
        return await ctx.reply(f"✅ Imported into pool: {ok} OK, {bad} failed. (mode={mode})")

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

    @cbadmin_fruits.command(name="owners", aliases=["owned", "equipped", "holders"])
    async def cbadmin_fruits_owners(self, ctx: commands.Context):
        """Show which users have which devil fruits (grouped by fruit)."""

        # Load all player records and group by equipped fruit
        try:
            all_players = await self.players.all(ctx.guild)
        except Exception:
            all_players = {}

        fruit_to_uids: dict[str, list[int]] = {}
        for uid_raw, pdata in (all_players or {}).items():
            if not isinstance(pdata, dict):
                continue
            fruit = pdata.get("fruit")
            if not fruit:
                continue
            fruit_name = str(fruit).strip()
            if not fruit_name:
                continue
            try:
                uid = int(uid_raw)
            except Exception:
                continue
            fruit_to_uids.setdefault(fruit_name, []).append(uid)

        if not fruit_to_uids:
            return await ctx.reply("No one has a devil fruit equipped yet.")

        # Sort fruits by owner count (desc) then name.
        fruits_sorted: list[tuple[str, list[int]]] = sorted(
            fruit_to_uids.items(),
            key=lambda kv: (-len(kv[1]), (kv[0] or "").lower()),
        )

        per = 10

        def pages() -> int:
            return max(1, (len(fruits_sorted) + per - 1) // per)

        def page_slice(page: int) -> list[tuple[str, list[int]]]:
            p = max(1, min(int(page), pages()))
            start = (p - 1) * per
            return fruits_sorted[start : start + per]

        def _mention(uid: int) -> str:
            m = ctx.guild.get_member(uid)
            return m.mention if m else f"<@{uid}>"

        def build_embed(*, page: int, selected_fruit: str | None) -> discord.Embed:
            p = max(1, min(int(page), pages()))
            chunk = page_slice(p)

            e = discord.Embed(title="🍈 Devil Fruit Owners", color=discord.Color.gold())
            e.description = "\n".join(
                f"**{fruit}** • `{len(uids)}` owner(s)" for fruit, uids in chunk
            )

            total_owners = sum(len(v) for v in fruit_to_uids.values())
            e.set_footer(text=f"Page {p}/{pages()} • {len(fruit_to_uids)} fruits • {total_owners} total owners")

            if selected_fruit:
                uids = fruit_to_uids.get(selected_fruit) or []
                # sort owners by current display name when possible
                def sort_key(uid: int) -> str:
                    m = ctx.guild.get_member(uid)
                    if m:
                        return (m.display_name or m.name or str(uid)).lower()
                    return str(uid)

                uids_sorted = sorted(uids, key=sort_key)
                shown = uids_sorted[:30]
                owners_txt = " ".join(_mention(uid) for uid in shown) or "—"
                if len(uids_sorted) > len(shown):
                    owners_txt += f"\n…and `{len(uids_sorted) - len(shown)}` more"
                e.add_field(name=f"Owners of {selected_fruit}", value=owners_txt[:1024], inline=False)

            return e

        class _OwnersView(discord.ui.View):
            def __init__(self, *, author_id: int):
                super().__init__(timeout=90)
                self.author_id = author_id
                self.page = 1
                self.selected_fruit: str | None = None
                self._msg: discord.Message | None = None
                self._sync_components()

            def _sync_components(self):
                self.prev_btn.disabled = self.page <= 1
                self.next_btn.disabled = self.page >= pages()

                # Update select options from current page
                opts: list[discord.SelectOption] = []
                for fruit, uids in page_slice(self.page)[:25]:
                    label = fruit if len(fruit) <= 100 else (fruit[:99] + "…")
                    desc = f"{len(uids)} owner(s)"
                    opts.append(discord.SelectOption(label=label, value=fruit, description=desc[:100]))
                self.fruit_select.options = opts

                if self.selected_fruit and all(o.value != self.selected_fruit for o in opts):
                    self.selected_fruit = None

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user is not None and interaction.user.id == self.author_id

            async def on_timeout(self) -> None:
                for child in self.children:
                    if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                        child.disabled = True
                if self._msg:
                    try:
                        await self._msg.edit(view=self)
                    except Exception:
                        pass

            @discord.ui.select(placeholder="Pick a fruit…", options=[])
            async def fruit_select(self, interaction: discord.Interaction, select: discord.ui.Select):
                self.selected_fruit = select.values[0] if select.values else None
                self._sync_components()
                await interaction.response.edit_message(
                    embed=build_embed(page=self.page, selected_fruit=self.selected_fruit),
                    view=self,
                )

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page = max(1, self.page - 1)
                self.selected_fruit = None
                self._sync_components()
                await interaction.response.edit_message(
                    embed=build_embed(page=self.page, selected_fruit=self.selected_fruit),
                    view=self,
                )

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page = min(pages(), self.page + 1)
                self.selected_fruit = None
                self._sync_components()
                await interaction.response.edit_message(
                    embed=build_embed(page=self.page, selected_fruit=self.selected_fruit),
                    view=self,
                )

        view = _OwnersView(author_id=ctx.author.id)
        view._sync_components()
        msg = await ctx.send(embed=build_embed(page=view.page, selected_fruit=view.selected_fruit), view=view)
        view._msg = msg

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
        Add ALL fruits from attached fruits.json into the SHOP at once.
        Default stock=1. Does pool import first.
        """
        if not ctx.message.attachments:
            return await ctx.reply("Attach `fruits.json`: `.cbadmin fruits addall [stock]`")

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
            return await ctx.reply('JSON must look like: `{ "fruits": [ ... ] }`')

        # Apply pricing rules so JSON prices do NOT win
        rules = await self._get_price_rules(ctx.guild)
        for f in fruits_list:
            if not isinstance(f, dict):
                continue
            f["price"] = self._compute_price_from_rules(f, rules)
            f["price_locked"] = False

        ok_pool, bad_pool = self.fruits.pool_import(payload)

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
        return await ctx.reply(
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
            pdata = await self.config.member(member).all()
            old = pdata.get("fruit")
            if not old:
                return await ctx.reply("That user has no fruit equipped.")
            pdata["fruit"] = None
            await self.config.member(member).set(pdata)

        await ctx.reply(f"✅ Removed **{member.display_name}**'s fruit: **{old}**")

    @cbadmin.command(name="givefruit")
    async def cbadmin_givefruit(self, ctx: commands.Context, member: discord.Member, *, fruit_name_and_confirm: str):
        """
        Give a user a fruit directly from the POOL (catalog). Does NOT change shop stock.

        Usage:
          .cbadmin givefruit @user <fruit name>
          .cbadmin givefruit @user <fruit name> confirm   (overwrites existing fruit)
        """
        raw = (fruit_name_and_confirm or "").strip()
        if not raw:
            return await ctx.reply("Usage: `.cbadmin givefruit @user <fruit name>`")

        # Allow optional trailing "confirm" to overwrite an existing equipped fruit
        parts = raw.rsplit(" ", 1)
        confirm = None
        fruit_name = raw
        if len(parts) == 2 and parts[1].lower() == "confirm":
            fruit_name = parts[0].strip()
            confirm = "confirm"

        fruit = self.fruits.pool_get(fruit_name)
        if not isinstance(fruit, dict):
            return await ctx.reply("That fruit is not in the pool. Import/add it first.")

        canonical_name = fruit.get("name") or fruit_name

        # Prefer PlayerManager if available
        if hasattr(self, "players") and getattr(self.players, "get", None) and getattr(self.players, "save", None):
            pdata = await self.players.get(member)
            if not pdata.get("started"):
                return await ctx.reply("That user has not started. They must run `.startcb` first.")

            existing = pdata.get("fruit")
            if existing and confirm != "confirm":
                return await ctx.reply(
                    f"That user already has **{existing}**. To overwrite: "
                    f"`.cbadmin givefruit {member.mention} {canonical_name} confirm`"
                )

            pdata["fruit"] = canonical_name
            await self.players.save(member, pdata)
        else:
            pdata = await self.config.member(member).all()
            if not pdata.get("started"):
                return await ctx.reply("That user has not started. They must run `.startcb` first.")

            existing = pdata.get("fruit")
            if existing and confirm != "confirm":
                return await ctx.reply(
                    f"That user already has **{existing}**. To overwrite: "
                    f"`.cbadmin givefruit {member.mention} {canonical_name} confirm`"
                )

            pdata["fruit"] = canonical_name
            await self.config.member(member).set(pdata)

        await ctx.reply(f"✅ Gave **{member.display_name}** the fruit: **{canonical_name}** (stock unchanged).")