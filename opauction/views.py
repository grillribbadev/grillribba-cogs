from __future__ import annotations

import discord

from .constants import (
    COLOR_AUCTION,
    COLOR_DEFAULT,
    COLOR_SUCCESS,
    COLOR_ERROR,
)
from .utils import format_berries, format_duration


class AuctionEmbeds:
    @staticmethod
    def character_info(
        character: dict,
        owner_text: str,
        last_sale_price: int,
        image_url: str | None = None,
    ) -> discord.Embed:
        """Build the detailed character information card."""
        embed = discord.Embed(title=character["name"], color=COLOR_AUCTION)
        embed.add_field(name="Rarity", value=character.get("rarity", "Unknown"), inline=True)
        embed.add_field(name="Arc", value=character.get("arc", "Unknown"), inline=True)
        embed.add_field(name="Owner", value=owner_text, inline=True)
        embed.add_field(
            name="Last Sold Price",
            value=format_berries(last_sale_price) if last_sale_price else "No completed sale yet",
            inline=False,
        )
        embed.set_footer(text=f"Character #{character['id']}")
        if image_url:
            embed.set_image(url=image_url)
        return embed

    @staticmethod
    def character_view(
        character: dict,
        status: str,
        last_sale_price: int,
        image_url: str | None = None,
    ) -> discord.Embed:
        """Build a full character profile for the public roster viewer."""
        embed = discord.Embed(
            title=character["name"],
            description=f"**{character.get('rarity', 'Unknown')} Tier**",
            color=COLOR_AUCTION,
        )
        embed.add_field(name="Arc", value=character.get("arc", "Unknown"), inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(
            name="Last Sale",
            value=format_berries(last_sale_price) if last_sale_price else "No completed sale yet",
            inline=True,
        )
        embed.set_footer(text=f"Character #{character['id']}")
        if image_url:
            embed.set_image(url=image_url)
        return embed

    @staticmethod
    def ping_preferences(selected: list[str]) -> discord.Embed:
        """Build the rarity-ping preference panel."""
        selected_text = ", ".join(rarity.title() for rarity in selected) if selected else "None"
        return discord.Embed(
            title="Auction Rarity Pings",
            description=(
                "Toggle the rarities you want to be mentioned for when they enter the pool auction.\n\n"
                f"Your selections: **{selected_text}**"
            ),
            color=COLOR_AUCTION,
        )

    @staticmethod
    def legendary_arrival(
        character: dict,
        ending: int,
        starting_bid: int,
        image_url: str | None = None,
        seller: discord.abc.User | None = None,
    ) -> discord.Embed:
        """Build the high-visibility announcement for a legendary auction."""
        seller_text = seller.mention if seller else "🏛️ The Auction House"
        embed = discord.Embed(
            title="A LEGENDARY CHARACTER HAS ARRIVED",
            description=(
                f"## {character['name']}\n\n"
                f"Starting Bid: **{format_berries(starting_bid)}**\n"
                f"Seller: {seller_text}\n"
                f"Arc: {character.get('arc', 'Unknown')}\n"
                f"Auction ends <t:{ending}:R>"
            ),
            color=COLOR_AUCTION,
        )

        if image_url:
            embed.set_image(url=image_url)

        return embed

    @staticmethod
    def auction_start(
        character: dict,
        ending: int,
        starting_bid: int = 1,
        image_url: str | None = None,
        seller: discord.abc.User | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🔨 {character['name']}",
            description="A new auction has begun!",
            color=COLOR_AUCTION,
        )

        embed.add_field(
            name="Starting Bid",
            value=format_berries(starting_bid),
            inline=True,
        )

        embed.add_field(
            name="Seller",
            value=seller.mention if seller else "🏛️ The Auction House",
            inline=True,
        )

        embed.add_field(
            name="Rarity",
            value=character.get("rarity", "Unknown"),
            inline=True,
        )

        embed.add_field(
            name="Arc",
            value=character.get("arc", "Unknown"),
            inline=True,
        )

        if image_url:
            embed.set_thumbnail(url=image_url)

        embed.set_footer(text=f"Auction ends <t:{ending}:R>")

        return embed

    @staticmethod
    def new_bid(
        character: dict,
        bidder: discord.Member,
        bid: int,
        ending: int,
        image_url: str | None = None,
        seller: discord.abc.User | None = None,
    ) -> discord.Embed:

        embed = discord.Embed(
            title="🔨 New Highest Bid",
            color=COLOR_AUCTION,
        )

        seller_text = seller.mention if seller else "🏛️ The Auction House"
        embed.description = (
            f"**{character['name']}**\n\n"
            f"Seller: {seller_text}\n"
            f"Highest Bidder: {bidder.mention}\n"
            f"Current Bid: **{format_berries(bid)}**"
        )

        if image_url:
            embed.set_thumbnail(url=image_url)

        embed.set_footer(text=f"Ends <t:{ending}:R>")

        return embed

    @staticmethod
    def sold(
        character: dict,
        winner: discord.abc.User | str,
        price: int,
        image_url: str | None = None,
    ) -> discord.Embed:

        embed = discord.Embed(
            title="🎉 SOLD!",
            color=COLOR_SUCCESS,
        )

        embed.description = (
            f"**{character['name']}**\n\n"
            f"Winner: {winner.mention if not isinstance(winner, str) else winner}\n"
            f"Price: **{format_berries(price)}**"
        )

        if image_url:
            embed.set_thumbnail(url=image_url)

        return embed

    @staticmethod
    def no_bids(character: dict, image_url: str | None = None) -> discord.Embed:

        embed = discord.Embed(
            title="❌ No Bids",
            description=f"No one bid on **{character['name']}**.",
            color=COLOR_ERROR,
        )

        if image_url:
            embed.set_thumbnail(url=image_url)

        return embed

    @staticmethod
    def balance(member: discord.Member, amount: int, reserved: int = 0, debt: int = 0) -> discord.Embed:

        embed = discord.Embed(
            title="💰 Balance",
            color=COLOR_DEFAULT,
        )

        embed.description = (
            f"{member.mention}\n\n"
            f"Balance: **{format_berries(amount)}**\n"
            f"Reserved on current bid: **{format_berries(reserved)}**\n"
            f"Available to bid: **{format_berries(max(0, amount - reserved))}**\n"
            f"Loan debt: **{format_berries(debt)}**"
        )

        return embed

    @staticmethod
    def leaderboard(entries: list[tuple[int, int]], page: int, page_size: int = 10) -> discord.Embed:
        """Build one page of the Auction House beri leaderboard."""
        total_pages = max(1, (len(entries) + page_size - 1) // page_size)
        start = page * page_size
        lines = []
        for rank, (user_id, balance) in enumerate(entries[start:start + page_size], start=start + 1):
            lines.append(f"**{rank}.** <@{user_id}> - **{format_berries(balance)}**")

        embed = discord.Embed(title="🏆 Auction Balance Leaderboard", color=COLOR_AUCTION)
        embed.description = "\n".join(lines) if lines else "No registered players yet."
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        return embed

    @staticmethod
    def collection(member: discord.Member, characters: list[tuple[dict, str, int]]):

        embed = discord.Embed(
            title=f"{member.display_name}'s Collection",
            color=COLOR_DEFAULT,
        )

        if not characters:
            embed.description = "No characters owned."
            return embed

        lines = []

        total_value = 0
        for character, status, last_bought_value in characters:
            total_value += last_bought_value
            value_text = format_berries(last_bought_value) if last_bought_value else "No completed sale"
            lines.append(
                f"• **{character['name']}** ({character['rarity']}) - {status}\n"
                f"  Last bought: **{value_text}**"
            )

        embed.description = "\n".join(lines) + f"\n\n**Total collection value: {format_berries(total_value)}**"

        return embed

    @staticmethod
    def bank(total: int) -> discord.Embed:

        embed = discord.Embed(
            title="🏦 The Auction House Vault",
            description=(
                f"Vault balance: **{format_berries(total)}** 💰\n"
                "Queued sales contribute their 5% fee; pool sales contribute their full price."
            ),
            color=COLOR_DEFAULT,
        )

        return embed

    @staticmethod
    def queue(entries: list[tuple[dict, str]]) -> discord.Embed:

        embed = discord.Embed(
            title="📜 Auction Queue",
            color=COLOR_AUCTION,
        )

        if not entries:
            embed.description = "The auction queue is empty."
            return embed

        lines = []

        for index, (character, seller_text) in enumerate(entries, start=1):
            lines.append(
                f"{index}. **{character['name']}** ({character.get('rarity', 'Unknown')}) — queued by {seller_text}"
            )

        embed.description = "\n".join(lines)

        return embed

    @staticmethod
    def error(message: str):

        return discord.Embed(
            title="❌ Error",
            description=message,
            color=COLOR_ERROR,
        )

    @staticmethod
    def success(message: str):

        return discord.Embed(
            title="✅ Success",
            description=message,
            color=COLOR_SUCCESS,
        )


class AuctionPingView(discord.ui.View):
    """Persistent buttons for selecting pool-auction rarity pings."""

    RARITIES = ("normal", "rare", "epic", "legendary")

    def __init__(self, cog, selected: list[str] | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        selected = set(selected or [])

        for rarity in self.RARITIES:
            button = discord.ui.Button(
                label=rarity.title(),
                style=discord.ButtonStyle.success if rarity in selected else discord.ButtonStyle.secondary,
                custom_id=f"opauction:ping:{rarity}",
            )
            button.callback = self._rarity_callback(rarity)
            self.add_item(button)

        all_button = discord.ui.Button(
            label="All",
            style=discord.ButtonStyle.primary,
            custom_id="opauction:ping:all",
        )
        all_button.callback = self._all_callback
        self.add_item(all_button)

        clear_button = discord.ui.Button(
            label="Clear",
            style=discord.ButtonStyle.danger,
            custom_id="opauction:ping:clear",
        )
        clear_button.callback = self._clear_callback
        self.add_item(clear_button)

    def _rarity_callback(self, rarity: str):
        async def callback(interaction: discord.Interaction):
            preferences = await self.cog.config.user_from_id(interaction.user.id).ping_rarities()
            selected = set(preferences)
            if rarity in selected:
                selected.remove(rarity)
            else:
                selected.add(rarity)
            await self._save(interaction, selected)

        return callback

    async def _all_callback(self, interaction: discord.Interaction):
        await self._save(interaction, set(self.RARITIES))

    async def _clear_callback(self, interaction: discord.Interaction):
        await self._save(interaction, set())

    async def _save(self, interaction: discord.Interaction, selected: set[str]):
        values = [rarity for rarity in self.RARITIES if rarity in selected]
        await self.cog.config.user_from_id(interaction.user.id).ping_rarities.set(values)
        await interaction.response.edit_message(
            embed=AuctionEmbeds.ping_preferences(values),
            view=AuctionPingView(self.cog, values),
        )