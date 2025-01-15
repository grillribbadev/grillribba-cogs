from .reboot import Reboot  # Import the cog class

async def setup(bot):
    """Setup function to add the cog to the bot."""
    await bot.add_cog(Reboot(bot))
