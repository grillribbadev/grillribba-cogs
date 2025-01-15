import os
from redbot.core import commands, Config
from redbot.core.bot import Red

class Reboot(commands.Cog):
    """Cog to reboot the Raspberry Pi hosting the bot."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_global(rpi_user=None)  # Store the Raspberry Pi username globally

    @commands.command()
    @commands.is_owner()
    async def rpiuser(self, ctx: commands.Context, username: str):
        """
        Set the Raspberry Pi username for system commands.
        """
        await self.config.rpi_user.set(username)
        await ctx.send(f"? Raspberry Pi username set to: `{username}`")

    @commands.command()
    @commands.is_owner()
    async def reboot(self, ctx: commands.Context):
        """
        Reboot the Raspberry Pi hosting the bot.

        This command will restart the system where the bot is hosted.
        Restricted to the bot owner.
        """
        username = await self.config.rpi_user()
        if not username:
            await ctx.send("?? Raspberry Pi username is not set. Use `.rpiuser <username>` to set it.")
            return

        await ctx.send("?? Are you sure you want to reboot the Raspberry Pi? Reply with 'yes' to confirm.")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "yes"

        try:
            # Updated to use proper logging and error checks
            await self.bot.wait_for("message", check=check, timeout=30)
            await ctx.send(f"Rebooting now! ?? (as user `{username}`)")
            os.system(f"/usr/bin/sudo -u {username} /sbin/reboot")
        except TimeoutError:
            await ctx.send("Reboot cancelled. ?")
        except Exception as e:
            await ctx.send(f"An error occurred while trying to reboot: {e}")
