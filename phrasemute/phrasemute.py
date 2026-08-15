# phrasemute.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional, Union, List

import discord
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.bot import Red

# Matches Discord user mentions: <@123> or <@!123>
MENTION_RE = re.compile(r"<@!?\d+>")
# Collapses multiple "<@mention>" tokens into one
MULTI_MENTION_TOKEN_RE = re.compile(r"(?:<@mention>\s*){2,}")
DURATION_RE = re.compile(r"(?:\d+[smhd])+")
DURATION_PART_RE = re.compile(r"(\d+)([smhd])")
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def normalize_for_match(s: str, *, strip_leading_mentions: bool = True) -> str:
    """
    Safe normalizer:
    - case-insensitive (casefold)
    - trims edges
    - normalizes ALL user mentions to "<@mention>" so victim IDs don't matter
    - collapses repeated "<@mention>" tokens to ONE so ping-count doesn't matter
    - optionally strips leading mention spam entirely
    - collapses all whitespace (spaces/newlines/tabs) to single spaces
    """
    s = (s or "").casefold().strip()

    # Normalize mentions to a stable token
    s = MENTION_RE.sub("<@mention>", s)

    # Collapse multiple mentions anywhere: "<@mention> <@mention> <@mention>" -> "<@mention>"
    s = MULTI_MENTION_TOKEN_RE.sub("<@mention> ", s)

    # Collapse whitespace
    s = " ".join(s.split())

    if strip_leading_mentions:
        # Remove leading mention spam: "<@mention> <@mention> ..." -> ""
        while s.startswith("<@mention>"):
            s = s[len("<@mention>"):].lstrip()
        s = " ".join(s.split())

    return s


@dataclass
class MatchResult:
    phrase: str  # stored phrase/pattern that matched


