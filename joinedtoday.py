import discord
from redbot.core import commands
import datetime

class JoinedToday(commands.Cog):
    """Track and report members who joined today."""

    def __init__(self, bot):
        self.bot = bot

    def _joined_today(self, guild: discord.Guild):
        """Return list of members who joined today and are still in the guild."""
        today = datetime.datetime.utcnow().date()
        return [
            m for m in guild.members
            if m.joined_at and m.joined_at.date() == today
        ]

    @commands.guild_only()
    @commands.command(name="joinedcount")
    async def joined_count(self, ctx: commands.Context):
        """
        Show how many members joined today (who are still in the server).
        """
        joined = self._joined_today(ctx.guild)
        count = len(joined)
        embed = discord.Embed(
            title="📊 Members Joined Today",
            description=f"**{count}** members joined today and are still in this server.",
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)

    @commands.guild_only()
    @commands.command(name="joinedlist")
    async def joined_list(self, ctx: commands.Context):
        """
        List members who joined today (still in the server).
        """
        joined = self._joined_today(ctx.guild)
        if not joined:
            return await ctx.send(embed=discord.Embed(
                title="📋 Members Joined Today",
                description="No members joined today.",
                color=discord.Color.blurple()
            ))

        lines = [f"• {m.mention} (`{m}`)" for m in joined]
        # Discord embed field limit handling
        desc = "\n".join(lines[:50])  # cap to 50 entries for readability
        if len(lines) > 50:
            desc += f"\n… and {len(lines) - 50} more."

        embed = discord.Embed(
            title=f"📋 Members Joined Today ({len(joined)})",
            description=desc,
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(JoinedToday(bot))
