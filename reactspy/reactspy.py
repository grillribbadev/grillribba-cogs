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
    "spam_threshold": {"count": 5, "interval": 10},  # N reactions in X seconds
}

class ReactSpy(commands.Cog):
    """Watches a channel for spammy reaction activity and logs only when spam is detected."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xA1B2C3D4E5F6, force_registration=True)
        self.config.register_guild(**DEFAULTS_GUILD)
        # global per-user history
        self._reaction_history: Dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=128))

    # ---------------- Config commands ----------------
    @commands.group(name="reactspy", invoke_without_command=True)
    @commands.admin()
    async def reactspy(self, ctx: commands.Context):
        """View current ReactSpy settings."""
        data = await self.config.guild(ctx.guild).all()
        spam = data["spam_threshold"]
        await ctx.send(
            f"👁 Watching: <#{data['watch_channel_id']}>\n"
            f"🪵 Logging to: <#{data['log_channel_id']}>\n"
            f"⚠️ Spam threshold: **{spam['count']}** reactions in **{spam['interval']}s**"
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
        """Set the log channel for spam alerts."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"🪵 Spam alerts will be sent to: {channel.mention}")

    @reactspy.command(name="spamthreshold")
    @commands.admin()
    async def set_spam_threshold(self, ctx: commands.Context, count: int, seconds: int):
        """Set spam detection: N reactions in X seconds (global per user)."""
        if count < 1 or seconds < 1:
            return await ctx.send("❌ Both values must be >= 1.")
        await self.config.guild(ctx.guild).spam_threshold.set({"count": count, "interval": seconds})
        await ctx.send(f"⚠️ Spam threshold set: **{count}** reactions in **{seconds}s** (across messages)")

    @reactspy.command(name="off")
    @commands.admin()
    async def disable_spy(self, ctx: commands.Context):
        """Disable reaction spy (clears config)."""
        await self.config.guild(ctx.guild).clear()
        await ctx.send("❌ Reaction spying disabled and config cleared.")

    # ---------------- Listeners ----------------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent):
        # Ignore DMs or guildless events
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        cfg = await self.config.guild(guild).all()
        watch_channel_id = cfg.get("watch_channel_id")
        if payload.channel_id != watch_channel_id or not watch_channel_id:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        # track per-user globally
        key = payload.user_id
        now = time.time()
        self._reaction_history[key].append(now)

        spam_cfg = cfg["spam_threshold"]
        count = int(spam_cfg["count"])
        interval = int(spam_cfg["interval"])

        recent = [t for t in self._reaction_history[key] if now - t <= interval]

        if len(recent) >= count:
            # get message link if possible
            jump_url = None
            try:
                channel = guild.get_channel(payload.channel_id)
                msg = await channel.fetch_message(payload.message_id)
                jump_url = msg.jump_url
            except Exception:
                pass

            # choose where to log
            log_channel = guild.get_channel(cfg["log_channel_id"]) or guild.get_channel(watch_channel_id)
            if not log_channel:
                return

            embed = discord.Embed(
                title="🚨 Reaction Spam Detected",
                description=(
                    f"**{member.mention}** reacted {len(recent)} times in {interval}s.\n"
                    + (f"[Jump to message]({jump_url})" if jump_url else "")
                ),
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"User ID: {member.id}")
            try:
                await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:
                log.exception("Failed to send spam embed: %s", e)

            # reset that user's counter
            self._reaction_history[key].clear()
