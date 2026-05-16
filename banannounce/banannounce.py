from __future__ import annotations

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


class BanAnnounce(commands.Cog):
    """Announce bans in a configured channel."""

    def __init__(self, bot: Red):
        self.bot = bot

        self.config = Config.get_conf(
            self,
            identifier=55332211009988,
            force_registration=True,
        )

        self.config.register_guild(
            channel_id=None,
            message="🔨 {user} has been banned from {server}.",
            image_url=None,
            enabled=True,
        )

    @commands.group(name="banannounce")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def banannounce(self, ctx: commands.Context):
        """Configure ban announcements."""
        pass

    @banannounce.command(name="channel")
    async def set_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ):
        """Set the announcement channel."""

        await self.config.guild(ctx.guild).channel_id.set(channel.id)

        await ctx.send(
            f"✅ Ban announcement channel set to {channel.mention}"
        )

    @banannounce.command(name="message")
    async def set_message(
        self,
        ctx: commands.Context,
        *,
        message: str,
    ):
        """
        Set the announcement message.

        Variables:
        {user}
        {mention}
        {id}
        {server}
        """

        await self.config.guild(ctx.guild).message.set(message)

        await ctx.send("✅ Ban announcement message updated.")

    @banannounce.command(name="image")
    async def set_image(
        self,
        ctx: commands.Context,
        url: str,
    ):
        """Set image or GIF URL."""

        await self.config.guild(ctx.guild).image_url.set(url)

        await ctx.send("✅ Ban announcement image/GIF updated.")

    @banannounce.command(name="clearimage")
    async def clear_image(self, ctx: commands.Context):
        """Remove image/GIF."""

        await self.config.guild(ctx.guild).image_url.set(None)

        await ctx.send("✅ Ban announcement image/GIF removed.")

    @banannounce.command(name="toggle")
    async def toggle(self, ctx: commands.Context):
        """Enable or disable announcements."""

        current = await self.config.guild(ctx.guild).enabled()

        await self.config.guild(ctx.guild).enabled.set(not current)

        status = "enabled" if not current else "disabled"

        await ctx.send(
            f"✅ Ban announcements are now **{status}**."
        )

    @banannounce.command(name="settings")
    async def settings(self, ctx: commands.Context):
        """Show current settings."""

        data = await self.config.guild(ctx.guild).all()

        channel = None

        if data["channel_id"]:
            channel = ctx.guild.get_channel(data["channel_id"])

        await ctx.send(
            "**Ban Announcement Settings**\n"
            f"Enabled: `{data['enabled']}`\n"
            f"Channel: {channel.mention if channel else 'Not Set'}\n"
            f"Message: `{data['message']}`\n"
            f"Image/GIF: `{data['image_url'] or 'None'}`"
        )

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User,
    ):
        data = await self.config.guild(guild).all()

        if not data["enabled"]:
            return

        channel_id = data["channel_id"]

        if not channel_id:
            return

        channel = guild.get_channel(channel_id)

        if channel is None:
            return

        message = data["message"].format(
            user=str(user),
            mention=user.mention,
            id=user.id,
            server=guild.name,
        )

        try:
            await channel.send(message)

            if data["image_url"]:
                await channel.send(data["image_url"])

        except discord.Forbidden:
            pass


async def setup(bot: Red):
    await bot.add_cog(BanAnnounce(bot))
