import discord
from redbot.core import commands, Config
from discord.ext import tasks

class BumpReminder(commands.Cog):
    """A cog to send bump reminders and provide instructions for bumping."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987654321)
        self.config.register_guild(bump_channel=None)
        self.bump_reminder.start()

    def cog_unload(self):
        self.bump_reminder.cancel()

    @tasks.loop(hours=2)
    async def bump_reminder(self):
        """Send a bump reminder every 2 hours."""
        for guild in self.bot.guilds:
            channel_id = await self.config.guild(guild).bump_channel()
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel:
                    await self.send_bump_instructions(channel)

    @commands.command()
    async def setbumpchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where bump reminders will be sent."""
        await self.config.guild(ctx.guild).bump_channel.set(channel.id)
        await ctx.send(f"Bump reminders will now be sent in {channel.mention} every 2 hours.")

    @commands.command()
    async def getbumpchannel(self, ctx):
        """Get the currently configured bump channel."""
        channel_id = await self.config.guild(ctx.guild).bump_channel()
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                await ctx.send(f"Bump reminders are being sent in {channel.mention}.")
                return
        await ctx.send("No bump channel has been set.")

    @commands.command()
    async def howtobump(self, ctx):
        """Provide immediate instructions for bumping."""
        await self.send_bump_instructions(ctx.channel)

    async def send_bump_instructions(self, channel):
        """Send bump instructions in the specified channel."""
        embed = discord.Embed(
            title="How to Bump the Server",
            description=(
                "🔔 **Follow these steps to bump the server:**\n"
                "1. Type `/bump` in this channel.\n"
                "2. Select the **Disboard** bot's suggestion and press enter.\n\n"
                "Help the server grow by keeping it active! 🚀"
            ),
            color=discord.Color.blue(),
        )
        await channel.send(embed=embed)

    @commands.command()
    async def startbump(self, ctx):
        """Manually start the bump reminder loop."""
        if not self.bump_reminder.is_running():
            self.bump_reminder.start()
            await ctx.send("Bump reminders have been started.")
        else:
            await ctx.send("Bump reminders are already running.")

    @commands.command()
    async def stopbump(self, ctx):
        """Manually stop the bump reminder loop."""
        if self.bump_reminder.is_running():
            self.bump_reminder.cancel()
            await ctx.send("Bump reminders have been stopped.")
        else:
            await ctx.send("Bump reminders are not running.")

    @bump_reminder.before_loop
    async def before_bump_reminder(self):
        """Wait until the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BumpReminder(bot))
