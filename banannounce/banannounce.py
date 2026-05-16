from __future__ import annotations

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


class BanAnnounce(commands.Cog):
    """Announce bans in a configured channel."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=55332211009988, force_registration=True)

        self.config.register_guild(
            channel_id=None,
            title="🏴‍☠️ Divine Departure",
            message="{user} has been cast into the depths of Impel Down by {server}!",
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
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"✅ Ban announcement channel set to {channel.mention}")

    @banannounce.command(name="title")
    async def set_title(self, ctx: commands.Context, *, title: str):
        await self.config.guild(ctx.guild).title.set(title)
        await ctx.send("✅ Ban announcement title updated.")

    @banannounce.command(name="message")
    async def set_message(self, ctx: commands.Context, *, message: str):
        await self.config.guild(ctx.guild).message.set(message)
        await ctx.send("✅ Ban announcement message updated.")

    @banannounce.command(name="image")
    async def set_image(self, ctx: commands.Context, *, url: str):
        await self.config.guild(ctx.guild).image_url.set(url)
        await ctx.send("✅ Ban announcement GIF/image updated.")

    @banannounce.command(name="clearimage")
    async def clear_image(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).image_url.set(None)
        await ctx.send("✅ Ban announcement GIF/image removed.")

    @banannounce.command(name="toggle")
    async def toggle(self, ctx: commands.Context):
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        await ctx.send(f"✅ Ban announcements are now **{'enabled' if not current else 'disabled'}**.")

    @banannounce.command(name="settings")
    async def settings(self, ctx: commands.Context):
        data = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data["channel_id"]) if data["channel_id"] else None

        await ctx.send(
            "**Ban Announcement Settings**\n"
            f"Enabled: `{data['enabled']}`\n"
            f"Channel: {channel.mention if channel else 'Not Set'}\n"
            f"Title: `{data['title']}`\n"
            f"Message: `{data['message']}`\n"
            f"GIF/Image: `{data['image_url'] or 'None'}`"
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        data = await self.config.guild(guild).all()

        if not data["enabled"] or not data["channel_id"]:
            return

        channel = guild.get_channel(data["channel_id"])
        if channel is None:
            return

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

        embed.set_footer(text="Done. That felt good. 🏴‍☠️")

        try:
            await channel.send(embed=embed)

            if data["image_url"]:
                await channel.send(data["image_url"])

        except discord.Forbidden:
            pass


async def setup(bot: Red):
    await bot.add_cog(BanAnnounce(bot))
