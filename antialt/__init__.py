from .antialtnotifier import AntiAltNotifier

def setup(bot):
    bot.add_cog(AntiAltNotifier(bot))
