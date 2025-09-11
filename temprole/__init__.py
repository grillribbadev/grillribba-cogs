from .temprole import AutoRoleManager

async def setup(bot):
    await bot.add_cog(AutoRoleManager(bot))
