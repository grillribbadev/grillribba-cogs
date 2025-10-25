from .reactspy import ReactSpy

async def setup(bot):
    await bot.add_cog(ReactSpy(bot))
