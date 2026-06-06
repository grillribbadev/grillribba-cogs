from .filtercog import KeywordFilter

async def setup(bot):
    await bot.add_cog(KeywordFilter(bot))