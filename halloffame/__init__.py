from .halloffame import HallOfFame


async def setup(bot):
    await bot.add_cog(HallOfFame(bot))
