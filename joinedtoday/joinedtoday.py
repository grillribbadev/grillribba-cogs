import discord
from redbot.core import commands
from datetime import datetime, timedelta, timezone


class JoinedToday(commands.Cog):
    """Track and report members who joined in the last N days."""

    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.command(name="joinedcount")
    async def joinedcount(self, ctx, days: int = 1):
        """
        Show how many members joined in the last N days (default 1).
        Includes only members still in the server.
        """
        if days < 1:
            return await ctx.send("⚠️ Days must be at least 1.")

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)

        # Count members still in guild
        joined_members = [
            m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff
        ]
        count = len(joined_members)

        embed = discord.Embed(
            title=f"📥 Members Joined (Last {days} day{'s' if days > 1 else ''})",
            description=f"✅ **{count} member{'s' if count != 1 else ''} joined** "
                        f"in the last {days} day{'s' if days > 1 else ''}.",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Only includes members still in {ctx.guild.name}.")

        await ctx.send(embed=embed)

    @commands.guild_only()
    @commands.command(name="joinedlist")
    async def joinedlist(self, ctx, days: int = 1):
        """
        List members who joined in the last N days (default 1).
        Includes only members still in the server.
        """
        if days < 1:
            return await ctx.send("⚠️ Days must be at least 1.")

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)

        joined_members = [
            m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff
        ]

        if not joined_members:
            return await ctx.send(
                f"ℹ️ No members joined in the last {days} day{'s' if days > 1 else ''}."
            )

        lines = [f"• {m.mention} (`{m}`)" for m in joined_members]
        desc = "\n".join(lines[:30])  # Show up to 30
        if len(joined_members) > 30:
            desc += f"\n… and {len(joined_members) - 30} more."

        embed = discord.Embed(
            title=f"📋 Members Joined (Last {days} day{'s' if days > 1 else ''})",
            description=desc,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Only includes members still in {ctx.guild.name}.")

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(JoinedToday(bot))
