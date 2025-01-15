from .mangaupdates import MangaUpdates

async def setup(bot):
    """
    Setup function for the MangaUpdates cog.
    """
    await bot.add_cog(MangaUpdates(bot))
