from .banannounce import BanAnnounce


async def setup(bot):
    await bot.add_cog(BanAnnounce(bot))