from .pingcontrol import PingControl

async def setup(bot):
    await bot.add_cog(PingControl(bot))
