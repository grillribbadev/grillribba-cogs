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
    def legendary_arrival(
        character: dict,
        ending: int,
        starting_bid: int,
        image_url: str | None = None,
    ) -> discord.Embed:
        """Build the high-visibility announcement for a legendary pool draw."""
        embed = discord.Embed(
            title="A LEGENDARY CHARACTER HAS ARRIVED",
            description=(
                f"## {character['name']}\n\n"
                f"Starting Bid: **{format_berries(starting_bid)}**\n"
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
    def balance(member: discord.Member, amount: int, reserved: int = 0) -> discord.Embed:

        embed = discord.Embed(
            title="💰 Balance",
            color=COLOR_DEFAULT,
        )

        embed.description = (
            f"{member.mention}\n\n"
            f"Balance: **{format_berries(amount)}**\n"
            f"Reserved on current bid: **{format_berries(reserved)}**\n"
            f"Available to bid: **{format_berries(max(0, amount - reserved))}**"
        )

        return embed

    @staticmethod
    def collection(member: discord.Member, characters: list[tuple[dict, str]]):

        embed = discord.Embed(
            title=f"{member.display_name}'s Collection",
            color=COLOR_DEFAULT,
        )

        if not characters:
            embed.description = "No characters owned."
            return embed

        lines = []

        for character, status in characters:
            lines.append(
                f"• **{character['name']}** ({character['rarity']}) - {status}"
            )

        embed.description = "\n".join(lines)

        return embed

    @staticmethod
    def bank(total: int) -> discord.Embed:

        embed = discord.Embed(
            title="🏦 The Auction House Vault",
            description=(
                f"The house has skimmed **{format_berries(total)}** in auction fees so far. 💰\n"
                "Every queued sale pays a 5% cut straight into this vault."
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