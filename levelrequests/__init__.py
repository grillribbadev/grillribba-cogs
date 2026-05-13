from .levelrequests import LevelRequests


async def setup(bot):
    await bot.add_cog(LevelRequests(bot))
