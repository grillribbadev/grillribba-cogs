from .prune import Prune

async def setup(bot):
    await bot.add_cog(Prune(bot))
