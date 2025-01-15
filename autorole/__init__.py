from .autorole import AutoRole

async def setup(bot):
    """
    Setup function for the AutoRole cog.
    """
    await bot.add_cog(AutoRole(bot))
