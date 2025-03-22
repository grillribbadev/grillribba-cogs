from .antialtnotifier import AntiAltNotifier

async def setup(bot):
    await bot.add_cog(AntiAltNotifier(bot))
