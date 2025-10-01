from .wheelcog import WheelCog

async def setup(bot):
    await bot.add_cog(WheelCog(bot))
