from .onepieceleveling import OnePieceLeveling

async def setup(bot):
    await bot.add_cog(OnePieceLeveling(bot))
