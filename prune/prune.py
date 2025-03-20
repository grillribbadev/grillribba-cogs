import discord
import aiohttp
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from typing import Optional
from collections import defaultdict

PASTE_SERVICE_URL = "https://mystb.in/documents"

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
        async with aiohttp.ClientSession() as session:
            async with session.post(PASTE_SERVICE_URL, data=text.encode("utf-8")) as response:
                if response.status == 200:
                    try:
                        json_data = await response.json()
                        if "key" in json_data:
                            return f"https://mystb.in/{json_data['key']}"
                        else:
                            print(f"Unexpected response from Mystbin: {json_data}")
                            return None
                    except Exception as e:
                        print(f"Error parsing Mystbin response: {e}")
                        return None
                else:
                    print(f"Mystbin API returned status {response.status}")
                    return None

async def setup(bot: Red):
    await bot.add_cog(Prune(bot))
