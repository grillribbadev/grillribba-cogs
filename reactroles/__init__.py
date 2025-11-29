from .reactroles import ReactRoles

async def setup(bot):
    await bot.add_cog(ReactRoles(bot))
