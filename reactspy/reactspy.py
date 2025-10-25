from __future__ import annotations
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger(__name__)

DEFAULTS_GUILD = {
    "watch_channel_id": None,
    "log_channel_id": None,
    # Spam threshold: N reactions in X seconds -> flagged
    "spam_threshold": {"count": 5, "interval": 10},
}

class ReactSpy(commands.Cog):
    """Track reactions in a channel, log them in embeds, detect global per-user spam."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xA1B2C3D4E5F6, force_registration=True)
        self.config.register_guild(**DEFAULTS_GUILD)
        # per-user reaction timestamps (global across messages)
        self._reaction_history: Dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=128))

    # ----------------- Config commands -----------------
    @commands.group(name="reactspy", invoke_without_command=True)
    @commands.admin()
    async def reactspy(self, ctx: commands.Context):
        """View ReactSpy settings."""
        data = await self.config.guild(ctx.guild).all()
        watch = f"<#{data['watch_channel_id']}>" if data.get("watch_channel_id") else "Not set"
        logch = f"<#{data['log_channel_id']}>" if data.get("log_channel_id") else "Not set"
        spam = data.get("spam_threshold", {})
        await ctx.send(
            f"👁 Watching: {watch}\n"
            f"🪵 Logging to: {logch}\n"
            f"⚠️ Spam threshold: **{spam.get('count',5)}** reactions in **{spam.get('interval',10)}s**"
        )

    @reactspy.command(name="setwatch")
    @commands.admin()
    async def set_watch(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel to monitor for reactions."""
        await self.config.guild(ctx.guild).watch_channel_id.set(channel.id)
        await ctx.send(f"✅ Now watching reactions in: {channel.mention}")

    @reactspy.command(name="setlog")
    @commands.admin()
    async def set_log(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel to receive reaction logs and spam alerts."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"🪵 Reaction logs will be sent to: {channel.mention}")

    @reactspy.command(name="spamthreshold")
    @commands.admin()
    async def set_spam_threshold(self, ctx: commands.Context, count: int, seconds: int):
        """Set spam detection threshold: N reactions in X seconds (global per user)."""
        if count < 1 or seconds < 1:
            return await ctx.send("❌ Both values must be >= 1.")
        await self.config.guild(ctx.guild).spam_threshold.set({"count": count, "interval": seconds})
        await ctx.send(f"⚠️ Spam threshold set: **{count}** reactions in **{seconds}s** (counts across messages)")

    @reactspy.command(name="off")
    @commands.admin()
    async def disable_spy(self, ctx: commands.Context):
        """Disable reaction spying for this guild (clears config)."""
        await self.config.guild(ctx.guild).set_raw("watch_channel_id", value=None)
        await self.config.guild(ctx.guild).set_raw("log_channel_id", value=None)
        await self.config.guild(ctx.guild).set_raw("spam_threshold", value=DEFAULTS_GUILD["spam_threshold"])
        await ctx.send("❌ Reaction spying disabled and config cleared (kept defaults).")

    # ----------------- Listeners -----------------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, added=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, added: bool):
        # Ignore DMs, guildless events
        if payload.guild_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        cfg = await self.config.guild(guild).all()
        watch_channel_id = cfg.get("watch_channel_id")
        log_channel_id = cfg.get("log_channel_id")

        # Only watch the configured channel
        if watch_channel_id is None or payload.channel_id != watch_channel_id:
            return

        # Ignore bots
        if payload.user_id is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        # Attempt to fetch message for jump link (may fail if deleted)
        try:
            channel = guild.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            jump = message.jump_url
        except Exception:
            jump = None

        # Global per-user key
        key = payload.user_id
        now = time.time()
        self._reaction_history[key].append(now)

        spam_cfg = cfg.get("spam_threshold", {"count": 5, "interval": 10})
        count = int(spam_cfg.get("count", 5))
        interval = int(spam_cfg.get("interval", 10))

        # Count timestamps within interval
        timestamps = self._reaction_history[key]
        recent = [t for t in timestamps if now - t <= interval]

        # Determine log channel (fallback to watched channel)
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else (guild.get_channel(watch_channel_id) if guild.get_channel(watch_channel_id) else None)
        if log_channel is None:
            # No channel to log to
            return

        if len(recent) >= count:
            # Spam detected: send a single spam embed and clear that user's history
            embed = discord.Embed(
                title="🚨 Reaction Spam Detected",
                description=f"**{member.mention}** triggered spam detection: **{len(recent)}** reactions within **{interval}s**.",
                color=discord.Color.red(),
            )
            if jump:
                embed.add_field(name="Jump", value=f"[Click to message]({jump})", inline=False)
            embed.set_footer(text=f"User ID: {member.id}")
            try:
                await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:
                log.exception("Failed to send spam embed: %s", e)
            # reset history for this user so repeated spam requires re-triggering
            self._reaction_history[key].clear()
            return

        # Normal logging embed for each reaction add/remove
        verb = "reacted with" if added else "removed reaction"
        emoji = str(payload.emoji)
        embed = discord.Embed(
            title="Reaction Logged",
            description=f"**{member.mention}** {verb} {emoji}.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        if jump:
            embed.add_field(name="Message", value=f"[Jump to message]({jump})", inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        try:
            await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            log.exception("Failed to send log embed: %s", e)
