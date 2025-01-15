from redbot.core import commands
import discord

class testcog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.deleted_messages = {}  # dict to store deleted messages guild

    @commands.command()
    async def setrole(self, ctx, member: discord.Member, role: discord.Role):
        if role in member.roles:
            await ctx.send(f"{member.display_name} already has the {role.name} role.")
            return
        
        try:
            await member.add_roles(role)
            await ctx.send(f"Successfully added {role.name} to {member.display_name}.")
        except discord.Forbidden:
            await ctx.send("I don't have permission to manage this role.")
        except discord.HTTPException as e:
            await ctx.send(f"Failed to add role: {e}")

    def is_not_pinned(self, msg):
        return not msg.pinned

    @commands.command()
    async def delmessage(self, ctx, amount: int):
        if amount < 1:
            await ctx.send("You must specify at least 1 message to delete.")
            return

        guild_id = ctx.guild.id
        if guild_id not in self.deleted_messages:
            self.deleted_messages[guild_id] = []  # list for the guild if its not present

        try:
            deleted = await ctx.channel.purge(limit=amount + 1, check=self.is_not_pinned)  # +1 to include the command message
            if deleted:
                self.deleted_messages[guild_id].extend(deleted[1:])  # excluding the command message
                self.deleted_messages[guild_id] = self.deleted_messages[guild_id][-10:]  # save last 10 messages
                await ctx.send(f"Successfully deleted {len(deleted) - 1} messages.", delete_after=3)
            else:
                await ctx.send("No messages were deleted.")
        except discord.Forbidden:
            await ctx.send("I don't have permission to delete messages.")
        except discord.HTTPException as e:
            await ctx.send(f"Failed to delete messages: {e}")

    @commands.command()
    async def showdel(self, ctx):
        guild_id = ctx.guild.id
        if guild_id not in self.deleted_messages or not self.deleted_messages[guild_id]:
            await ctx.send("No messages have been deleted yet.")
            return

        embed = discord.Embed(title="Last 10 Deleted Messages", color=discord.Color.red())
        for message in self.deleted_messages[guild_id]:
            embed.add_field(name=f"Message by {message.author}", value=message.content, inline=False)

        await ctx.send(embed=embed)
