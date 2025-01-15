from .bump_reminder import BumpReminder

async def setup(bot):
    await bot.add_cog(BumpReminder(bot))
