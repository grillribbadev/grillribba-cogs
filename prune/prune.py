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
    async def prune(self, ctx: commands.Context, user: discord.Member, amount: int, keyword: Optional[str] = None, channel: Optional[discord.TextChannel] = None):
        if amount <= 0:
            return await ctx.send("Amount must be a positive number.")

        if not channel:
            channel = ctx.channel

        def check(msg):
            return msg.author.id == user.id and (keyword.lower() in msg.content.lower() if keyword else True)

        deleted_messages = []
        async for msg in channel.history(limit=100):  
            if check(msg):
                deleted_messages.append(msg)
                if len(deleted_messages) == amount:
                    break

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
    async def prunelogs(self, ctx: commands.Context, user: Optional[discord.Member] = None, limit: int = 20, channel: Optional[discord.TextChannel] = None):
        if limit > 100:
            return await ctx.send("Limit cannot exceed 100 messages.")

        if not channel:
            channel = ctx.channel

        guild_id = ctx.guild.id
        channel_id = channel.id

        logs = self.deleted_logs.get(guild_id, {}).get(channel_id, [])
        if not logs:
            return await ctx.send(f"No pruned messages logged for {channel.mention}.")

        if user:
            logs = [log for log in logs if log["user_id"] == user.id]

        if not logs:
            return await ctx.send(f"No logs found for {user.mention} in {channel.mention}.")

        logs = logs[-limit:]
        formatted_logs = "\n".join([f"[{log['timestamp']}] {log['user']}: {log['content']}" for log in logs])

        await ctx.send(box(formatted_logs, lang="yaml"))

async def setup(bot: Red):
    await bot.add_cog(Prune(bot))
