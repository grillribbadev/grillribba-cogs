from .testcog import testcog

async def setup(bot):
    await bot.add_cog(testcog(bot))
  