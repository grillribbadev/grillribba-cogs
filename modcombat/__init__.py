from .autobotcleaner import AutoBotCleaner


async def setup(bot):
    await bot.add_cog(AutoBotCleaner(bot))
