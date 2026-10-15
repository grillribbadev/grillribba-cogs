from __future__ import annotations

import logging
from pathlib import Path

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

log = logging.getLogger("red.banannounce")


class BanAnnounce(commands.Cog):
    """Announce bans in a configured channel."""

    MAX_IMAGE_SIZE = 8 * 1024 * 1024
    IMAGE_FILENAME = "ban_announcement.gif"

    def __init__(self, bot: Red):
        self.bot = bot

        self.config = Config.get_conf(
            self,
            identifier=55332211009988,
            force_registration=True,
        )

        self.config.register_guild(
            channel_id=None,
            title="🏴‍☠️ Divine Departure",
            message="{user} has been banned from {server}.",
            image_url=None,
            image_file=None,
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

    @banannounce.command(name="title")
    async def set_title(
        self,
        ctx: commands.Context,
        *,
        title: str,
    ):
        """Set the embed title."""

        await self.config.guild(ctx.guild).title.set(title)

        await ctx.send(
            "✅ Ban announcement title updated."
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

        await ctx.send(
            "✅ Ban announcement message updated."
        )

    @banannounce.command(name="image")
    async def set_image(
        self,
        ctx: commands.Context,
        attachment: discord.Attachment,
    ):
        """Upload a GIF attached to this command message."""

        if not attachment.filename.lower().endswith(".gif"):
            await ctx.send("⚠️ Please attach a GIF file (`.gif`).")
            return

        if attachment.content_type not in (None, "image/gif"):
            await ctx.send("⚠️ The attached file must be a GIF.")
            return

        if attachment.size > self.MAX_IMAGE_SIZE:
            await ctx.send("⚠️ The GIF must be 8 MiB or smaller.")
            return

        image_path = self._image_path(ctx.guild)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        await attachment.save(image_path)
        await self.config.guild(ctx.guild).image_file.set(self.IMAGE_FILENAME)
        await self.config.guild(ctx.guild).image_url.set(None)

        await ctx.send(
            "✅ Ban announcement GIF uploaded and updated."
        )

    @banannounce.command(name="clearimage")
    async def clear_image(
        self,
        ctx: commands.Context,
    ):
        """Remove GIF/image."""

        image_path = self._image_path(ctx.guild)
        if image_path.exists():
            image_path.unlink()

        await self.config.guild(ctx.guild).image_file.set(None)
        await self.config.guild(ctx.guild).image_url.set(None)

        await ctx.send(
            "✅ Ban announcement GIF/image removed."
        )

    @banannounce.command(name="toggle")
    async def toggle(
        self,
        ctx: commands.Context,
    ):
        """Enable or disable announcements."""

        current = await self.config.guild(ctx.guild).enabled()

        await self.config.guild(ctx.guild).enabled.set(
            not current
        )

        status = (
            "enabled"
            if not current
            else "disabled"
        )

        await ctx.send(
            f"✅ Ban announcements are now **{status}**."
        )

    @banannounce.command(name="settings")
    async def settings(
        self,
        ctx: commands.Context,
    ):
        """Show current settings."""

        data = await self.config.guild(ctx.guild).all()

        channel = None

        if data["channel_id"]:
            channel = ctx.guild.get_channel(
                data["channel_id"]
            )

        image_status = (
            "Uploaded GIF"
            if data["image_file"]
            else "No GIF uploaded"
        )

        await ctx.send(
            "**Ban Announcement Settings**\n"
            f"Enabled: `{data['enabled']}`\n"
            f"Channel: {channel.mention if channel else 'Not Set'}\n"
            f"Title: `{data['title']}`\n"
            f"Message: `{data['message']}`\n"
            f"GIF/Image: `{image_status}`"
        )

    @commands.command(name="ben")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ben(
        self,
        ctx: commands.Context,
        user: discord.User,
    ):
        """Send a fake ban announcement for a user or ID."""

        sent = await self._send_ban_announcement(ctx.guild, user)

        if sent:
            await ctx.send(
                f"✅ Fake ban announcement sent for {user.mention}."
            )
        else:
            await ctx.send(
                "⚠️ Ban announcements are disabled or not configured for this server."
            )

    async def _send_ban_announcement(
        self,
        guild: discord.Guild,
        user: discord.User,
    ) -> bool:
        data = await self.config.guild(guild).all()

        if not data["enabled"]:
            return False

        channel_id = data["channel_id"]

        if not channel_id:
            return False

        channel = guild.get_channel(channel_id)

        if channel is None:
            return False

        title = data["title"].format(
            user=str(user),
            mention=user.mention,
            id=user.id,
            server=guild.name,
        )

        description = data["message"].format(
            user=str(user),
            mention=user.mention,
            id=user.id,
            server=guild.name,
        )

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.red(),
        )

        embed.set_author(
            name=f"{user} was banned",
            icon_url=user.display_avatar.url,
        )

        embed.add_field(
            name="Banned Pirate",
            value=f"`{user}`\nID: `{user.id}`",
            inline=True,
        )

        embed.add_field(
            name="Server",
            value=guild.name,
            inline=True,
        )

        embed.set_footer(
            text="The Marines have carried out justice. ☠️"
        )

        image_file = None
        if data["image_file"]:
            image_path = self._image_path(guild)
            if image_path.is_file():
                image_file = discord.File(image_path, filename=self.IMAGE_FILENAME)
                embed.set_image(url=f"attachment://{self.IMAGE_FILENAME}")
            else:
                log.warning(
                    "Configured ban announcement GIF is missing for guild %s",
                    guild.id,
                )

        try:
            await channel.send(embed=embed, file=image_file)
        except discord.Forbidden:
            return False
        finally:
            if image_file is not None:
                image_file.close()

        return True

    def _image_path(self, guild: discord.Guild) -> Path:
        return (
            cog_data_path(self)
            / str(guild.id)
            / self.IMAGE_FILENAME
        )

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User,
    ):
        await self._send_ban_announcement(guild, user)


async def setup(bot: Red):
    await bot.add_cog(BanAnnounce(bot))
