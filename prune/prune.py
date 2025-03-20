import discord
import aiohttp
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from typing import Optional
from collections import defaultdict

PASTE_SERVICE_URL = "https://mystb.in/documents"  # More reliable than Hastebin

class Prune(commands.Cog):
    """A cog to prune messages from a specific user with optional keyword and channel selection. Logs are uploaded to Mystbin."""

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
            channel = ctx.channel  

        def check(msg):
            return msg.author.id == user.id and (keyword.lower() in msg.content.lower() if keyword else True)

        deleted_messages = await channel.purge(limit=amount * 2, check=check, before=ctx.message)

      
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
        """Retrieve pruned messages, upload logs to Mystbin, and return a link."""
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

        
        paste_url = await self.upload_logs_to_pastebin(formatted_logs)
        if paste_url:
            await ctx.send(f"Logs uploaded: {paste_url}")
        else:
            await ctx.send("Failed to upload logs. Please try again later.")

    async def upload_logs_to_pastebin(self, text: str) -> Optional[str]:
        """Uploads logs to Mystbin and returns the URL."""
        async with aiohttp.ClientSession() as session:
            async with session.post(PASTE_SERVICE_URL, data={"text": text}) as response:
                if response.status == 200:
                    json_data = await response.json()
                    return f"https://mystb.in/{json_data['key']}"
                return None

async def setup(bot: Red):
    await bot.add_cog(Prune(bot))
