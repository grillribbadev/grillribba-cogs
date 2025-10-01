from __future__ import annotations
import random
import discord
from redbot.core import commands
from redbot.core.bot import Red


class WheelCog(commands.Cog):
    """Spin the wheel: pick random winners from a role."""

    def __init__(self, bot: Red):
        self.bot = bot

    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.guild)  # 1 use per guild every 10s
    @commands.hybrid_command(name="wheel")
    async def wheel(
        self,
        ctx: commands.Context,
        role: discord.Role,
        amount: int = 1,
    ):
        """
        Pick one or more random winners from a role.

        Examples:
        `[p]wheel @SomeRole`
        `[p]wheel 123456789012345678 3`
        """
        members = [m for m in role.members if not m.bot]
        if not members:
            return await ctx.reply(
                embed=discord.Embed(
                    title="❌ No Eligible Members",
                    description=f"No eligible members found in {role.mention}.",
                    color=discord.Color.red(),
                )
            )

        # Clamp amount so it's not bigger than the member count
        amount = max(1, min(amount, len(members)))

        winners = random.sample(members, k=amount)

        # Build message
        if amount == 1:
            desc = f"🎉 The winner from {role.mention} is **{winners[0].mention}**!"
        else:
            mentions = ", ".join(m.mention for m in winners)
            desc = f"🎉 The {amount} winners from {role.mention} are:\n{mentions}"

        embed = discord.Embed(
            title="🎡 Wheel of Fate",
            description=desc,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")

        # Always include the role mention so it pings, even if passed by ID
        await ctx.send(content=role.mention, embed=embed)
