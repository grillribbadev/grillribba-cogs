import discord
from redbot.core import commands
import datetime

class JoinedToday(commands.Cog):
    """Track and list members who joined in the last N days."""

    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.command(name="joinedlist")
    async def joined_list(self, ctx: commands.Context, days: int = 1):
        """
        Show members who joined in the last N days (paginated).
        """
        now = datetime.datetime.utcnow()
        cutoff = now - datetime.timedelta(days=days)

        members = [
            m for m in ctx.guild.members
            if m.joined_at and m.joined_at > cutoff
        ]
        members.sort(key=lambda m: m.joined_at)

        if not members:
            return await ctx.send(f"❌ No members joined in the last {days} days.")

        # Split into pages
        page_size = 10
        pages = [
            members[i:i + page_size] for i in range(0, len(members), page_size)
        ]

        class Paginator(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.current = 0

            def format_page(self):
                embed = discord.Embed(
                    title=f"📋 Members Joined in Last {days} Day(s)",
                    color=discord.Color.blurple()
                )
                embed.set_footer(text=f"Page {self.current+1}/{len(pages)} • Total: {len(members)}")

                desc = []
                for m in pages[self.current]:
                    ts = int(m.joined_at.timestamp())
                    desc.append(f"👤 {m.mention} (`{m.id}`) • <t:{ts}:R>")
                embed.description = "\n".join(desc)
                return embed

            @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.secondary)
            async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current > 0:
                    self.current -= 1
                    await interaction.response.edit_message(embed=self.format_page(), view=self)

            @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current < len(pages) - 1:
                    self.current += 1
                    await interaction.response.edit_message(embed=self.format_page(), view=self)

            @discord.ui.button(label="❌ Close", style=discord.ButtonStyle.danger)
            async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.message.delete()

        view = Paginator()
        await ctx.send(embed=view.format_page(), view=view)
