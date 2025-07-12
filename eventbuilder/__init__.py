from .eventbuilder import EventBuilder

async def setup(bot):
    await bot.add_cog(EventBuilder(bot))
