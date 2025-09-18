from .nickblocker import NickBlocker

async def setup(bot):
    await bot.add_cog(NickBlocker(bot))
