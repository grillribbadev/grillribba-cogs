from .battlebot import BattleBot

async def setup(bot):
    await bot.add_cog(BattleBot(bot))
