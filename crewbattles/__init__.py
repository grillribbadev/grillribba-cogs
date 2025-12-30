from .crewbattles import CrewBattles

async def setup(bot):
    await bot.add_cog(CrewBattles(bot))
