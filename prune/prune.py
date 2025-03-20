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
        if action.lower() == "activate":
            if level == 5:
                self.lockdown_level = LEVEL_5_ROLE_ID
                self.lockdown_active = True
                await self.lock_channels(ctx, LEVEL_5_ROLE_ID)
                await ctx.send("🛡️ **Lockdown Activated:** Only users with `Level 5+` can talk.")
            elif level == 15:
                self.lockdown_level = LEVEL_15_ROLE_ID
                self.lockdown_active = True
                await self.lock_channels(ctx, LEVEL_15_ROLE_ID)
                await ctx.send("🛡️ **Lockdown Activated:** Only users with `Level 15+` can talk.")
            else:
                await ctx.send("Usage: `.shield activate level 5` or `.shield activate level 15`")
        
        elif action.lower() == "deactivate":
            self.lockdown_active = False
            self.lockdown_level = None
            await self.unlock_channels(ctx)
            await ctx.send("❌ **Lockdown Deactivated:** All users can talk again.")
        else:
            await ctx.send("Usage: `.shield activate level 5`, `.shield activate level 15`, or `.shield deactivate`")

    async def lock_channels(self, ctx, role_id: int):
        role = discord.utils.get(ctx.guild.roles, id=role_id)
        if not role:
            await ctx.send("❌ The required role does not exist. Please check the role IDs.")
            return

        for channel in ctx.guild.text_channels:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)

            # Allow the specified level role to talk
            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = True
            await channel.set_permissions(role, overwrite=overwrite)

    async def unlock_channels(self, ctx):
        for channel in ctx.guild.text_channels:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None  # Reset permissions
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)

    @commands.mod()
    @commands.guild_only()
    @commands.command()
    async def prune(self, ctx: commands.Context, user: discord.Member, amount: int, channel: Optional[discord.TextChannel] = None, *, keyword: Optional[str] = None):
        if amount <= 0:
            return await ctx.send("Amount must be a positive number.")

        if not channel:
            channel = ctx.channel

        deleted_messages = []
        async for msg in channel.history(limit=500):
            if msg.id == ctx.message.id:
                continue
            if msg.author.id == user.id and (keyword.lower() in msg.content.lower() if keyword else True):
                deleted_messages.append(msg)
                if len(deleted_messages) == amount:
                    break

        if not deleted_messages:
            return await ctx.send(f"No matching messages found in {channel.mention}.")

        await channel.delete_messages(deleted_messages)

        guild_id = ctx.guild.id
        channel_id = channel.id
        self.deleted_logs[guild_id][channel_id].extend(
            [{"user_id": msg.author.id, "user": msg.author.name, "content": msg.content, "timestamp": msg.created_at.strftime("%Y-%m-%d %H:%M:%S")} for msg in deleted_messages]
        )

        await ctx.send(f"Deleted {len(deleted_messages)} messages from {user.mention} in {channel.mention}.")

    @commands.mod()
    @commands.guild_only()
    @commands.command()
    async def prunelogs(self, ctx: commands.Context, user: discord.Member, limit: Optional[int] = 20, channel: Optional[discord.TextChannel] = None):
        if limit > 100:
            return await ctx.send("Limit cannot exceed 100 messages.")

        if not channel:
            channel = ctx.channel

        guild_id = ctx.guild.id
        channel_id = channel.id

        logs = self.deleted_logs.get(guild_id, {}).get(channel_id, [])
        if not logs:
            return await ctx.send(f"No pruned messages logged for {channel.mention}.")

        logs = [log for log in logs if log["user_id"] == user.id]

        if not logs:
            return await ctx.send(f"No logs found for {user.mention} in {channel.mention}.")

        logs = logs[-limit:]
        formatted_logs = "\n".join([f"[{log['timestamp']}] {log['user']}: {log['content']}" for log in logs])

        await ctx.send(box(formatted_logs, lang="yaml"))

    @commands.mod()
    @commands.guild_only()
    @commands.command()
    async def nuke(self, ctx: commands.Context, user: discord.Member):
        deleted_count = 0

        for channel in ctx.guild.text_channels:
            if not channel.permissions_for(ctx.guild.me).manage_messages:
                continue  

            try:
                while True:
                    deleted = await channel.purge(limit=100, check=lambda m: m.author == user)
                    deleted_count += len(deleted)
                    if len(deleted) < 100:
                        break  
            except discord.Forbidden:
                await ctx.send(f"❌ I don't have permission to delete messages in {channel.mention}.")
            except discord.HTTPException:
                continue  

        silenced_role = discord.utils.get(ctx.guild.roles, name="Silenced")
        if not silenced_role:
            return await ctx.send("❌ The `Silenced` role does not exist. Please create it manually.")

        try:
            await user.add_roles(silenced_role)
            await ctx.send(f"🚨 Nuked **{deleted_count}** messages from {user.mention} and assigned the `Silenced` role.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to assign the `Silenced` role.")

async def setup(bot: Red):
    await bot.add_cog(Prune(bot))
