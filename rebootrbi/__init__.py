from .reboot_cog import Reboot  # Import the cog class

async def setup(bot):
    """Setup function to add the cog to the bot."""
    try:
        await bot.add_cog(Reboot(bot))
        print("Reboot cog loaded successfully.")
    except Exception as e:
        print(f"Error while loading Reboot cog: {e}")
