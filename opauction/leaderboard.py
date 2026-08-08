from __future__ import annotations

import discord

from .views import AuctionEmbeds


class BalanceLeaderboardView(discord.ui.View):
    """Public page controls for one balance leaderboard message."""

    def __init__(self, entries: list[tuple[int, int]], owner_id: int, page: int = 0):
        super().__init__(timeout=180)
        self.entries = entries
        self.owner_id = owner_id
        self.page = page
        self.page_size = 10
        self.total_pages = max(1, (len(entries) + self.page_size - 1) // self.page_size)
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This leaderboard menu belongs to another user.", ephemeral=True)
        return False

    def _update_buttons(self) -> None:
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=AuctionEmbeds.leaderboard(self.entries, self.page, self.page_size),
            view=self,
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=AuctionEmbeds.leaderboard(self.entries, self.page, self.page_size),
            view=self,
        )