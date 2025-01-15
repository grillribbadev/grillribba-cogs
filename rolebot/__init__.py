from .rolemanagement import RoleManagement

async def setup(bot):
    await bot.add_cog(RoleManagement(bot))
