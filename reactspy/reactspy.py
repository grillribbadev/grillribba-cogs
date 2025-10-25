from __future__ import annotations
import logging
import time
import asyncio
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.reactspy")

DEFAULTS_GUILD = {
    "watch_channel_id": None,
    "log_channel_id": None,
    "spam_threshold": {"count": 5, "interval": 10},
}

# Hardcoded delay and cooldown settings (per user)
SPAM_LOG_DELAY_SEC = 3    # Delay after threshold is hit
SPAM_LOG_COOLDOWN_SEC = 30  # Per-user cooldown between logs

class ReactSpy(commands.Cog):
    """Logs only spammy reactions with per-user delay + cooldown (global)."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xFA51C0DE, force_registration=True)
        self.config.register_guild(**DEFAULTS_GUILD)

        self._reaction_history: Dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=128))
        self._cooldowns: Dict[int, float] = {}  # user_id -> timestamp
        self._pending_logs: Dict[int, asyncio.Task] = {}  # debounce log tasks

    @commands.group(name="reactspy", invoke_without_command=True)
    @commands.admin()
    async def reactspy(self, ctx: commands.Context):
        """Show ReactSpy settings."""
        data = await self.config.guild(ctx.guild).all()
        spam = data["spam_threshold"]
        await ctx.send(
            f"👁 Watching: <#{data['watch_channel_id']}>\n"
            f"🪵 Logging to: <#{data['log_channel_id']}>\n"
            f"⚠️ Spam: **{spam['count']}** reactions in **{spam['interval']}s**\n"
            f"⏱️ Log delay: {SPAM_LOG_DELAY_SEC}s • Cooldown: {SPAM_LOG_COOLDOWN_SEC}s (per user)"
        )

    @reactspy.command(name="setwatch")
    @commands.admin()
    async def set_watch(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).watch_channel_id.set(channel.id)
        await ctx.send(f"✅ Now watching: {channel.mention}")

    @reactspy.command(name="setlog")
    @commands.admin()
    async def set_log(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"🪵 Logs will go to: {channel.mention}")

    @reactspy.command(name="spamthreshold")
    @commands.admin()
    async def set_threshold(self, ctx: commands.Context, count: int, seconds: int):
        if count < 1 or seconds < 1:
            return await ctx.send("Both values must be ≥ 1.")
        await self.config.guild(ctx.guild).spam_threshold.set({"count": count, "interval": seconds})
        await ctx.send(f"⚠️ Spam = {count} reactions in {seconds}s.")

    @reactspy.command(name="off")
    @commands.admin()
    async def disable(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).clear()
        await ctx.send("❌ ReactSpy disabled.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload)

    async def _handle(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        cfg = await self.config.guild(guild).all()
        if payload.channel_id != cfg["watch_channel_id"]:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        now = time.time()
        self._reaction_history[member.id].append(now)

        spam_cfg = cfg["spam_threshold"]
        count = spam_cfg["count"]
        interval = spam_cfg["interval"]

        recent = [t for t in self._reaction_history[member.id] if now - t <= interval]

        if len(recent) >= count:
            if member.id in self._pending_logs:
                return  # debounce: already waiting to log this user

            if now < self._cooldowns.get(member.id, 0):
                return  # per-user cooldown

            # Start delay task
            task = asyncio.create_task(self._delayed_log_if_still_spamming(member, payload, guild, cfg))
            self._pending_logs[member.id] = task

    async def _delayed_log_if_still_spamming(
        self,
        member: discord.Member,
        payload: discord.RawReactionActionEvent,
        guild: discord.Guild,
        cfg: dict,
    ):
        await asyncio.sleep(SPAM_LOG_DELAY_SEC)

        now = time.time()
        timestamps = self._reaction_history[member.id]
        spam_cfg = cfg["spam_threshold"]
        interval = spam_cfg["interval"]
        recent = [t for t in timestamps if now - t <= interval]

        if len(recent) < spam_cfg["count"]:
            # They slowed down
            self._pending_logs.pop(member.id, None)
            return

        # Still spamming — log it
        jump_url = None
        try:
            channel = guild.get_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
            jump_url = msg.jump_url
        except Exception:
            pass

        log_channel = guild.get_channel(cfg["log_channel_id"]) or guild.get_channel(cfg["watch_channel_id"])
        if log_channel:
            embed = discord.Embed(
                title="🚨 Reaction Spam Detected",
                description=f"**{member.mention}** reacted {len(recent)} times in {interval}s.",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            if jump_url:
                embed.add_field(name="Message", value=f"[Jump to message]({jump_url})", inline=False)
            embed.set_footer(text=f"User ID: {member.id}")
            try:
                await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:
                log.exception("Failed to send spam log: %s", e)

        self._reaction_history[member.id].clear()
        self._cooldowns[member.id] = time.time() + SPAM_LOG_COOLDOWN_SEC
        self._pending_logs.pop(member.id, None)
