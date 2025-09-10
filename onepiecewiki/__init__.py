from .onepiecewiki import OnePieceWiki

async def setup(bot):
    await bot.add_cog(OnePieceWiki(bot))  # <-- instantiate the cog