class PhraseMute(commands.Cog):
    """
    Auto-mute users who send configured phrases.

    Key behaviors:
    - Fast: triggers in on_message_without_command
    - Safe matching default: exact (normalized full-message match)
    - Robust to: mention spam, different victim IDs, whitespace/newline tricks
    - Deletes triggering message (optional)
    - Applies configured muted role
    - Logs ONE embed per user per cooldown window (prevents mod-log spam)
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=984231274001, force_registration=True)

        defaults_guild = {
            "enabled": False,
            "muted_role_id": None,         # int
            "log_channel_id": None,        # int
            "mention_role_id": None,       # int (optional)
            "mention_user_id": None,       # int (optional)
            "delete_trigger_message": True,
            "phrases": [],                 # list[str]
            "match_mode": "exact",         # "exact" | "contains" | "regex"
            "ignore_admins": True,
            "ignore_mods": True,
            "log_cooldown_seconds": 45,    # log once per user per N seconds
            "mute_duration_seconds": 0,    # 0 means permanent
            "scheduled_unmutes": {},       # {user_id: unix expiry timestamp}
        }
        self.config.register_guild(**defaults_guild)

        # In-memory cooldown tracker (fast, resets on bot restart)
        # key = (guild_id, user_id) -> last_log_timestamp (float)
        self._recent_logs = {}
        self.check_expired_mutes.start()

    def cog_unload(self):
        self.check_expired_mutes.cancel()

    # -------------------------
    # Helpers
    # -------------------------

    async def _get_muted_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role_id = await self.config.guild(guild).muted_role_id()
        if not role_id:
            return None
        return guild.get_role(role_id)

    async def _get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        channel_id = await self.config.guild(guild).log_channel_id()
        if not channel_id:
            return None
        ch = guild.get_channel(channel_id)
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _get_mention_target(self, guild: discord.Guild) -> Optional[Union[discord.Role, discord.Member]]:
        role_id = await self.config.guild(guild).mention_role_id()
        user_id = await self.config.guild(guild).mention_user_id()

        if role_id:
            role = guild.get_role(role_id)
            if role:
                return role
        if user_id:
            member = guild.get_member(user_id)
            if member:
                return member
        return None

    async def _is_ignored(self, member: discord.Member) -> bool:
        if member.guild is None:
            return True

        ignore_admins = await self.config.guild(member.guild).ignore_admins()
        ignore_mods = await self.config.guild(member.guild).ignore_mods()

        if ignore_admins and member.guild_permissions.administrator:
            return True

        if ignore_mods:
            # Prefer Red helper if available
            try:
                from redbot.core.utils.mod import is_mod_or_admin
                return await is_mod_or_admin(self.bot, member)
            except Exception:
                perms = member.guild_permissions
                if perms.manage_messages or perms.kick_members or perms.ban_members or perms.manage_guild:
                    return True

        return False

    async def _match_phrase(self, guild: discord.Guild, content: str) -> Optional[MatchResult]:
        phrases: List[str] = await self.config.guild(guild).phrases()
        if not phrases:
            return None

        mode = await self.config.guild(guild).match_mode()

        if mode == "regex":
            for pattern in phrases:
                try:
                    if re.search(pattern, content or "", flags=re.IGNORECASE):
                        return MatchResult(phrase=pattern)
                except re.error:
                    continue
            return None

        msg = normalize_for_match(content, strip_leading_mentions=True)

        for raw in phrases:
            p = normalize_for_match(raw, strip_leading_mentions=True)

            if mode == "exact":
                if msg == p:
                    return MatchResult(phrase=raw)

            elif mode == "contains":
                if p and p in msg:
                    return MatchResult(phrase=raw)

        return None

    async def _mute_member(self, member: discord.Member, muted_role: discord.Role) -> bool:
        if muted_role in member.roles:
            return True  # already muted

        me = member.guild.me
        if me is None:
            return False

        if not me.guild_permissions.manage_roles:
            return False

        # Bot must be above muted role AND above the target member
        if muted_role >= me.top_role:
            return False
        if member.top_role >= me.top_role:
            return False

        try:
            await member.add_roles(muted_role, reason="PhraseMute: triggered phrase")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    def _dt_discord(self, dt) -> str:
        if not dt:
            return "Unknown"
        ts = int(dt.timestamp())
        return f"<t:{ts}:F> • <t:{ts}:R>"

    def _should_log_now(self, guild_id: int, user_id: int, cooldown: int) -> bool:
        key = (guild_id, user_id)
        now = time.time()
        last = self._recent_logs.get(key)
        if last is not None and (now - last) < cooldown:
            return False
        self._recent_logs[key] = now
        return True

    @staticmethod
    def _parse_duration(duration: str) -> Optional[int]:
        duration = duration.lower().strip()
        if not DURATION_RE.fullmatch(duration):
            return None
        return sum(int(value) * DURATION_UNITS[unit] for value, unit in DURATION_PART_RE.findall(duration))

    @staticmethod
    def _format_duration(seconds: int) -> str:
        parts = []
        for unit, unit_seconds in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
            value, seconds = divmod(seconds, unit_seconds)
            if value:
                parts.append(f"{value}{unit}")
        return " ".join(parts) or "0s"

    async def _schedule_unmute(self, member: discord.Member, duration: int) -> None:
        expires = await self.config.guild(member.guild).scheduled_unmutes()
        expires[str(member.id)] = int(time.time()) + duration
        await self.config.guild(member.guild).scheduled_unmutes.set(expires)

    @tasks.loop(seconds=30)
    async def check_expired_mutes(self):
        now = int(time.time())
        for guild_id in (await self.config.all_guilds()).keys():
            guild_config = self.config.guild_from_id(guild_id)
            expires = await guild_config.scheduled_unmutes()
            expired_user_ids = [user_id for user_id, expiry in expires.items() if int(expiry) <= now]
            if not expired_user_ids:
                continue

            guild = self.bot.get_guild(guild_id)
            muted_role = await self._get_muted_role(guild) if guild else None
            for user_id in expired_user_ids:
                if guild and muted_role:
                    member = guild.get_member(int(user_id))
                    if member and muted_role in member.roles:
                        try:
                            await member.remove_roles(muted_role, reason="PhraseMute timer expired")
                        except (discord.Forbidden, discord.HTTPException):
                            continue
                expires.pop(user_id, None)
            await guild_config.scheduled_unmutes.set(expires)

    @check_expired_mutes.before_loop
    async def before_check_expired_mutes(self):
        await self.bot.wait_until_ready()

    async def _log_action(
        self,
        *,
        message: discord.Message,
        matched: MatchResult,
        success: bool,
        muted_role: Optional[discord.Role],
        deleted: bool,
    ) -> None:
        guild = message.guild
        if guild is None:
            return

        log_channel = await self._get_log_channel(guild)
        if log_channel is None:
            return

        mention_target = await self._get_mention_target(guild)
        ping = mention_target.mention if mention_target else ""

        member = message.author if isinstance(message.author, discord.Member) else None
        author = message.author
        where = message.channel
        jump = message.jump_url

        status = "✅ Muted" if success else "❌ Failed to mute"
        role_text = muted_role.mention if muted_role else "Not set"
        deleted_text = "✅ Yes" if deleted else "❌ No"

        created_at = self._dt_discord(author.created_at)
        joined_at = self._dt_discord(member.joined_at) if member and member.joined_at else "Unknown"

        roles_text = "Unknown"
        if member:
            roles = [r.mention for r in member.roles if r.name != "@everyone"]
            roles_text = "None" if not roles else ", ".join(roles)
            if len(roles_text) > 900:
                roles_text = roles_text[:900] + "…"

        content = message.content or ""
        if len(content) > 900:
            content = content[:900] + "…"

        colour = discord.Colour.orange() if success else discord.Colour.red()

        embed = discord.Embed(
            title="PhraseMute Triggered",
            description=status,
            colour=colour,
        )

        embed.add_field(name="User", value=f"{author.mention}\n`{author}`\n`{author.id}`", inline=True)
        embed.add_field(name="Channel", value=f"{where.mention}", inline=True)
        embed.add_field(name="Deleted", value=deleted_text, inline=True)

        embed.add_field(name="Muted Role", value=role_text, inline=True)
        embed.add_field(name="Account Created", value=created_at, inline=False)
        embed.add_field(name="Joined Server", value=joined_at, inline=False)

        if member:
            embed.add_field(name="Highest Role", value=member.top_role.mention, inline=True)
            embed.add_field(name="Roles", value=roles_text, inline=False)

        embed.add_field(name="Matched Phrase/Pattern", value=f"`{matched.phrase}`", inline=False)
        embed.add_field(name="Message Link", value=f"[Jump to message]({jump})", inline=False)

        if content:
            embed.add_field(name="Content", value=content, inline=False)

        try:
            embed.set_thumbnail(url=author.display_avatar.url)
        except Exception:
            pass

        await log_channel.send(content=ping if ping else None, embed=embed)

    # -------------------------
    # Listener
    # -------------------------

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        if not await self.config.guild(message.guild).enabled():
            return

        if not isinstance(message.author, discord.Member):
            return

        if await self._is_ignored(message.author):
            return

        muted_role = await self._get_muted_role(message.guild)
        if muted_role is None:
            return  # not configured

        matched = await self._match_phrase(message.guild, message.content or "")
        if matched is None:
            return

        # Delete first (reduces exposure)
        delete_trigger = await self.config.guild(message.guild).delete_trigger_message()
        deleted = False
        if delete_trigger:
            try:
                await message.delete()
                deleted = True
            except (discord.Forbidden, discord.HTTPException):
                deleted = False

        # Do not schedule removal of a mute that existed before PhraseMute triggered.
        was_already_muted = muted_role in message.author.roles

        # Always ensure muted (even if we skip logging)
        success = await self._mute_member(message.author, muted_role)
        duration = await self.config.guild(message.guild).mute_duration_seconds()
        if success and not was_already_muted and duration > 0:
            await self._schedule_unmute(message.author, duration)

        # Cooldown: prevent log channel spam
        cooldown = await self.config.guild(message.guild).log_cooldown_seconds()
        if not self._should_log_now(message.guild.id, message.author.id, int(cooldown)):
            return

        await self._log_action(
            message=message,
            matched=matched,
            success=success,
            muted_role=muted_role,
            deleted=deleted,
        )

    # -------------------------
    # Commands
    # -------------------------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def phrasemute(self, ctx: commands.Context):
        """Configure PhraseMute."""
        pass

    @phrasemute.command()
    async def enable(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("✅ PhraseMute enabled.")

    @phrasemute.command()
    async def disable(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("🛑 PhraseMute disabled.")

    @phrasemute.command()
    async def setmuterole(self, ctx: commands.Context, role: discord.Role):
        """
        Set the muted role used for auto-mutes.

        Usage:
        [p]phrasemute setmuterole @Muted
        """
        me = ctx.guild.me
        if me is None:
            return await ctx.send("❌ Couldn't read my guild member object. Try again.")

        if role >= me.top_role:
            return await ctx.send(
                "❌ That muted role is ABOVE (or equal to) my highest role.\n"
                "Move my bot role above the muted role, then try again."
            )

        await self.config.guild(ctx.guild).muted_role_id.set(role.id)
        await ctx.send(f"✅ Muted role set to: {role.mention} (`{role.id}`)")

    @phrasemute.command()
    async def setlogchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"✅ Log channel set to: {channel.mention}")

    @phrasemute.command()
    async def mentionrole(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """Set (or clear) the role to mention on triggers."""
        if role is None:
            await self.config.guild(ctx.guild).mention_role_id.set(None)
            await ctx.send("✅ Mention role cleared.")
            return
        await self.config.guild(ctx.guild).mention_role_id.set(role.id)
        await self.config.guild(ctx.guild).mention_user_id.set(None)  # avoid double pings
        await ctx.send(f"✅ Will mention role: {role.mention}")

    @phrasemute.command()
    async def mentionuser(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """Set (or clear) the user to mention on triggers."""
        if user is None:
            await self.config.guild(ctx.guild).mention_user_id.set(None)
            await ctx.send("✅ Mention user cleared.")
            return
        await self.config.guild(ctx.guild).mention_user_id.set(user.id)
        await self.config.guild(ctx.guild).mention_role_id.set(None)  # avoid double pings
        await ctx.send(f"✅ Will mention user: {user.mention}")

    @phrasemute.command()
    async def matchmode(self, ctx: commands.Context, mode: str):
        """
        Set phrase matching mode:
        - exact: normalized full-message match (safest; best for copy-paste spam)
        - contains: substring match (broader)
        - regex: phrases are regex patterns (advanced)
        """
        mode = mode.lower().strip()
        if mode not in {"exact", "contains", "regex"}:
            return await ctx.send("❌ Mode must be one of: `exact`, `contains`, `regex`.")
        await self.config.guild(ctx.guild).match_mode.set(mode)
        await ctx.send(f"✅ match_mode set to `{mode}`.")

    @phrasemute.command()
    async def deletemessage(self, ctx: commands.Context, value: bool):
        """If true, deletes the triggering message (if the bot has perms)."""
        await self.config.guild(ctx.guild).delete_trigger_message.set(value)
        await ctx.send(f"✅ delete_trigger_message set to `{value}`")

    @phrasemute.command()
    async def logcooldown(self, ctx: commands.Context, seconds: int):
        """
        Set how often the bot is allowed to LOG per user (prevents spam).
        Deletion + mute still happen on every trigger.
        """
        if seconds < 0:
            return await ctx.send("❌ Cooldown must be 0 or greater.")
        await self.config.guild(ctx.guild).log_cooldown_seconds.set(int(seconds))
        await ctx.send(f"✅ Log cooldown set to `{seconds}` seconds per user.")

    @phrasemute.command()
    async def timer(self, ctx: commands.Context, duration: str):
        """Set automatic unmute time, e.g. 30m, 1h, or 1d12h. Use off to disable."""
        if duration.lower().strip() in {"off", "none", "0"}:
            await self.config.guild(ctx.guild).mute_duration_seconds.set(0)
            return await ctx.send("✅ PhraseMute timer disabled; future mutes will be permanent.")

        seconds = self._parse_duration(duration)
        if seconds is None or seconds <= 0:
            return await ctx.send("❌ Invalid duration. Use formats such as `30m`, `1h`, or `1d12h`.")

        await self.config.guild(ctx.guild).mute_duration_seconds.set(seconds)
        await ctx.send(f"✅ PhraseMute timer set to `{self._format_duration(seconds)}`.")

    @phrasemute.command()
    async def ignoreadmins(self, ctx: commands.Context, value: bool):
        await self.config.guild(ctx.guild).ignore_admins.set(value)
        await ctx.send(f"✅ ignore_admins set to `{value}`")

    @phrasemute.command()
    async def ignoremods(self, ctx: commands.Context, value: bool):
        await self.config.guild(ctx.guild).ignore_mods.set(value)
        await ctx.send(f"✅ ignore_mods set to `{value}`")

    @phrasemute.command()
    async def add(self, ctx: commands.Context, *, phrase: str):
        """Add a phrase/pattern to the list."""
        phrase = phrase.strip()
        if not phrase:
            return await ctx.send("❌ Phrase cannot be empty.")

        phrases = await self.config.guild(ctx.guild).phrases()
        if phrase in phrases:
            return await ctx.send("ℹ️ That phrase is already in the list.")

        phrases.append(phrase)
        await self.config.guild(ctx.guild).phrases.set(phrases)
        await ctx.send("✅ Added phrase/pattern.")

    @phrasemute.command()
    async def remove(self, ctx: commands.Context, *, phrase: str):
        """Remove a phrase/pattern from the list (exact stored match)."""
        phrase = phrase.strip()
        phrases = await self.config.guild(ctx.guild).phrases()
        if phrase not in phrases:
            return await ctx.send("❌ That phrase is not in the list (must match exactly as stored).")

        phrases.remove(phrase)
        await self.config.guild(ctx.guild).phrases.set(phrases)
        await ctx.send("✅ Removed.")

    @phrasemute.command(name="list")
    async def list_phrases(self, ctx: commands.Context):
        """List configured phrases/patterns."""
        phrases = await self.config.guild(ctx.guild).phrases()
        if not phrases:
            return await ctx.send("No phrases configured.")

        lines = [f"{i}. `{p}`" for i, p in enumerate(phrases, start=1)]
        text = "\n".join(lines)
        if len(text) > 1800:
            text = text[:1800] + "\n…(truncated)"
        await ctx.send(text)

    @phrasemute.command()
    async def clear(self, ctx: commands.Context):
        """Clear all phrases."""
        await self.config.guild(ctx.guild).phrases.set([])
        await ctx.send("✅ Cleared all phrases.")

    @phrasemute.command()
    async def settings(self, ctx: commands.Context):
        """Show current settings."""
        g = self.config.guild(ctx.guild)

        enabled = await g.enabled()
        muted_role_id = await g.muted_role_id()
        log_channel_id = await g.log_channel_id()
        mention_role_id = await g.mention_role_id()
        mention_user_id = await g.mention_user_id()
        delete_trigger = await g.delete_trigger_message()
        match_mode = await g.match_mode()
        ignore_admins = await g.ignore_admins()
        ignore_mods = await g.ignore_mods()
        cooldown = await g.log_cooldown_seconds()
        mute_duration = await g.mute_duration_seconds()
        phrases = await g.phrases()

        muted_role = ctx.guild.get_role(muted_role_id) if muted_role_id else None
        log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None
        mention_role = ctx.guild.get_role(mention_role_id) if mention_role_id else None
        mention_user = ctx.guild.get_member(mention_user_id) if mention_user_id else None

        embed = discord.Embed(title="PhraseMute Settings", colour=discord.Colour.blurple())
        embed.add_field(name="Enabled", value=str(enabled), inline=True)
        embed.add_field(name="Match mode", value=str(match_mode), inline=True)
        embed.add_field(name="Log cooldown", value=f"{cooldown}s", inline=True)
        embed.add_field(
            name="Mute timer",
            value=self._format_duration(mute_duration) if mute_duration else "Permanent",
            inline=True,
        )
        embed.add_field(name="Muted role", value=muted_role.mention if muted_role else "Not set", inline=False)
        embed.add_field(
            name="Log channel",
            value=log_channel.mention if isinstance(log_channel, discord.abc.GuildChannel) else "Not set",
            inline=False,
        )
        embed.add_field(
            name="Mention",
            value=(mention_role.mention if mention_role else (mention_user.mention if mention_user else "None")),
            inline=False,
        )
        embed.add_field(name="Delete trigger message", value=str(delete_trigger), inline=True)
        embed.add_field(name="Ignore admins", value=str(ignore_admins), inline=True)
        embed.add_field(name="Ignore mods", value=str(ignore_mods), inline=True)
        embed.add_field(name="Phrase count", value=str(len(phrases)), inline=True)

        await ctx.send(embed=embed)
