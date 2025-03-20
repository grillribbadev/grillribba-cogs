import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from typing import Optional
from collections import defaultdict

class Prune(commands.Cog):
    """A cog to prune messages from a specific user with optional keyword and channel selection. Logs exist until bot restarts."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.deleted_logs = defaultdict(lambda: defaultdict(list))  # Store logs in memory (resets on restart)

    @commands.mod()
    @commands.guild_only()
    @commands.command()
    async def prune(self, ctx: commands.Context, user: discord.Member, amount: int, keyword: Optional[str] = None, channel: Optional[discord.TextChannel] = None):
        """Delete the last <amount> messages from <user> in a specific channel (default: current channel)."""
        if amount <= 0:
            return await ctx.send("Amount must be a positive number.")

        if not channel:
            channel = ctx.channel  # Default to current channel

        def check(msg):
            return msg.author.id == user.id and (keyword.lower() in msg.content.lower() if keyword else True)

        deleted_messages = await channel.purge(limit=amount * 2, check=check, before=ctx.message)

        # Log deleted messages (only until restart)
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
        """Retrieve pruned messages. Can filter by user and channel (default: current channel)."""
        if limit > 100:
            return await ctx.send("Limit cannot exceed 100 messages.")

        if not channel:
            channel = ctx.channel  # Default to current channel

        guild_id = ctx.guild.id
        channel_id = channel.id

        logs = self.deleted_logs.get(guild_id, {}).get(channel_id, [])
        if not logs:
            return await ctx.send(f"No pruned messages logged for {channel.mention}.")

        # Filter logs by user if provided
        if user:
            logs = [log for log in logs if log["user_id"] == user.id]

        if not logs:
            return await ctx.send(f"No logs found for {user.mention} in {channel.mention}.")

        logs = logs[-limit:]  # Get last 'limit' messages
        formatted_logs = "\n".join([f"[{log['timestamp']}] {log['user']}: {log['content']}" for log in logs])

        await ctx.send(box(formatted_logs, lang="yaml"))

async def setup(bot: Red):
    await bot.add_cog(Prune(bot))
