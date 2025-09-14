from .mutelist import MuteList

async def setup(bot):
    await bot.add_cog(MuteList(bot))
