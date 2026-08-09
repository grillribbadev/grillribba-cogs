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


class CharacterListView(discord.ui.View):
    """Private paginated character roster with a rarity filter."""

    RARITY_ORDER = ("normal", "common", "uncommon", "rare", "epic", "legendary", "mythical")

    def __init__(self, cog, owner_id: int, rarity_filter: str = "normal"):
        super().__init__(timeout=180)
        self.cog = cog
        self.owner_id = owner_id
        self.rarity_filter = rarity_filter
        self.page = 0
        self.page_size = 10

        options = [discord.SelectOption(label="All Rarities", value="all", default=rarity_filter == "all")]
        options.extend(
            discord.SelectOption(
                label=rarity.title(),
                value=rarity,
                default=rarity_filter == rarity,
            )
            for rarity in self.RARITY_ORDER
        )
        self.rarity_select = discord.ui.Select(
            placeholder="Choose a rarity…",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.rarity_select.callback = self._select_rarity
        self.add_item(self.rarity_select)
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This character list belongs to another user.", ephemeral=True)
        return False

    def _entries(self) -> list[tuple[dict, int | None]]:
        characters = self.cog.characters.all()
        if self.rarity_filter != "all":
            characters = [
                character
                for character in characters
                if str(character.get("rarity", "")).lower() == self.rarity_filter
            ]
        return sorted(
            [
                (character, self.cog.characters.owner_of(int(character["id"])))
                for character in characters
            ],
            key=lambda entry: entry[0].get("name", "").casefold(),
        )

    def _total_pages(self) -> int:
        return max(1, (len(self._entries()) + self.page_size - 1) // self.page_size)

    def _update_buttons(self) -> None:
        total_pages = self._total_pages()
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= total_pages - 1

    def _embed(self) -> discord.Embed:
        return AuctionEmbeds.character_list(
            self._entries(),
            self.rarity_filter,
            self.page,
            self.page_size,
        )

    async def _select_rarity(self, interaction: discord.Interaction) -> None:
        self.rarity_filter = self.rarity_select.values[0]
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._embed(), view=self)