from .one_piece_updates import OnePieceUpdates

async def setup(bot):
    await bot.add_cog(OnePieceUpdates(bot))
