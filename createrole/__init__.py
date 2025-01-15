from .custom_role_cog import CustomRoleCog

async def setup(bot):
    await bot.add_cog(CustomRoleCog(bot))
