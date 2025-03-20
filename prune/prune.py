import discord
from redbot.core import commands
from redbot.core.bot import Red
from typing import Optional
from collections import defaultdict

LEVEL_5_ROLE_ID = 644731127738662922  # Petty Officer [LVL5]
LEVEL_15_ROLE_ID = 644731543415291911  # Level 15 Role

class Prune(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.deleted_logs = defaultdict(lambda: defaultdict(list))
        self.lockdown_active = False
        self.lockdown_level = None

    @commands.mod()
    @commands.guild_only()
    @commands.command()
    async def shield(self, ctx: commands.Context, action: str, level: Optional[int] = None):
        if action.lower() == "activate" and level in [5, 15]:
            self.lockdown_active = True
            self.lockdown_level = LEVEL_5_ROLE_ID if level == 5 else LEVEL_15_ROLE_ID
            await self.lock_channels(ctx, self.lockdown_level)
            await ctx.send(f"🛡️ **Lockdown Activated:** Only users with `Level {level}+` can talk.")
        
        elif action.lower() == "deactivate":
            self.lockdown_active = False
            self.lockdown_level = None
            await self.unlock_channels(ctx)
            await ctx.send("❌ **Lockdown Deactivated:** All users can talk again.")

        else:
            await ctx.send("Usage: `.shield activate 5` or `.shield activate 15`")

    async def lock_channels(self, ctx, role_id: int):
        role = discord.utils.get(ctx.guild.roles, id=role_id)
        if not role:
            await ctx.send("❌ The required role does not exist. Please check the role IDs.")
            return

        for channel in ctx.guild.text_channels:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)

            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = True
            await channel.set_permissions(role, overwrite=overwrite)

    async def unlock_channels(self, ctx):
        for channel in ctx.guild.text_channels:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None  # Reset permissions
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)

async def setup(bot: Red):
    await bot.add_cog(Prune(bot))
