from .weather import weather

async def setup(bot):
    await bot.add_cog(weather(bot))
  