from .joinedtoday import JoinedToday

async def setup(bot):
    await bot.add_cog(JoinedToday(bot))
