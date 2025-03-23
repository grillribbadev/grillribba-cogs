from .pythondocs import DiscordPySearcher

async def setup(bot):
    await bot.add_cog(DiscordPySearcher(bot))
