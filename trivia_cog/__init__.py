from .trivia_cog import TriviaCog

async def setup(bot):
    """Load the TriviaCog."""
    cog = TriviaCog(bot)
    await bot.add_cog(cog)
