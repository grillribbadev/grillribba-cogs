import asyncio
import copy
import io
import json
import random
import re
import time
import inspect  # <-- ADD THIS
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import discord
from redbot.core import commands, Config, bank
from redbot.core.data_manager import cog_data_path

from .constants import BASE_HP, DEFAULT_USER, DEFAULT_PRICE_RULES, MAX_LEVEL
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .embeds import battle_embed
from .utils import exp_to_next, format_duration
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
        self.config.register_member(**DEFAULT_USER)

        self.config.register_guild(
            maintenance=False,
            beri_win=0,
            beri_loss=0,
            turn_delay=1.0,
            battle_cooldown=DEFAULT_BATTLE_COOLDOWN,
            haki_cost=HAKI_TRAIN_COST,
            haki_cost_armament=HAKI_TRAIN_COST,
            haki_cost_observation=HAKI_TRAIN_COST,
            haki_cost_conqueror=HAKI_TRAIN_COST,
            haki_cooldown=HAKI_TRAIN_COOLDOWN,
            conqueror_unlock_cost=5000,
            crew_points_win=1,
            exp_win_min=0,
            exp_win_max=0,
            exp_loss_min=0,
            exp_loss_max=0,
            price_rules=DEFAULT_PRICE_RULES,
            beri_log_channel=None,  # Channel ID for general beri logs
            beri_win_channel=None,  # Channel ID for win beri logs
            beri_loss_channel=None,  # Channel ID for loss beri logs
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
        # Short stable suffix to avoid collisions between same-named guilds.
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
        """Derive a short, human-ish label for filenames from a longer meta note."""
        note = " ".join((note or "").strip().split())
        if not note:
            return ""
        low = note.lower()
        if low.startswith("periodic"):
            return "periodic"
        if low.startswith("manual by") and ":" in note:
            # Prefer the user-provided part after the first ':'
            tail = note.split(":", 1)[1].strip()
            return tail or "manual"
        return note

    def _resolve_backup_path(self, filename: str, guild: discord.Guild | None = None) -> Path | None:
        """Resolve a backup filename safely from root or this guild's backup folder."""
        if not filename:
            return None

        root = self._backup_dir().resolve()

        # Allow relative paths under the backup root (e.g. guild_123/foo.json)
        try:
            candidate = (root / filename).resolve()
            if candidate == root or root not in candidate.parents:
                candidate = None
        except Exception:
            candidate = None
        if candidate and candidate.exists() and candidate.is_file():
            return candidate

        # Otherwise try guild folder then root folder.
        if guild:
            gp = (self._guild_backup_dir(guild) / filename)
            if gp.exists() and gp.is_file():
                return gp
        rp = (self._backup_dir() / filename)
        if rp.exists() and rp.is_file():
            return rp
        return None

    async def _write_backup(self, *, note: str = "", guild: discord.Guild = None) -> Path:
        """Write a backup of member-scoped data (per guild). If no guild provided, backs up all guilds."""
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        note = " ".join((note or "").strip().split())

        payload = {
            "meta": {
                "cog": "crewbattles",
                "ts": datetime.now(timezone.utc).isoformat(),
                "note": note,
                "scope": "guild" if guild else "all_guilds",
                "guild_id": int(getattr(guild, "id", 0) or 0) if guild else None,
                "guild_name": str(getattr(guild, "name", "")) if guild else None,
            }
        }

        if guild:
            members = await self.config.all_members(guild)
            payload["meta"]["count"] = len(members or {})
            payload["members"] = members or {}
        else:
            guilds_payload = {}
            for g in list(getattr(self.bot, "guilds", []) or []):
                try:
                    guilds_payload[str(int(g.id))] = await self.config.all_members(g)
                except Exception:
                    guilds_payload[str(int(getattr(g, "id", 0) or 0))] = {}
            payload["meta"]["count"] = sum(len(v or {}) for v in guilds_payload.values())
            payload["guilds"] = guilds_payload

        # Include legacy user-scope data for safety/rollback.
        try:
            payload["users_legacy"] = await self.config.all_users()
        except Exception:
            payload["users_legacy"] = {}

        if guild:
            fname = f"users_{ts}.json"
            path = self._guild_backup_dir(guild) / fname
        else:
            fname = f"users_allguilds_{ts}.json"
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
                # Prefer per-guild periodic backups for clarity and safety.
                for g in list(getattr(self.bot, "guilds", []) or []):
                    try:
                        await self._write_backup(note=f"periodic guild={int(getattr(g, 'id', 0) or 0)}", guild=g)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[CrewBattles] periodic backup failed: {e}")
            await asyncio.sleep(6 * 60 * 60)

    async def _restore_backup(self, backup_path: Path, guild: discord.Guild = None) -> int:
        def _sync_read():
            with open(backup_path, "r", encoding="utf-8") as f:
                return json.load(f)

        data = await asyncio.to_thread(_sync_read)
        restored = 0

        # Preferred: member-scoped restore
        members = (data or {}).get("members")
        guilds_map = (data or {}).get("guilds")

        if guild and isinstance(members, dict):
            for uid, pdata in members.items():
                try:
                    uid_int = int(uid)
                except Exception:
                    continue
                if not isinstance(pdata, dict):
                    continue
                try:
                    await self.config.member_from_ids(guild.id, uid_int).set(pdata)
                    restored += 1
                except Exception:
                    pass
            return restored

        if guild and isinstance(guilds_map, dict):
            bucket = guilds_map.get(str(int(guild.id)))
            if isinstance(bucket, dict):
                for uid, pdata in bucket.items():
                    try:
                        uid_int = int(uid)
                    except Exception:
                        continue
                    if not isinstance(pdata, dict):
                        continue
                    try:
                        await self.config.member_from_ids(guild.id, uid_int).set(pdata)
                        restored += 1
                    except Exception:
                        pass
                return restored

        # Legacy: restore user-scope backups into THIS guild's member scope
        legacy_users = (data or {}).get("users") or (data or {}).get("users_legacy") or {}
        if guild and isinstance(legacy_users, dict):
            for uid, pdata in legacy_users.items():
                try:
                    uid_int = int(uid)
                except Exception:
                    continue
                if not isinstance(pdata, dict):
                    continue
                try:
                    await self.config.member_from_ids(guild.id, uid_int).set(pdata)
                    restored += 1
                except Exception:
                    pass
            return restored

        raise ValueError("Backup file format invalid or no guild provided for restore")

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

    async def _add_money(self, member: discord.abc.User, amount: int, *, reason: str = "", source_channel: int = None) -> bool:
        amount = int(amount or 0)
        if amount == 0:
            return True

        core = self._beri()
        success = False
        if core:
            try:
                await core.add_beri(member, amount, reason=reason or "crew_battles:add", bypass_cap=True)
                success = True
            except Exception:
                pass

        if not success:
            try:
                if amount > 0:
                    await bank.deposit_credits(member, amount)
                    success = True
                else:
                    await bank.withdraw_credits(member, abs(amount))
                    success = True
            except Exception:
                return False

        # Emit beri log to configured channel and persist a local log copy
        try:
            if hasattr(member, "guild") and member.guild:
                guild = member.guild
            elif hasattr(member, "guild_id"):
                guild = self.bot.get_guild(member.guild_id)
            else:
                guild = None
            if guild:
                # build log entry
                user = member
                entry = {
                    "ts": int(datetime.now(timezone.utc).timestamp()),
                    "amount": int(amount),
                    "reason": reason or "",
                    "channel": int(source_channel) if source_channel is not None else None,
                }

                # choose which configured channel to send the log to
                # prefer specific win/loss channels when reason indicates a battle result
                chan_id = None
                try:
                    if isinstance(reason, str) and ":win" in reason:
                        chan_id = await self.config.guild(guild).beri_win_channel()
                    elif isinstance(reason, str) and ":loss" in reason:
                        chan_id = await self.config.guild(guild).beri_loss_channel()
                except Exception:
                    chan_id = None

                # fallback to general beri log channel
                if not chan_id:
                    try:
                        chan_id = await self.config.guild(guild).beri_log_channel()
                    except Exception:
                        chan_id = None

                if chan_id:
                    channel = guild.get_channel(chan_id)
                    if channel:
                        embed = discord.Embed(
                            title=("Beri Gained" if amount > 0 else "Beri Spent"),
                            color=discord.Color.green() if amount > 0 else discord.Color.red(),
                            timestamp=datetime.now(timezone.utc),
                        )
                        embed.add_field(name="User", value=f"{user.mention}\n`{user}`\n(ID: `{user.id}`)", inline=False)
                        embed.add_field(name="Amount", value=f"{'+' if amount > 0 else '-'}{abs(amount):,} beri", inline=True)
                        ch_str = f"<#{entry['channel']}>" if entry.get("channel") else "-"
                        embed.add_field(name="Channel", value=ch_str, inline=True)
                        embed.add_field(name="Reason", value=reason or "-", inline=False)
                        embed.set_footer(text=f"User: {user}")
                        try:
                            await channel.send(embed=embed)
                        except Exception:
                            pass

                # persist to member-scoped logs (cap to 200 entries)
                try:
                    gid = getattr(guild, "id", None)
                    uid = getattr(user, "id", None)
                    if gid and uid:
                        mconf = self.config.member_from_ids(gid, uid)
                        try:
                            existing = await mconf.beri_logs()
                        except Exception:
                            allm = await mconf.all()
                            existing = allm.get("beri_logs") if isinstance(allm, dict) else None
                        if not isinstance(existing, list):
                            existing = []
                        existing.append(entry)
                        # cap
                        if len(existing) > 200:
                            existing = existing[-200:]
                        try:
                            await mconf.set_raw("beri_logs", value=existing)
                        except Exception:
                            try:
                                await mconf.beri_logs.set(existing)
                            except Exception:
                                pass
                except Exception:
                    pass

        return success

    async def _spend_money(self, member: discord.abc.User, amount: int, *, reason: str = "", source_channel: int = None) -> bool:
        amount = int(amount or 0)
        if amount <= 0:
            return True
        bal = await self._get_money(member)
        if bal < amount:
            return False
        return await self._add_money(member, -amount, reason=reason or "crew_battles:spend", source_channel=source_channel)

    async def _add_beri(self, member: discord.abc.User, amount: int, *, reason: str = "", source_channel: int = None) -> bool:
        # backwards-compatible alias used elsewhere in this file
        return await self._add_money(member, amount, reason=reason, source_channel=source_channel)

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

        # Hard ceiling: once max level, do not accumulate EXP.
        if cur_level >= MAX_LEVEL:
            player["level"] = int(MAX_LEVEL)
            player["exp"] = 0
            return 0

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
    async def cbadmin_backup(self, ctx: commands.Context, *, note: str = ""):
        note = " ".join((note or "").strip().split())
        # Description is stored in backup metadata; not used in filename.
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

            metas = await asyncio.to_thread(lambda: [read_meta(p) for p in paths])

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
            return await ctx.reply("Run: `.cbadmin restore <filename> confirm`")

        bp = self._resolve_backup_path(filename, guild=ctx.guild)
        if not bp:
            return await ctx.reply("Backup file not found.")

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
            bp = self._resolve_backup_path(path, guild=ctx.guild)
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

        # Keep a little under the common 8MB upload cap.
        if size and size > 7_500_000:
            return await ctx.reply(f"That file is too large to send here ({size:,} bytes).")

        try:
            await ctx.reply(file=discord.File(str(file_path), filename=file_path.name))
        except Exception as e:
            await ctx.reply(f"Could not send file: {e}")

    @cbadmin.command(name="storedcounts")
    async def cbadmin_storedcounts(self, ctx: commands.Context):
        try:
            all_users = await self.config.all_members(ctx.guild)
        except Exception as e:
            return await ctx.reply(f"Could not read storage: {e}")
        total = len(all_users or {})
        started = sum(1 for _, v in (all_users or {}).items() if isinstance(v, dict) and v.get("started"))
        await ctx.reply(f"Stored member records (this server): {total} | started=True: {started}")

    @cbadmin.command(name="resetall", aliases=["resetstarted", "resetplayers"])
    async def cbadmin_resetall(self, ctx: commands.Context, confirm: str = None):
        if confirm != "confirm":
            return await ctx.reply("Run: `.cbadmin resetall confirm`")

        async with ctx.typing():
            try:
                await self._write_backup(note=f"pre-resetall by {ctx.author.id}", guild=ctx.guild)
            except Exception as e:
                return await ctx.reply(f"Backup failed; aborting reset: {e}")

            all_users = await self.players.all(ctx.guild)
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
                    await self.config.member_from_ids(ctx.guild.id, uid).set(copy.deepcopy(DEFAULT_USER))
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
                await self._write_backup(note=f"pre-wipeall by {ctx.author.id}", guild=ctx.guild)
            except Exception as e:
                return await ctx.reply(f"Backup failed; aborting wipe: {e}")

            all_users = await self.players.all(ctx.guild)
            uids = []
            for uid in (all_users or {}).keys():
                try:
                    uids.append(int(uid))
                except Exception:
                    pass

            wiped = 0
            for uid in uids:
                try:
                    await self.config.member_from_ids(ctx.guild.id, uid).clear()
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
    async def setbattlecooldown(self, ctx, seconds: int):
        seconds = int(seconds)
        if seconds < MIN_BATTLE_COOLDOWN or seconds > MAX_BATTLE_COOLDOWN:
            return await ctx.reply(
                f"Battle cooldown must be between {MIN_BATTLE_COOLDOWN} and {MAX_BATTLE_COOLDOWN} seconds."
            )
        await self.config.guild(ctx.guild).battle_cooldown.set(seconds)
        await ctx.reply(f"Battle cooldown set to {seconds}s")

    @cbadmin.command()
    async def sethakicost(self, ctx, cost: int):
        await self.config.guild(ctx.guild).haki_cost.set(int(cost))
        await ctx.reply(
            f"Default (fallback) Haki training cost set to {int(cost)} per train. "
            "Use `.cbadmin sethakicosttype <armament|observation|conqueror> <cost>` for per-type pricing."
        )

    @cbadmin.command()
    async def sethakicosttype(self, ctx, haki_type: str, cost: int):
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

    @cbadmin.command()
    async def sethakicosts(self, ctx, armament: int, observation: int, conqueror: int):
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
            all_users = await self.players.all(ctx.guild)
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
                        await self.config.member_from_ids(ctx.guild.id, int(uid)).set(pdata)
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
                await self.config.member(member).set(copy.deepcopy(DEFAULT_USER))
            except Exception as e:
                return await ctx.reply(f"Reset failed: {e}")

        await ctx.reply(f"✅ Reset Crew Battles data for **{member.display_name}**.")

    @cbadmin.command(name="setlevel", aliases=["setlvl", "setuserlevel"])
    async def cbadmin_setlevel(self, ctx: commands.Context, member: discord.Member, level: int):
        """Set a user's CrewBattles level."""
        try:
            level = int(level)
        except Exception:
            return await ctx.reply("Level must be a number.")

        level = max(1, min(int(MAX_LEVEL), level))
        p = await self.players.get(member)
        if not p.get("started"):
            e = discord.Embed(
                title="⚠️ Player Not Started",
                description=(
                    f"**{member.display_name}** has not started Crew Battles yet.\n"
                    "They must run **`.startcb`** first before you can set their level."
                ),
                color=discord.Color.orange(),
            )
            return await ctx.reply(embed=e)

        before_level = int(p.get("level", 1) or 1)
        p["level"] = level
        # Keep it simple/consistent: reset EXP at the new level.
        p["exp"] = 0
        await self.players.save(member, p)

        g = await self.config.guild(ctx.guild).all()
        cost = int(g.get("conqueror_unlock_cost", 5000) or 5000)
        if cost < 0:
            cost = 0

        conq_text = (
            "Locked until **Level 10**."
            if level < 10
            else f"Unlock with **`.cbunlockconqueror`** for `{cost:,}` Beri."
        )

        e = discord.Embed(
            title="✅ Crew Battles Level Updated",
            description=f"{member.mention} your level was updated by an admin.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        e.add_field(
            name="Level",
            value=f"`{before_level}` → `{level}`\nEXP reset to `0` at the new level.",
            inline=False,
        )
        e.add_field(
            name="👑 Conqueror Unlock",
            value=(
                f"{conq_text}"
            ),
            inline=False,
        )
        return await ctx.reply(embed=e)

    # =========================================================
    # Player commands
    # =========================================================

    # --- REMOVE/disable legacy leaderboard stub (it shadows the mixin even without a decorator)
    # DISABLE legacy leaderboard so the PlayerCommandsMixin.cbleaderboard(*args) is used.
    # @commands.command(name="cbleaderboard", aliases=["cblb", "cbtop"])
    #async def _legacy_cbleaderboard(self, ctx: commands.Context, page: int = 1, sort_by: str = "wins"):
     #   return await ctx.send("Legacy cbleaderboard disabled; using mixin command.")

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
            value=f"Level: `{lvl}` • EXP: `{exp}`\nTrain Haki: **`.cbtrain armament|observation|conqueror`**",
            inline=False,
        )
        e.set_footer(text="Tip: Use .cbhaki to see your Haki bonuses (crit/dodge/counter).")
        return await ctx.reply(embed=e)

    # REMOVE / DISABLE THIS LEGACY COMMAND (it overrides the mixin cbshop)
    # @commands.command()
    #async def cbshop(self, ctx: commands.Context, page: int = 1):
     #   return await ctx.send("Legacy cbshop disabled; using mixin command.")

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
        ok = await self._spend_money(ctx.author, price, reason="crew_battles:buy_fruit", source_channel=getattr(ctx.channel, 'id', None))
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
            ok = await self._spend_money(ctx.author, cost, reason="crew_battles:remove_fruit", source_channel=getattr(ctx.channel, 'id', None))
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

        g = await self.config.guild(ctx.guild).all()
        unlock_cost = int(g.get("conqueror_unlock_cost", 5000) or 5000)
        if unlock_cost < 0:
            unlock_cost = 0

        level = int(p.get("level", 1) or 1)

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
            if level < 10:
                locked_txt = (
                    "`Locked`\n"
                    f"🔒 Requires **Level 10** (you are level `{level}`).\n"
                    f"💰 Unlock cost: `{unlock_cost:,}` Beri (server-set)"
                )
            else:
                locked_txt = (
                    "`Locked`\n"
                    "🔓 Unlock with **`.cbunlockconqueror`**\n"
                    f"💰 Cost: `{unlock_cost:,}` Beri"
                )
            embed.add_field(
                name="👑 Conqueror (COUNTER CRIT)",
                value=locked_txt,
                inline=False,
            )

        embed.set_footer(text="Train: .cbtrain armament|observation|conqueror")
        await ctx.reply(embed=embed)

    @commands.command(name="cbunlockconqueror", aliases=["unlockconqueror", "cbunlockconq", "cbunlockconquerors"])
    async def cbunlockconqueror(self, ctx: commands.Context):
        """Unlock Conqueror's Haki (requires level 10)."""
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must start Crew Battles first (`.startcb`).")

        g = await self.config.guild(ctx.guild).all()
        cost = int(g.get("conqueror_unlock_cost", 5000) or 5000)
        if cost < 0:
            cost = 0

        level = int(p.get("level", 1) or 1)
        if level < 10:
            e = discord.Embed(
                title="👑 Conqueror Locked",
                description=(
                    f"You can unlock Conqueror's Haki at **Level 10**.\n"
                    f"Your level: `{level}`\n\n"
                    f"When you reach Level 10: use **`.cbunlockconqueror`** to unlock it for `{cost:,}` Beri."
                ),
                color=discord.Color.orange(),
            )
            return await ctx.reply(embed=e)

        haki = p.get("haki", {}) or {}
        if bool(haki.get("conquerors")):
            return await ctx.reply("👑 You already unlocked Conqueror's Haki.")

        if cost > 0:
            ok = await self._spend_money(ctx.author, cost, reason="crew_battles:unlock_conqueror", source_channel=getattr(ctx.channel, 'id', None))
            if not ok:
                bal = await self._get_money(ctx.author)
                e = discord.Embed(
                    title="💸 Not Enough Beri",
                    description=f"Unlock costs `{cost:,}` Beri, but you have `{bal:,}`.",
                    color=discord.Color.red(),
                )
                return await ctx.reply(embed=e)

        haki["conquerors"] = True
        haki["conqueror"] = int(haki.get("conqueror", 0) or 0)
        p["haki"] = haki
        await self.players.save(ctx.author, p)

        e = discord.Embed(
            title="⚡👑 Conqueror's Haki Awakened! 👑⚡",
            description=f"{ctx.author.mention} has unlocked **Conqueror's Haki**!\n\n⚡ The air crackles with lightning…",
            color=discord.Color.purple(),
        )
        try:
            e.set_thumbnail(url=ctx.author.display_avatar.url)
        except Exception:
            pass
        if cost > 0:
            e.add_field(name="Cost", value=f"`{cost:,}` Beri", inline=True)
        e.add_field(name="Next", value="Train it with **`.cbtrain conqueror`**", inline=False)

        # Send in-channel (not ephemeral) for flair
        return await ctx.send(embed=e)

    @commands.command(name="cbtrainhaki")
    async def cbtrainhaki(self, ctx: commands.Context, haki_type: str, *rest: str):
        # Delegate to the shared implementation in PlayerCommandsMixin.
        # This enforces +1 per selected type per train, and supports training all
        # three types in one action via the interactive menu when Conqueror is unlocked.
        return await PlayerCommandsMixin.cbtrainhaki(self, ctx, haki_type, *rest)

    @commands.command(name="cbtrain")
    async def cbtrain(self, ctx, haki_type: str = None, *rest: str):
        # No args -> interactive menu (can train arm/obs/conq together, +1 each)
        return await PlayerCommandsMixin.cbtrain(self, ctx, haki_type, *rest)

    async def _run_battle(self, ctx: commands.Context, opponent: discord.Member):
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
            g = await self.config.guild(ctx.guild).all()

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

            # battle cooldown
            now = self._now()
            cd = int(g.get("battle_cooldown", DEFAULT_BATTLE_COOLDOWN) or DEFAULT_BATTLE_COOLDOWN)
            cd = max(MIN_BATTLE_COOLDOWN, min(MAX_BATTLE_COOLDOWN, int(cd)))
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
            turn_delay = float(g.get("turn_delay", 1.0) or 1.0)
            turn_delay = max(0.0, min(5.0, turn_delay))  # hard clamp so it doesn't freeze channels

            hp1 = int(BASE_HP)
            hp2 = int(BASE_HP)

            log_entries = []
            turn_no = 0
            msg = await ctx.send(embed=battle_embed(ctx.author, opponent, hp1, hp2, BASE_HP, BASE_HP, "—"))

            def _yaml_safe(s: str) -> str:
                s = str(s or "")
                s = s.replace("\\", "\\\\").replace('"', "\\\"")
                s = s.replace("\n", " ").replace("\r", " ").strip()
                return s

            def _yaml_quote(s: str) -> str:
                return f'"{_yaml_safe(s)}"'

            def _short(s: str, n: int) -> str:
                s = str(s or "")
                return (s[: n - 1] + "…") if n >= 2 and len(s) > n else s

            # Play turns (cap log lines so embed stays readable)
            for side, dmg, defender_hp_after, atk_name, crit in turns:
                turn_no += 1
                if side == "p1":
                    hp2 = int(defender_hp_after)
                    actor = ctx.author.display_name
                    defender = opponent.display_name
                else:
                    hp1 = int(defender_hp_after)
                    actor = opponent.display_name
                    defender = ctx.author.display_name

                actor_s = _short(actor, 18)
                defender_s = _short(defender, 18)

                is_counter = str(atk_name) == "Conqueror Counter"
                is_fruit = isinstance(atk_name, str) and atk_name.startswith("🍈 ")

                if int(dmg) <= 0 and str(atk_name).lower() == "dodged":
                    # NOTE: In the turn tuple, `side` is the attacker. A "Dodged" entry means
                    # the defender dodged the attacker's move.
                    line_text = f"💨 {defender_s} dodged {actor_s}"
                else:
                    if is_counter:
                        line_text = f"👑 {actor_s}: Counter - {int(dmg)} (COUNTER-CRIT)"
                    else:
                        move_display = str(atk_name)
                        emoji = "🗡️"
                        if is_fruit:
                            emoji = "🍈"
                            move_display = str(atk_name)[2:].strip() or "Fruit Technique"

                        move_display = _short(move_display, 22)

                        suffix = ""
                        if crit:
                            suffix = " (CRIT)"
                        elif is_fruit:
                            suffix = " (FRUIT)"

                        line_text = f"{emoji} {actor_s}: {move_display} - {int(dmg)}{suffix}"

                entry = f"- {_yaml_quote(line_text)}"

                log_entries.append(entry)
                # Keep more history since entries are now one-liners
                log_entries = log_entries[-10:]
                log_text = "```yaml\n" + "\n".join(log_entries) + "\n```"

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

            winner_pre_level = int(winner_p.get("level", 1) or 1)
            loser_pre_level = int(loser_p.get("level", 1) or 1)

            win_min = int(g.get("exp_win_min", 0) or 0)
            win_max = int(g.get("exp_win_max", 0) or 0)
            loss_min = int(g.get("exp_loss_min", 0) or 0)
            loss_max = int(g.get("exp_loss_max", 0) or 0)

            win_gain = random.randint(min(win_min, win_max), max(win_min, win_max)) if max(win_min, win_max) > 0 else 0
            loss_gain = random.randint(min(loss_min, loss_max), max(loss_min, loss_max)) if max(loss_min, loss_max) > 0 else 0

            # Max-level ceiling: don't award EXP if already max.
            if winner_pre_level >= MAX_LEVEL:
                win_gain = 0
            if loser_pre_level >= MAX_LEVEL:
                loss_gain = 0

            leveled_w = self._apply_exp(winner_p, win_gain)
            leveled_l = self._apply_exp(loser_p, loss_gain)

            beri_win = int(g.get("beri_win", 0) or 0)
            beri_loss = int(g.get("beri_loss", 0) or 0)
            if beri_win:
                await self._add_beri(winner_user, beri_win, reason="crew_battle:win", source_channel=getattr(ctx.channel, 'id', None))
            if beri_loss:
                await self._add_beri(loser_user, beri_loss, reason="crew_battle:loss", source_channel=getattr(ctx.channel, 'id', None))

            raw_crew_points = g.get("crew_points_win", 1)
            crew_points = int(raw_crew_points) if raw_crew_points is not None else 1
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

            crew_points_line = (
                "🏴‍☠️ **Crew Points Added:** `DISABLED`"
                if int(crew_points or 0) <= 0
                else f"🏴‍☠️ **Crew Points Added:** `+{points_added}`"
            )

            winner_lines = [
                f"💰 **Beri:** `+{beri_win:,}`",
                (f"⭐ **EXP:** `MAX`" if winner_level >= MAX_LEVEL else f"⭐ **EXP Gained:** `+{win_gain}`"),
                (None if winner_level >= MAX_LEVEL else f"✨ **Current EXP:** `{winner_exp}`"),
                (f"📈 **Level:** `MAX`" if winner_level >= MAX_LEVEL else (f"📈 **Level:** `{winner_level}`" + (f" *(+{leveled_w})*" if leveled_w else ""))),
                crew_points_line,
            ]
            winner_lines = [x for x in winner_lines if x]
            res.add_field(name=f"🏆 Winner — {winner_who}", value="\n".join(winner_lines), inline=False)

            # loser rewards in “lower/smaller” style using blockquote + italics
            loser_lines = [
                f"💰 Beri: `+{beri_loss:,}`",
                (f"⭐ EXP: `MAX`" if loser_level >= MAX_LEVEL else f"⭐ EXP Gained: `+{loss_gain}`"),
                (None if loser_level >= MAX_LEVEL else f"✨ Current EXP: `{loser_exp}`"),
                (f"📉 Level: `MAX`" if loser_level >= MAX_LEVEL else (f"📉 Level: `{loser_level}`" + (f" *(+{leveled_l})*" if leveled_l else ""))),
            ]
            loser_lines = [x for x in loser_lines if x]
            loser_value = "\n".join(f"> *{line}*" for line in loser_lines)
            res.add_field(name=f"☠️ Loser — {loser_who}", value=loser_value, inline=False)

            res.set_footer(text="✨ Armament=CRIT • Observation=DODGE • Conqueror=COUNTER CRIT • Fruits can proc abilities")
            await ctx.reply(embed=res)

        finally:
            try:
                self._active_battles.discard(ctx.channel.id)
            except Exception:
                pass

    @commands.group(name="battle", invoke_without_command=True)
    async def battle(self, ctx: commands.Context, opponent: discord.Member = None):
        """Start a Crew Battle, or use `.battle random`."""
        if opponent is None:
            return await ctx.reply("Usage: `.battle @user` or `.battle random`")
        return await self._run_battle(ctx, opponent)

    @battle.command(name="random")
    async def battle_random(self, ctx: commands.Context):
        """Battle a random started player in this server."""
        if not ctx.guild:
            return await ctx.reply("This command can only be used in a server.")

        author_team = await self._team_of(ctx.guild, ctx.author)

        all_users = await self.players.all(ctx.guild)
        candidates: list[discord.Member] = []
        for uid, pdata in (all_users or {}).items():
            if not isinstance(pdata, dict) or not pdata.get("started"):
                continue
            try:
                uid_int = int(uid)
            except Exception:
                continue
            if uid_int == ctx.author.id:
                continue

            m = ctx.guild.get_member(uid_int)
            if not m or m.bot:
                continue

            # Enforce the existing rule: you cannot battle crewmates/teammates.
            if author_team is not None:
                try:
                    m_team = await self._team_of(ctx.guild, m)
                except Exception:
                    m_team = None
                if m_team is not None and m_team == author_team:
                    continue
            candidates.append(m)

        if not candidates:
            return await ctx.reply("No eligible players found in the player pool (excluding bots, yourself, and teammates).")

        opponent = random.choice(candidates)
        return await self._run_battle(ctx, opponent)

    # NOTE: Do NOT define a method named `cbleaderboard` on the Cog class unless it is the
    # actual decorated command. A plain method with that name will override the Command
    # object defined in PlayerCommandsMixin and cause the leaderboard command to disappear.
    async def _cbleaderboard_delegate(self, ctx: commands.Context, *args: str):
        return await PlayerCommandsMixin.cbleaderboard(self, ctx, *args)

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

    # --- REMOVE/disable legacy cbtutorial command (decorator makes it override the mixin)
    # @commands.command(name="cbtutorial", aliases=["cbguide", "cbhelp"])
    async def _legacy_cbtutorial(self, ctx: commands.Context):
        return await ctx.send("Legacy tutorial is disabled; use the mixin `.cbtutorial`.")