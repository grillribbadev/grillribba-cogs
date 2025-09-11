from .seen import Seen

async def setup(bot):
    await bot.add_cog(Seen(bot))
