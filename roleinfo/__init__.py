from .roleinfo import RoleInfo

async def setup(bot):
    await bot.add_cog(RoleInfo(bot))
