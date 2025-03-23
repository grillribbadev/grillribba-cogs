from .googlesearch import GoogleSearch

async def setup(bot):
    await bot.add_cog(GoogleSearch(bot))
