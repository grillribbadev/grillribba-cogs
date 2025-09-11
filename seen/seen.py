from redbot.core import commands, Config, checks
import discord
import time

class Seen(commands.Cog):
    """Track last known user activity across the server."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8217509211, force_registration=True)

        # Guild-level config: toggles
        self.config.register_guild(
            track_reactions=False,
            track_typing=False,
            track_voice=False
        )

        # User-level config: seen data
        self.config.register_user(
            last_seen=0,
            last_channel=None,
            activity_type="message"
        )

    def _now(self) -> int:
        return int(time.time())

    async def _update_seen(self, user: discord.User, channel: discord.abc.GuildChannel, activity_type: str):
        # Save last activity info (persisted in Config)
        await self.config.user(user).set({
            "last_seen": self._now(),
            "last_channel": channel.id,
            "activity_type": activity_type
        })

    # ------------------------
    #       LISTENERS
    # ------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild and not message.author.bot:
            await self._update_seen(message.author, message.channel, "message")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if after.guild and not after.author.bot:
            await self._update_seen(after.author, after.channel, "edit")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot or not reaction.message.guild:
            return
        conf = await self.config.guild(reaction.message.guild).track_reactions()
        if conf:
            await self._update_seen(user, reaction.message.channel, "reaction")

    @commands.Cog.listener()
    async def on_typing(self, channel, user, when):
        if user.bot or not channel.guild:
            return
        conf = await self.config.guild(channel.guild).track_typing()
        if conf:
            await self._update_seen(user, channel, "typing")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or not member.guild:
            return
        conf = await self.config.guild(member.guild).track_voice()
        if conf and (before.channel != after.channel):
            chan = after.channel or before.channel
            if chan:
                await self._update_seen(member, chan, "voice")

    # ------------------------
    #      SEEN COMMAND
    # ------------------------

    @commands.guild_only()
    @commands.command(name="seen")
    async def seen(self, ctx: commands.Context, member: discord.Member):
        """Check when someone was last active in the server."""
        data = await self.config.user(member).all()
        ts = data.get("last_seen")

        if not ts:
            return await ctx.send(f"❌ No activity recorded for **{member.display_name}**.")

        chan = ctx.guild.get_channel(data.get("last_channel"))
        delta = self._now() - ts
        mins = delta // 60
        hours = delta // 3600
        time_text = f"{hours}h ago" if hours >= 1 else f"{mins}m ago"
        activity = data.get("activity_type", "unknown").capitalize()

        embed = discord.Embed(
            title=f"Last Seen: {member.display_name}",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Time", value=f"{time_text}", inline=True)
        embed.add_field(name="Activity", value=activity, inline=True)
        embed.add_field(
            name="Channel",
            value=chan.mention if chan else "*Deleted Channel*",
            inline=True
        )
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(embed=embed)

    # ------------------------
    #     ADMIN SETTINGS
    # ------------------------

    @checks.admin()
    @commands.group(name="seenconfig", invoke_without_command=True)
    async def seenconfig(self, ctx):
        """View current SeenBot config."""
        conf = await self.config.guild(ctx.guild).all()
        msg = (
            f"**SeenBot Config:**\n"
            f"- Track reactions: {'✅' if conf['track_reactions'] else '❌'}\n"
            f"- Track typing: {'✅' if conf['track_typing'] else '❌'}\n"
            f"- Track voice: {'✅' if conf['track_voice'] else '❌'}"
        )
        await ctx.send(msg)

    @seenconfig.command(name="reactions")
    async def seenconfig_reactions(self, ctx, on_off: bool):
        """Enable or disable reaction tracking."""
        await self.config.guild(ctx.guild).track_reactions.set(on_off)
        await ctx.send(f"✅ Reaction tracking set to **{on_off}**.")

    @seenconfig.command(name="typing")
    async def seenconfig_typing(self, ctx, on_off: bool):
        """Enable or disable typing tracking."""
        await self.config.guild(ctx.guild).track_typing.set(on_off)
        await ctx.send(f"✅ Typing tracking set to **{on_off}**.")

    @seenconfig.command(name="voice")
    async def seenconfig_voice(self, ctx, on_off: bool):
        """Enable or disable voice tracking."""
        await self.config.guild(ctx.guild).track_voice.set(on_off)
        await ctx.send(f"✅ Voice tracking set to **{on_off}**.")
