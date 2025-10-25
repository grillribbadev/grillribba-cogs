from __future__ import annotations
import discord
import logging
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger("red.reactspy")

DEFAULTS_GUILD = {
    "watch_channel_id": None,
    "log_channel_id": None
}

class ReactSpy(commands.Cog):
    """Tracks reaction adds/removes in a specific channel and logs them elsewhere."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2025102402, force_registration=True)
        self.config.register_guild(**DEFAULTS_GUILD)

    def format_help_for_context(self, ctx: commands.Context) -> str:
        return "Tracks reactions in a set channel and logs them elsewhere."

    @commands.group(name="reactspy", invoke_without_command=True)
    @commands.admin()
    async def reactspy(self, ctx: commands.Context):
        """Reaction Spy configuration."""
        data = await self.config.guild(ctx.guild).all()
        watch = f"<#{data['watch_channel_id']}>" if data["watch_channel_id"] else "Not set"
        logch = f"<#{data['log_channel_id']}>" if data["log_channel_id"] else "Not set"
        await ctx.send(f"📡 Watching: {watch}\n🪵 Logging to: {logch}")

    @reactspy.command(name="setwatch")
    @commands.admin()
    async def reactspy_set_watch(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel to monitor for reactions."""
        await self.config.guild(ctx.guild).watch_channel_id.set(channel.id)
        await ctx.send(f"📌 Now watching reactions in: {channel.mention}")

    @reactspy.command(name="setlog")
    @commands.admin()
    async def reactspy_set_log(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel to log reaction events to."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"🪵 Reaction logs will be sent to: {channel.mention}")

    @reactspy.command(name="off")
    @commands.admin()
    async def reactspy_off(self, ctx: commands.Context):
        """Disable reaction spying."""
        await self.config.guild(ctx.guild).clear()
        await ctx.send("❌ Reaction spying disabled.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_react_event(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_react_event(payload, added=False)

    async def _handle_react_event(self, payload: discord.RawReactionActionEvent, added: bool):
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        config = await self.config.guild(guild).all()
        watch_channel_id = config.get("watch_channel_id")
        log_channel_id = config.get("log_channel_id")

        if payload.channel_id != watch_channel_id:
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return

        emoji = str(payload.emoji)
        verb = "reacted with" if added else "removed reaction"

        try:
            channel = guild.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        log_channel = guild.get_channel(log_channel_id) if log_channel_id else channel

        try:
            await log_channel.send(
                f"🔍 **{user.mention}** {verb} {emoji} on [this message]({message.jump_url})",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as e:
            log.warning("Failed to send reaction log: %s", e)
