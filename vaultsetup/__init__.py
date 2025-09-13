from .vaultsetup import VaultSetup

async def setup(bot):
    await bot.add_cog(VaultSetup(bot))
