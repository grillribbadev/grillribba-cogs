import discord
from datetime import datetime, timedelta, timezone

from redbot.core import Config
from redbot.core import commands as redcommands


DEFAULT_GUILD = {
    "leave_log": [],
}

class JoinedToday(redcommands.Cog):
    """Track members who joined recently."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=602481771104, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

    def _normalize_days(self, days: int) -> int:
        return max(1, int(days))

    def _cutoff_for_days(self, days: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=self._normalize_days(days))

    def _current_members_joined_since(self, guild: discord.Guild, cutoff: datetime) -> list[discord.Member]:
        return [
            member
            for member in guild.members
            if member.guild.id == guild.id and member.joined_at and member.joined_at > cutoff
        ]

    async def _recent_leavers_since(self, guild: discord.Guild, cutoff: datetime) -> list[dict]:
        entries = await self.config.guild(guild).leave_log()
        if not isinstance(entries, list):
            return []

        recent: list[dict] = []
        kept: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                left_at = datetime.fromisoformat(str(entry.get("left_at", "")))
            except Exception:
                continue
            if left_at.tzinfo is None:
                left_at = left_at.replace(tzinfo=timezone.utc)
            if left_at > cutoff:
                recent.append(entry)
            if left_at > datetime.now(timezone.utc) - timedelta(days=30):
                kept.append(entry)

        if kept != entries:
            await self.config.guild(guild).leave_log.set(kept)

        recent.sort(key=lambda item: item.get("left_at", ""), reverse=True)
        return recent

    @redcommands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        entry = {
            "member_id": member.id,
            "name": str(member),
            "display_name": member.display_name,
            "left_at": datetime.now(timezone.utc).isoformat(),
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        }

        async with self.config.guild(member.guild).leave_log() as leave_log:
            leave_log.append(entry)

            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            leave_log[:] = [
                item
                for item in leave_log
                if isinstance(item, dict)
                and item.get("left_at")
                and self._entry_is_new_enough(item, cutoff)
            ]

    def _entry_is_new_enough(self, entry: dict, cutoff: datetime) -> bool:
        try:
            left_at = datetime.fromisoformat(str(entry.get("left_at", "")))
        except Exception:
            return False
        if left_at.tzinfo is None:
            left_at = left_at.replace(tzinfo=timezone.utc)
        return left_at > cutoff

    @redcommands.guild_only()
    @redcommands.command(name="joinedcount")
    async def joined_count(self, ctx, days: int = 1):
        """Show how many members joined in the last X days (default 1)."""
        days = self._normalize_days(days)
        cutoff = self._cutoff_for_days(days)
        members = self._current_members_joined_since(ctx.guild, cutoff)
        count = len(members)

        embed = discord.Embed(
            title="📊 Join Count",
            description=f"**{count}** members joined in the last **{days} day(s)**.",
            color=discord.Color.green() if count > 0 else discord.Color.orange()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @redcommands.guild_only()
    @redcommands.command(name="joinedlist")
    async def joined_list(self, ctx, days: int = 1):
        """List members who joined in the last X days with pagination (default 1)."""
        days = self._normalize_days(days)
        cutoff = self._cutoff_for_days(days)
        members = self._current_members_joined_since(ctx.guild, cutoff)

        if not members:
            embed = discord.Embed(
                title="📋 Joined Members",
                description=f"ℹ️ No members joined in the last **{days} day(s)**.",
                color=discord.Color.orange()
            )
            return await ctx.send(embed=embed)

        # Sort newest first (reverse chronological)
        members.sort(key=lambda m: m.joined_at, reverse=True)

        pages = []
        page_size = 8  # fewer per page for readability on phone
        for i in range(0, len(members), page_size):
            chunk = members[i:i + page_size]
            desc = []
            for m in chunk:
                ts = int(m.joined_at.replace(tzinfo=timezone.utc).timestamp())
                desc.append(
                    f"👤 **{m.display_name}** ({m.mention} | `{m.id}`)\n"
                    f"   ⏰ Joined: <t:{ts}:R>\n"
                )
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
                super().__init__(timeout=90)
                self.current = 0

            async def update(self, interaction: discord.Interaction):
                await interaction.response.edit_message(embed=pages[self.current], view=self)

            @discord.ui.button(label="⬅ Previous", style=discord.ButtonStyle.primary)
            async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current > 0:
                    self.current -= 1
                    await self.update(interaction)

            @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.primary)
            async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current < len(pages) - 1:
                    self.current += 1
                    await self.update(interaction)

            @discord.ui.button(label="❌ Close", style=discord.ButtonStyle.danger)
            async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.message.delete()

        view = Paginator()
        await ctx.send(embed=pages[0], view=view)

    @redcommands.guild_only()
    @redcommands.command(name="leftcount")
    async def left_count(self, ctx, days: int = 1):
        """Show how many members left in the last X days (default 1)."""
        days = self._normalize_days(days)
        cutoff = self._cutoff_for_days(days)
        members = await self._recent_leavers_since(ctx.guild, cutoff)
        count = len(members)

        embed = discord.Embed(
            title="📉 Leave Count",
            description=f"**{count}** members left in the last **{days} day(s)**.",
            color=discord.Color.red() if count > 0 else discord.Color.orange(),
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @redcommands.guild_only()
    @redcommands.command(name="leftlist")
    async def left_list(self, ctx, days: int = 1):
        """List members who left in the last X days with pagination (default 1)."""
        days = self._normalize_days(days)
        cutoff = self._cutoff_for_days(days)
        members = await self._recent_leavers_since(ctx.guild, cutoff)

        if not members:
            embed = discord.Embed(
                title="📋 Left Members",
                description=f"ℹ️ No members left in the last **{days} day(s)**.",
                color=discord.Color.orange(),
            )
            return await ctx.send(embed=embed)

        pages = []
        page_size = 8
        for i in range(0, len(members), page_size):
            chunk = members[i:i + page_size]
            desc = []
            for item in chunk:
                try:
                    ts = int(datetime.fromisoformat(item["left_at"]).timestamp())
                except Exception:
                    continue
                display_name = str(item.get("display_name") or item.get("name") or "Unknown Member")
                member_id = item.get("member_id", "unknown")
                desc.append(
                    f"👋 **{display_name}** (`{member_id}`)\n"
                    f"   ⏰ Left: <t:{ts}:R>\n"
                )

            embed = discord.Embed(
                title=f"📋 Members Who Left in Last {days} Day(s)",
                description="\n".join(desc) or "No recent leave records found.",
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"Page {i//page_size + 1}/{(len(members)-1)//page_size + 1} • Total: {len(members)}")
            pages.append(embed)

        class Paginator(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=90)
                self.current = 0

            async def update(self, interaction: discord.Interaction):
                await interaction.response.edit_message(embed=pages[self.current], view=self)

            @discord.ui.button(label="⬅ Previous", style=discord.ButtonStyle.primary)
            async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current > 0:
                    self.current -= 1
                    await self.update(interaction)

            @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.primary)
            async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current < len(pages) - 1:
                    self.current += 1
                    await self.update(interaction)

            @discord.ui.button(label="❌ Close", style=discord.ButtonStyle.danger)
            async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.message.delete()

        view = Paginator()
        await ctx.send(embed=pages[0], view=view)

async def setup(bot):
    await bot.add_cog(JoinedToday(bot))
