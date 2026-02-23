# __init__.py
from .phrasemute import PhraseMute

async def setup(bot):
    await bot.add_cog(PhraseMute(bot))