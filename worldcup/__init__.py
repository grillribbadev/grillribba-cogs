from .worldcup import WorldCup


async def setup(bot):
    await bot.add_cog(WorldCup(bot))