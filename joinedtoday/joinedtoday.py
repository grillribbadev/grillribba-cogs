import discord
from redbot.core import commands
import datetime

class JoinedTracker(commands.Cog):
    """Track and report members who joined in the last N days."""

    def __init__(self, bot):
        self.bot = bot

    def _joined_within(self, guild: discord.Guild, days: int):
        """Return list of members who joined within the last <days> days."""
        now = datetime.datetime.utcnow()
        cutoff = now - datetime.timedelta(days=days)
        return [m for m in guild.members if m.joined_at and m.joined_at >= cutoff]

    @commands.guild_only()
    @commands.command(name="joinedcount")
    async def joined_count(self, ctx: commands.Context, days: int = 1):
        """
        Show how many members joined in the last <days> (default = 1, i.e. today).
        """
        if days < 1:
            return await ctx.send("❌ Days must be at least 1.")

        joined = self._joined_within(ctx.guild, days)
        count = len(joined)

        title = f"📊 Members Joined in the Last {days} Day{'s' if days > 1 else ''}"
        embed = discord.Embed(
            title=title,
            description=f"**{count}** members joined in the last {days} day{'s' if days > 1 else ''} and are still in this server.",
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)

    @commands.guild_only()
    @commands.command(name="joinedlist")
    async def joined_list(self, ctx: commands.Context, days: int = 1):
        """
        List members who joined in the last <days> (default = 1, i.e. today).
        """
        if days < 1:
            return await ctx.send("❌ Days must be at least 1.")

        joined = self._joined_within(ctx.guild, days)
        if not joined:
            title = f"📋 Members Joined in the Last {days} Day{'s' if days > 1 else ''}"
            return await ctx.send(embed=discord.Embed(
                title=title,
                description="No members joined in that time frame.",
                color=discord.Color.blurple()
            ))

        lines = [f"• {m.mention} (`{m}`)" for m in joined]
        desc = "\n".join(lines[:50])  # cap to 50 entries
        if len(lines) > 50:
            desc += f"\n… and {len(lines) - 50} more."

        title = f"📋 Members Joined in the Last {days} Day{'s' if days > 1 else ''}"
        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(JoinedTracker(bot))
