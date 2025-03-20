import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from typing import Optional
from collections import defaultdict

class Prune(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.deleted_logs = defaultdict(lambda: defaultdict(list))

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
            try:
                messages = [msg async for msg in channel.history(limit=None) if msg.author == user]
                if messages:
                    await channel.delete_messages(messages)
                    deleted_count += len(messages)
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
