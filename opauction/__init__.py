from .opauction import OPAuction


async def setup(bot):
    await bot.add_cog(OPAuction(bot))