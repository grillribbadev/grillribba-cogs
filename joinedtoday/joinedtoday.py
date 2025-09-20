import discord
from discord.ext import commands
from redbot.core import commands as redcommands
from datetime import datetime, timedelta, timezone

class JoinedToday(redcommands.Cog):
    """Track members who joined recently."""

    def __init__(self, bot):
        self.bot = bot

    @redcommands.guild_only()
    @redcommands.command(name="joinedcount")
    async def joined_count(self, ctx, days: int = 1):
        """Show how many members joined in the last X days (default 1)."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        count = sum(1 for m in ctx.guild.members if m.joined_at and m.joined_at > cutoff)
        await ctx.send(f"📊 **{count}** members joined in the last **{days} day(s)**.")

    @redcommands.guild_only()
    @redcommands.command(name="joinedlist")
    async def joined_list(self, ctx, days: int = 1):
        """List members who joined in the last X days with pagination (default 1)."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        members = [m for m in ctx.guild.members if m.joined_at and m.joined_at > cutoff]

        if not members:
            return await ctx.send(f"ℹ️ No members joined in the last **{days} day(s)**.")

        members.sort(key=lambda m: m.joined_at)

        pages = []
        page_size = 10
        for i in range(0, len(members), page_size):
            chunk = members[i:i + page_size]
            desc = []
            for m in chunk:
                ts = int(m.joined_at.replace(tzinfo=timezone.utc).timestamp())
                desc.append(f"👤 {m.mention} • **{m.display_name}** (`{m.id}`) • <t:{ts}:R>")
            embed = discord.Embed(
                title=f"📋 Members Joined in Last {days} Day(s)",
                description="\n".join(desc),
                color=discord.Color.blurple()
            )
            embed.set_footer(text=f"Page {i//page_size + 1}/{(len(members)-1)//page_size + 1} • Total: {len(members)}")
            pages.append(embed)

        # --- Pagination with Buttons ---
        class Paginator(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.current = 0

            async def update(self, interaction: discord.Interaction):
                await interaction.response.edit_message(embed=pages[self.current], view=self)

            @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary)
            async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current > 0:
                    self.current -= 1
                    await self.update(interaction)

            @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
            async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current < len(pages) - 1:
                    self.current += 1
                    await self.update(interaction)

            @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
            async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.message.delete()

        view = Paginator()
        await ctx.send(embed=pages[0], view=view)

async def setup(bot):
    await bot.add_cog(JoinedToday(bot))
