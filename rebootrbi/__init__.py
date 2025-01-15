from .reboot import Reboot  # Import the cog class

async def setup(bot):
    """Setup function to add the cog to the bot."""
    cog = Reboot(bot)
    await bot.add_cog(cog)
