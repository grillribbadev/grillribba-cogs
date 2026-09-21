from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


log = logging.getLogger("red.selfreactshit")


DEFAULT_GUILD = {
    "enabled": False,
    "channel_id": None,
    "mute_role_id": None,
    "ignored_role_ids": [],
    "sob_watch_member_ids": [],
    "duration_seconds": 600,
    "embed_title": "Self-reaction mute",
    "embed_description": "{user_mention} was muted for reacting to their own message.",
    "embed_color": 0xCC3333,
    "active_mutes": {},
}

DURATION_RE = re.compile(r"^(\d+)\s*(s|m|h|d)$", re.IGNORECASE)


def parse_duration(value: str) -> Optional[int]:
    match = DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = amount * multiplier
    return seconds if 1 <= seconds <= 30 * 86400 else None


def format_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


class SelfReactMute(commands.Cog):
    """Mute members who add a reaction to their own message."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7391846205, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self._expiry_task: Optional[asyncio.Task] = None
        self._processing: set[tuple[int, int]] = set()

    async def cog_load(self) -> None:
        self._expiry_task = asyncio.create_task(self._expiry_loop())

    def cog_unload(self) -> None:
        if self._expiry_task:
            self._expiry_task.cancel()

    @staticmethod
    def _render(value: str, member: discord.Member, duration: int, channel: discord.abc.GuildChannel) -> str:
        replacements = {
            "{user}": str(member),
            "{user_mention}": member.mention,
            "{duration}": format_duration(duration),
            "{channel}": getattr(channel, "mention", "the channel"),
        }
        for key, replacement in replacements.items():
            value = value.replace(key, replacement)
        return value

    async def _get_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role_id = await self.config.guild(guild).mute_role_id()
        return guild.get_role(role_id) if role_id else None

    async def _expiry_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            now = int(time.time())
            for guild in list(self.bot.guilds):
                try:
                    active = await self.config.guild(guild).active_mutes()
                    changed = False
                    role = await self._get_role(guild)
                    if not role:
                        continue
                    for member_id, expires_at in list(active.items()):
                        if int(expires_at) > now:
                            continue
                        member = guild.get_member(int(member_id))
                        if member and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="Self-reaction mute expired")
                            except discord.HTTPException:
                                continue
                        active.pop(member_id, None)
                        changed = True
                    if changed:
                        await self.config.guild(guild).active_mutes.set(active)
                except Exception:
                    continue
            await asyncio.sleep(5)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        conf = await self.config.guild(guild).all()
        key = (payload.guild_id, payload.message_id)
        if key in self._processing:
            return
        self._processing.add(key)
        try:
            channel = guild.get_channel(payload.channel_id)
            if not channel or not hasattr(channel, "fetch_message"):
                return
            message = await channel.fetch_message(payload.message_id)

            # This cleanup is independent of self-reaction mute being enabled.
            watched_member_ids = {int(member_id) for member_id in conf.get("sob_watch_member_ids", [])}
            is_sob = payload.emoji.id is None and payload.emoji.name in {"sob", "😭"}
            if is_sob and message.author.id in watched_member_ids:
                try:
                    await message.clear_reaction(payload.emoji)
                except discord.Forbidden:
                    log.warning(
                        "Cannot clear :sob: reaction in guild %s; Manage Messages is required.",
                        guild.id,
                    )
                except discord.HTTPException:
                    log.exception("Failed to clear :sob: reaction in guild %s", guild.id)
                return

            configured_channel_id = conf.get("channel_id")
            if configured_channel_id and payload.channel_id != configured_channel_id:
                return
            if not conf.get("enabled") or not conf.get("mute_role_id"):
                return
            if self.bot.user and payload.user_id == self.bot.user.id:
                return
            if message.author.id != payload.user_id:
                return
            member = guild.get_member(payload.user_id)
            if not member:
                try:
                    member = await guild.fetch_member(payload.user_id)
                except discord.HTTPException:
                    return
            ignored_role_ids = {int(role_id) for role_id in conf.get("ignored_role_ids", [])}
            if any(role.id in ignored_role_ids for role in member.roles):
                return
            role = guild.get_role(conf["mute_role_id"])
            bot_member = guild.me
            if not role or not bot_member or role >= bot_member.top_role or member == guild.owner:
                return
            try:
                await member.add_roles(role, reason="Reacted to own message")
            except discord.Forbidden:
                return
            duration = int(conf.get("duration_seconds") or 600)
            active = conf.get("active_mutes") or {}
            active[str(member.id)] = int(time.time()) + duration
            await self.config.guild(guild).active_mutes.set(active)
            embed = discord.Embed(
                title=self._render(conf.get("embed_title") or "Self-reaction mute", member, duration, channel),
                description=self._render(
                    conf.get("embed_description") or "{user_mention} was muted.",
                    member,
                    duration,
                    channel,
                ),
                color=int(conf.get("embed_color") or 0xCC3333),
            )
            embed.set_footer(text=f"Duration: {format_duration(duration)}")
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
        finally:
            self._processing.discard(key)

    @commands.hybrid_group(name="selfreact", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def selfreact(self, ctx: commands.Context) -> None:
        await self._send_config(ctx)

    @selfreact.command(name="enable")
    async def selfreact_enable(self, ctx: commands.Context, enabled: Optional[bool] = None) -> None:
        current = await self.config.guild(ctx.guild).enabled()
        value = not current if enabled is None else enabled
        await self.config.guild(ctx.guild).enabled.set(value)
        await ctx.reply(f"Self-reaction mute **{'enabled' if value else 'disabled'}**.")

    @selfreact.command(name="role")
    async def selfreact_role(self, ctx: commands.Context, role: discord.Role) -> None:
        await self.config.guild(ctx.guild).mute_role_id.set(role.id)
        await ctx.reply(f"Mute role set to {role.mention}.")

    @selfreact.group(name="ignore", invoke_without_command=True)
    async def selfreact_ignore(self, ctx: commands.Context) -> None:
        await self.selfreact_ignore_list(ctx)

    @selfreact_ignore.command(name="add")
    async def selfreact_ignore_add(self, ctx: commands.Context, role: discord.Role) -> None:
        ignored = await self.config.guild(ctx.guild).ignored_role_ids()
        if role.id not in ignored:
            ignored.append(role.id)
            await self.config.guild(ctx.guild).ignored_role_ids.set(ignored)
        await ctx.reply(f"Members with {role.mention} will be ignored by self-reaction mutes.")

    @selfreact_ignore.command(name="remove")
    async def selfreact_ignore_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        ignored = await self.config.guild(ctx.guild).ignored_role_ids()
        if role.id in ignored:
            ignored.remove(role.id)
            await self.config.guild(ctx.guild).ignored_role_ids.set(ignored)
        await ctx.reply(f"{role.mention} is no longer ignored by self-reaction mutes.")

    @selfreact_ignore.command(name="clear")
    async def selfreact_ignore_clear(self, ctx: commands.Context) -> None:
        await self.config.guild(ctx.guild).ignored_role_ids.set([])
        await ctx.reply("Cleared all ignored roles.")

    @selfreact_ignore.command(name="list")
    async def selfreact_ignore_list(self, ctx: commands.Context) -> None:
        ignored = await self.config.guild(ctx.guild).ignored_role_ids()
        roles = [ctx.guild.get_role(role_id) for role_id in ignored]
        mentions = [role.mention for role in roles if role]
        await ctx.reply("Ignored roles: " + (", ".join(mentions) if mentions else "none"))

    @selfreact.group(name="sob", invoke_without_command=True)
    async def selfreact_sob(self, ctx: commands.Context) -> None:
        await self.selfreact_sob_list(ctx)

    @selfreact_sob.command(name="add")
    async def selfreact_sob_add(self, ctx: commands.Context, member: discord.Member) -> None:
        watched = await self.config.guild(ctx.guild).sob_watch_member_ids()
        if member.id not in watched:
            watched.append(member.id)
            await self.config.guild(ctx.guild).sob_watch_member_ids.set(watched)
        await ctx.reply(f"I will silently clear `:sob:` reactions from {member.mention}'s messages.")

    @selfreact_sob.command(name="remove")
    async def selfreact_sob_remove(self, ctx: commands.Context, member: discord.Member) -> None:
        watched = await self.config.guild(ctx.guild).sob_watch_member_ids()
        if member.id in watched:
            watched.remove(member.id)
            await self.config.guild(ctx.guild).sob_watch_member_ids.set(watched)
        await ctx.reply(f"I will no longer clear `:sob:` reactions from {member.mention}'s messages.")

    @selfreact_sob.command(name="clear")
    async def selfreact_sob_clear(self, ctx: commands.Context) -> None:
        await self.config.guild(ctx.guild).sob_watch_member_ids.set([])
        await ctx.reply("Cleared the `:sob:` reaction watch list.")

    @selfreact_sob.command(name="list")
    async def selfreact_sob_list(self, ctx: commands.Context) -> None:
        watched = await self.config.guild(ctx.guild).sob_watch_member_ids()
        members = [ctx.guild.get_member(member_id) for member_id in watched]
        mentions = [member.mention for member in members if member]
        await ctx.reply("`:sob:` watch list: " + (", ".join(mentions) if mentions else "empty"))

    @selfreact.command(name="channel")
    async def selfreact_channel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ) -> None:
        if channel is None:
            await self.config.guild(ctx.guild).channel_id.set(None)
            return await ctx.reply("Self-reaction listening is enabled in **all channels**.")
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.reply(f"Self-reaction listening channel set to {channel.mention}.")

    @selfreact.command(name="duration")
    async def selfreact_duration(self, ctx: commands.Context, duration: str) -> None:
        seconds = parse_duration(duration)
        if seconds is None:
            return await ctx.reply("Use a duration from `1s` to `30d`, such as `10m`, `2h`, or `1d`.")
        await self.config.guild(ctx.guild).duration_seconds.set(seconds)
        await ctx.reply(f"Mute duration set to **{format_duration(seconds)}**.")

    @selfreact.command(name="title")
    async def selfreact_title(self, ctx: commands.Context, *, title: str) -> None:
        await self.config.guild(ctx.guild).embed_title.set(title[:256])
        await ctx.reply("Mute embed title updated.")

    @selfreact.command(name="message")
    async def selfreact_message(self, ctx: commands.Context, *, description: str) -> None:
        await self.config.guild(ctx.guild).embed_description.set(description[:4000])
        await ctx.reply("Mute embed message updated.")

    @selfreact.command(name="color")
    async def selfreact_color(self, ctx: commands.Context, color: str) -> None:
        try:
            value = int(color.strip().lstrip("#"), 16)
            if not 0 <= value <= 0xFFFFFF:
                raise ValueError
        except ValueError:
            return await ctx.reply("Use a hex color such as `#CC3333`.")
        await self.config.guild(ctx.guild).embed_color.set(value)
        await ctx.reply(f"Mute embed color set to `#{value:06X}`.")

    @selfreact.command(name="show")
    async def selfreact_show(self, ctx: commands.Context) -> None:
        await self._send_config(ctx)

    async def _send_config(self, ctx: commands.Context) -> None:
        conf = await self.config.guild(ctx.guild).all()
        role = ctx.guild.get_role(conf.get("mute_role_id")) if conf.get("mute_role_id") else None
        channel = ctx.guild.get_channel(conf.get("channel_id")) if conf.get("channel_id") else None
        ignored = [
            ctx.guild.get_role(role_id)
            for role_id in conf.get("ignored_role_ids", [])
        ]
        ignored_mentions = [role.mention for role in ignored if role]
        watched_ids = conf.get("sob_watch_member_ids", [])
        watched_members = [ctx.guild.get_member(member_id) for member_id in watched_ids]
        watched_mentions = [member.mention for member in watched_members if member]
        await ctx.reply(
            f"Enabled: **{'yes' if conf.get('enabled') else 'no'}**\n"
            f"Listen channel: {channel.mention if channel else '**all channels**'}\n"
            f"Mute role: {role.mention if role else '**not configured**'}\n"
            f"Ignored roles: {', '.join(ignored_mentions) if ignored_mentions else '**none**'}\n"
            f"`:sob:` watch list: {', '.join(watched_mentions) if watched_mentions else '**empty**'}\n"
            f"Duration: **{format_duration(int(conf.get('duration_seconds') or 600))}**\n"
            f"Title: {conf.get('embed_title') or '—'}\n"
            f"Message: {conf.get('embed_description') or '—'}\n"
            "Placeholders: `{user}`, `{user_mention}`, `{duration}`, `{channel}`"
        )


async def setup(bot: Red) -> None:
    await bot.add_cog(SelfReactMute(bot))
