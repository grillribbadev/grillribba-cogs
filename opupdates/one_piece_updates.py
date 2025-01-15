import discord
from redbot.core import commands, Config
import aiohttp
import asyncio
from bs4 import BeautifulSoup

class OnePieceUpdates(commands.Cog):
    """Track updates for One Piece and One Punch Man."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210)
        self.config.register_guild(one_piece_channel=None, one_punch_channel=None)
        self.latest_chapters = {"one_piece": None, "one_punch": None}
        self.check_updates_task = self.bot.loop.create_task(self.check_updates())

    def cog_unload(self):
        self.check_updates_task.cancel()

    async def check_updates(self):
        """Check for updates every hour."""
        while True:
            await self.fetch_and_notify()
            await asyncio.sleep(3600)

    async def fetch_and_notify(self):
        """Fetch updates and notify relevant channels."""
        guilds = self.bot.guilds
        async with aiohttp.ClientSession() as session:
            for guild in guilds:
                one_piece_channel_id = await self.config.guild(guild).one_piece_channel()
                one_punch_channel_id = await self.config.guild(guild).one_punch_channel()

                if one_piece_channel_id:
                    channel = guild.get_channel(one_piece_channel_id)
                    if channel:
                        updated = await self.check_one_piece(session)
                        if updated:
                            role = discord.utils.get(guild.roles, name="op-updates")
                            role_mention = role.mention if role else ""
                            await channel.send(f"{role_mention} New One Piece chapter available! Check it here: https://tcbscans.me")

                if one_punch_channel_id:
                    channel = guild.get_channel(one_punch_channel_id)
                    if channel:
                        updated = await self.check_one_punch(session)
                        if updated:
                            role = discord.utils.get(guild.roles, name="opm-updates")
                            role_mention = role.mention if role else ""
                            await channel.send(f"{role_mention} New One Punch Man chapter available! Check it here: https://manga4life.com/manga/Onepunch-Man")

    async def check_one_piece(self, session):
        """Check for updates to One Piece."""
        url = "https://tcbscans.me"
        async with session.get(url) as response:
            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            latest_chapter = soup.select_one("a.latest-chapter")
            if latest_chapter:
                chapter_text = latest_chapter.get_text(strip=True)
                if chapter_text != self.latest_chapters["one_piece"]:
                    self.latest_chapters["one_piece"] = chapter_text
                    return True
        return False

    async def check_one_punch(self, session):
        """Check for updates to One Punch Man."""
        url = "https://manga4life.com/manga/Onepunch-Man"
        async with session.get(url) as response:
            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            latest_chapter = soup.select_one("a.list-group-item")
            if latest_chapter:
                chapter_text = latest_chapter.get_text(strip=True)
                if chapter_text != self.latest_chapters["one_punch"]:
                    self.latest_chapters["one_punch"] = chapter_text
                    return True
        return False

    @commands.command()
    @commands.admin_or_permissions(manage_channels=True)
    async def setupdatechannel(self, ctx, series: str, channel: discord.TextChannel):
        """Set the update channel for a specific series."""
        series = series.lower()
        if series == "one_piece":
            await self.config.guild(ctx.guild).one_piece_channel.set(channel.id)
            await ctx.send(f"Update channel for One Piece set to {channel.mention}.")
        elif series == "one_punch":
            await self.config.guild(ctx.guild).one_punch_channel.set(channel.id)
            await ctx.send(f"Update channel for One Punch Man set to {channel.mention}.")
        else:
            await ctx.send("Invalid series. Please specify 'one_piece' or 'one_punch'.")

    @commands.command()
    async def testupdate(self, ctx, series: str):
        """Test the update notification."""
        series = series.lower()
        if series == "one_piece":
            channel_id = await self.config.guild(ctx.guild).one_piece_channel()
            if not channel_id:
                await ctx.send("No update channel set for One Piece.")
                return
            channel = ctx.guild.get_channel(channel_id)
            if not channel:
                await ctx.send("The update channel for One Piece is invalid.")
                return
            role = discord.utils.get(ctx.guild.roles, name="op-updates")
            role_mention = role.mention if role else ""
            await channel.send(f"{role_mention} Test notification for One Piece updates.")
        elif series == "one_punch":
            channel_id = await self.config.guild(ctx.guild).one_punch_channel()
            if not channel_id:
                await ctx.send("No update channel set for One Punch Man.")
                return
            channel = ctx.guild.get_channel(channel_id)
            if not channel:
                await ctx.send("The update channel for One Punch Man is invalid.")
                return
            role = discord.utils.get(ctx.guild.roles, name="opm-updates")
            role_mention = role.mention if role else ""
            await channel.send(f"{role_mention} Test notification for One Punch Man updates.")
        else:
            await ctx.send("Invalid series. Please specify 'one_piece' or 'one_punch'.")
