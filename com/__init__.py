from .com import ChatterOfMonth


async def setup(bot):
	await bot.add_cog(ChatterOfMonth(bot))
