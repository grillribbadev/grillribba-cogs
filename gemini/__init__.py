from .gemini_cog import Gemini

async def setup(bot):
    """Standard setup function for Redbot."""
    await bot.add_cog(Gemini(bot))
