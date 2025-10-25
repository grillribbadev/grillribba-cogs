from __future__ import annotations
import discord
import logging
import time
from collections import defaultdict, deque
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.reactspy")

DEFAULTS_GUILD = {
    "watch_channel_id": None,
    "log_channel_id": None,
    "spam_threshold": {"count": 5, "interval": 10},
}

class ReactSpy(commands.Cog):
    """Tracks and logs reactions in a channel; filters spam; uses embeds."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2025102502, force_registration=True)
        self.config.register_guild(**DEFAULTS_GUILD)
        self._reaction_history = defaultdict(lambda: deque(maxlen=20))  # (user_id, message_id) -> timestamps

    @commands.group(name="reactspy", invoke_without_command=True)
    @commands.admin()
    async def reactspy(self, ctx: commands.Context):
        """Show current ReactSpy settings."""
        data = await self.config.guild(ctx.guild).all()
        spam = data["spam_threshold"]
        await ctx.send(
            f"👁 Watching: <#{data['watch_channel_id']}>\n"
            f"🪵 Logging to: <#{data['log_channel_id']}>\n"
            f"⚠️ Spam if {spam['count']} reactions in {spam['interval']}s"
        )

    @reactspy.command(name="setwatch")
    @commands.admin()
    async def set_watch(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).watch_channel_id.set(channel.id)
        await ctx.send(f"✅ Now watching reactions in: {channel.mention}")

    @reactspy.command(name="setlog")
    @commands.admin()
    async def set_log(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"🪵 Reaction logs will go to: {channel.mention}")

    @reactspy.command(name="off")
    @commands.admin()
    async def disable_spy(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).clear()
        await ctx.send("❌ Reaction spy disabled.")

    @reactspy.command(name="spamthreshold")
    @commands.admin()
    async def set_spam_threshold(self, ctx: commands.Context, count: int, seconds: int):
        """Set spam detection: N reactions in X seconds."""
        if count < 1 or seconds < 1:
            return await ctx.send("❌ Must be at least 1 reaction and 1 second.")
        await self.config.guild(ctx.guild).spam_threshold.set({"count": count, "interval": seconds})
        await ctx.send(f"⚠️ Spam threshold set to **{count}** reactions in **{seconds}**s")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle(payload, added=False)

    async def _handle(self, payload: discord.RawReactionActionEvent, added: bool):
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        cfg = await self.config.guild(guild).all()
        if payload.channel_id != cfg["watch_channel_id"]:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        try:
            channel = guild.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        log_channel = guild.get_channel(cfg["log_channel_id"]) or channel

        # Track reactions for spam detection
        key = (payload.user_id, payload.message_id)
        now = time.time()
        self._reaction_history[key].append(now)

        timestamps = self._reaction_history[key]
        count = cfg["spam_threshold"]["count"]
        interval = cfg["spam_threshold"]["interval"]
        recent = [t for t in timestamps if now - t <= interval]

        if len(recent) >= count:
            # SPAM DETECTED
            embed = discord.Embed(
                title="🚨 Reaction Spam Detected",
                description=f"**{member.mention}** spammed **{len(recent)}** reactions in **{interval}s**\n[Jump to message]({message.jump_url})",
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"User ID: {member.id}")
            await log_channel.send(embed=embed)
            self._reaction_history[key].clear()
            return  # Don't send normal log

        # Normal log
        verb = "reacted with" if added else "removed reaction"
        emoji = str(payload.emoji)
        embed = discord.Embed(
            title="Reaction Logged",
            description=f"**{member.mention}** {verb} {emoji} on [this message]({message.jump_url})",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"User ID: {member.id}")
        try:
            await log_channel.send(embed=embed)
        except Exception as e:
            log.warning("Failed to send log embed: %s", e)
