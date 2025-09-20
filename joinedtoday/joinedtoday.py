import discord
from redbot.core import commands
from datetime import datetime, timezone


class JoinedToday(commands.Cog):
    """Track how many members joined today."""

    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.command(name="joinedtoday")
    async def joined_today(self, ctx: commands.Context):
        """Show how many users joined the server today."""
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        joined = [m for m in ctx.guild.members if m.joined_at and m.joined_at >= start_of_day]

        emb = discord.Embed(
            title="📈 Members Joined Today",
            description=f"**{len(joined)}** members joined {ctx.guild.name} today.",
            color=discord.Color.green()
        )
        if joined:
            emb.add_field(
                name="Users",
                value=" ".join(m.mention for m in joined[:20]) + (" ..." if len(joined) > 20 else ""),
                inline=False
            )

        await ctx.send(embed=emb)


async def setup(bot):
    await bot.add_cog(JoinedToday(bot))
