import discord
from redbot.core import commands
from datetime import datetime, timedelta, timezone

class JoinTracker(commands.Cog):
    """Track how many members joined recently."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------
    # Count only
    # ------------------------
    @commands.guild_only()
    @commands.command(name="joinedcount")
    async def joined_count(self, ctx: commands.Context, days: int = 1):
        """
        Show how many members joined in the last X days.
        Defaults to today (1 day).
        """
        if days < 1:
            return await ctx.send("❌ Days must be at least 1.")

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        # Only count members who are STILL in the server
        members = [m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff]

        await ctx.send(
            embed=discord.Embed(
                title="📊 Join Count",
                description=f"**{len(members)}** members joined in the last **{days} day(s)**.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
        )

    # ------------------------
    # Detailed list
    # ------------------------
    @commands.guild_only()
    @commands.command(name="joinedlist")
    async def joined_list(self, ctx: commands.Context, days: int = 1):
        """
        List members who joined in the last X days (default: today).
        Only shows members still in the server.
        """
        if days < 1:
            return await ctx.send("❌ Days must be at least 1.")

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        members = [m for m in ctx.guild.members if m.joined_at and m.joined_at >= cutoff]

        if not members:
            return await ctx.send(
                embed=discord.Embed(
                    title="📋 Joined Members",
                    description=f"No members joined in the last {days} day(s).",
                    color=discord.Color.orange()
                )
            )

        # Sort by join date
        members.sort(key=lambda m: m.joined_at)

        # Split into chunks of 20 members each
        chunks = [members[i:i+20] for i in range(0, len(members), 20)]
        for i, chunk in enumerate(chunks, start=1):
            desc = "\n".join(
                f"• {m.mention} — joined <t:{int(m.joined_at.timestamp())}:R>"
                for m in chunk
            )
            embed = discord.Embed(
                title=f"📋 Joined Members (Last {days}d) — Page {i}/{len(chunks)}",
                description=desc,
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc)
            )
            await ctx.send(embed=embed)
