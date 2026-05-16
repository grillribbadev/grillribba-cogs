from .muterole import MuteRole


async def setup(bot):
    await bot.add_cog(MuteRole(bot))