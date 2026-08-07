from __future__ import annotations

import asyncio
import random
from typing import Any

import aiohttp
import discord

from redbot.core import Config

from .constants import (
    ANTI_SNIPE_EXTENSION,
    ANTI_SNIPE_THRESHOLD,
    AUCTION_TAX,
    BID_COOLDOWN,
    CHARACTER_DATA_FILE,
    DEFAULT_AUCTION_DURATION,
    DEFAULT_AUCTION_INTERVAL,
    INVALID_BID_LIMIT,
    MAX_ANTI_SNIPE,
    MINIMUM_BID_INCREMENT,
)
from .utils import clean_name, format_berries, format_duration, utc_timestamp
from .views import AuctionEmbeds


class AuctionManager:
    """Controller for the active auction lifecycle and bid processing."""

    def __init__(self, cog):
        self.cog = cog
        self.config: Config = cog.config
        self.image_cache: dict[int, str] = {}

    async def background_loop(self):
        """Background loop that owns automatic auction scheduling."""
        await self.cog.bot.wait_until_ready()

        try:
            while True:
                await asyncio.sleep(1)

                if not await self.config.auction_running():
                    continue

                current = await self.get_current_auction()

                if current:
                    if current.get("ends_at", 0) <= utc_timestamp():
                        await self.finish_auction()
                    continue

                interval = await self.config.auction_interval()
                last_started = await self.config.last_auction_started()
                if not last_started:
                    await self.start_auction()
                    continue

                if utc_timestamp() - last_started >= interval:
                    await self.start_auction()
        except asyncio.CancelledError:
            pass

    async def begin(self) -> bool:
        """Start the automatic auction loop."""
        await self.config.auction_running.set(True)
        await self.config.last_auction_started.set(utc_timestamp())
        if not await self.get_current_auction():
            await self.start_auction()
        return True

    async def stop(self) -> None:
        """Stop the automatic loop and leave any active auction intact."""
        await self.config.auction_running.set(False)

    async def status(self) -> dict[str, Any]:
        """Return a small status snapshot for command use."""
        current = await self.get_current_auction()
        return {
            "running": await self.config.auction_running(),
            "channel": await self.config.auction_channel(),
            "duration": await self.config.auction_duration(),
            "interval": await self.config.auction_interval(),
            "current": current is not None,
            "current_character": current.get("character_id") if current else None,
            "ending": current.get("ends_at") if current else None,
        }

    async def force(self) -> None:
        """Force-start the next auction immediately."""
        await self.start_auction()

    async def skip(self) -> None:
        """Cancel the current auction and advance the queue."""
        state = await self.get_current_auction()
        if state:
            await self.clear_current_auction()
        await self.start_auction()

    async def start_auction(self) -> bool:
        """Create a new active auction instance."""
        if await self.get_current_auction():
            return False

        character: dict[str, Any] | None = await self.select_character_for_auction()
        if not character:
            return False

        channel_id = await self.config.auction_channel()
        if not channel_id:
            return False

        channel = self.cog.bot.get_channel(channel_id)
        if not channel:
            return False

        duration = await self.config.auction_duration()
        ending = utc_timestamp() + duration

        seller_id = None
        queue = await self.config.queue()
        queue_entry = None
        if queue:
            queue_entry = queue.pop(0)
            seller_id = int(queue_entry.get("seller_id", 0) or 0)

        state = {
            "character_id": int(character["id"]),
            "bid": 1,
            "highest_bidder_id": None,
            "seller_id": seller_id,
            "started_at": utc_timestamp(),
            "ends_at": ending,
            "message_id": None,
            "channel_id": channel_id,
            "invalid_counts": {},
            "ignored_users": [],
            "last_bid_at": {},
            "image_url": await self.get_image_url(character),
        }

        # Preserve queue changes when an auction is pulled from it.
        await self.config.queue.set(queue)

        embed = AuctionEmbeds.auction_start(character, ending)
        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException:
            return False

        state["message_id"] = message.id
        await self.config.current_auction.set(state)
        await self.config.last_auction_started.set(utc_timestamp())
        return True

    async def select_character_for_auction(self) -> dict[str, Any] | None:
        """Return a character chosen for the live auction."""
        queue = await self.config.queue()
        if queue:
            character_id = int(queue[0]["character_id"])
            character = self.cog.characters.get(character_id)
            if character:
                return character

        available_pool = [cid for cid in self.cog.characters.all_ids() if not self.cog.characters.owned(cid)]
        if available_pool:
            selected_id = random.choice(available_pool)
            return self.cog.characters.get(selected_id)

        # Fallback: only player-listed auctions are available once the pool is empty.
        if queue:
            character_id = int(queue[0]["character_id"])
            return self.cog.characters.get(character_id)

        return None

    async def get_current_auction(self) -> dict[str, Any] | None:
        """Return the active auction configuration dictionary."""
        current = await self.config.current_auction()
        if not current:
            return None
        return current

    async def clear_current_auction(self) -> None:
        """Clear active auction state from storage."""
        await self.config.current_auction.set({})

    async def get_active_channel(self) -> discord.TextChannel | None:
        """Resolve the configured auction text channel."""
        channel_id = await self.config.auction_channel()
        if not channel_id:
            return None
        return self.cog.bot.get_channel(channel_id)

    async def handle_bid(self, message: discord.Message) -> None:
        """Handle a numeric bid sent in the configured auction channel."""
        if message.author.bot:
            return

        if not await self.config.auction_running():
            return

        state = await self.get_current_auction()
        if not state:
            return

        if message.channel.id != state.get("channel_id"):
            return

        if not message.content or not message.content.strip().isdigit():
            return

        bid = int(message.content.strip())
        bidder_id = message.author.id

        if not await self.cog.economy.exists(bidder_id):
            return

        character_id = int(state.get("character_id", 0))

        if bidder_id == state.get("seller_id"):
            await self.count_invalid_bid(state, bidder_id)
            return

        owner = self.cog.characters.owner_of(character_id)
        if owner == bidder_id:
            await self.count_invalid_bid(state, bidder_id)
            return

        if bidder_id == state.get("highest_bidder_id"):
            await self.count_invalid_bid(state, bidder_id)
            return

        if len(await self.cog.economy.get_characters(bidder_id)):
            if character_id in await self.cog.economy.get_characters(bidder_id):
                await self.count_invalid_bid(state, bidder_id)
                return

        if bidder_id in state.get("ignored_users", []):
            return

        if not await self.cog.economy.available_balance(bidder_id) >= bid:
            await self.count_invalid_bid(state, bidder_id)
            return

        if bid < 1:
            await self.count_invalid_bid(state, bidder_id)
            return

        current_bid = int(state.get("bid", 1))
        minimum_acceptable = current_bid + MINIMUM_BID_INCREMENT
        if bid < minimum_acceptable:
            await self.count_invalid_bid(state, bidder_id)
            return

        if bidder_id in state.get("last_bid_at", {}):
            elapsed = utc_timestamp() - int(state["last_bid_at"].get(str(bidder_id), 0))
            if elapsed < BID_COOLDOWN:
                await self.count_invalid_bid(state, bidder_id)
                return

        if not await self.cog.economy.reserve(bidder_id, bid):
            await self.count_invalid_bid(state, bidder_id)
            return

        old_highest = state.get("highest_bidder_id")
        if old_highest and old_highest != bidder_id:
            await self.cog.economy.release(old_highest)

        state["bid"] = bid
        state["highest_bidder_id"] = bidder_id
        state["last_bid_at"][str(bidder_id)] = utc_timestamp()

        if state.get("ends_at", 0) - utc_timestamp() <= ANTI_SNIPE_THRESHOLD:
            new_ends = state.get("ends_at", 0) + ANTI_SNIPE_EXTENSION
            max_allowed = state.get("started_at", utc_timestamp()) + (await self.config.auction_duration()) + MAX_ANTI_SNIPE
            if new_ends > max_allowed:
                new_ends = max_allowed
            state["ends_at"] = new_ends

        await self.config.current_auction.set(state)
        await self.update_current_embed(state)

    async def count_invalid_bid(self, state: dict[str, Any], user_id: int) -> None:
        """Track invalid bid attempts and ban a user from the current auction."""
        invalids = state.setdefault("invalid_counts", {})
        invalids[str(user_id)] = int(invalids.get(str(user_id), 0)) + 1
        if invalids.get(str(user_id), 0) >= INVALID_BID_LIMIT:
            ignored = state.setdefault("ignored_users", [])
            if user_id not in ignored:
                ignored.append(user_id)
        await self.config.current_auction.set(state)

    async def update_current_embed(self, state: dict[str, Any]) -> None:
        """Edit the live message embed after a bid update."""
        channel_id = state.get("channel_id")
        message_id = state.get("message_id")
        if not channel_id or not message_id:
            return

        channel = self.cog.bot.get_channel(channel_id)
        if not channel:
            return

        character = self.cog.characters.get(int(state["character_id"]))
        if not character:
            return

        bid = int(state["bid"])
        highest_bidder_id = state.get("highest_bidder_id")
        bidder = self.cog.bot.get_user(highest_bidder_id) if highest_bidder_id else None
        embed = AuctionEmbeds.new_bid(character, bidder, bid, int(state["ends_at"])) if bidder else AuctionEmbeds.auction_start(character, int(state["ends_at"]))

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def finish_auction(self) -> None:
        """End the current auction, transfer money, and deliver any settlement embeds."""
        state = await self.get_current_auction()
        if not state:
            return

        character_id = int(state.get("character_id"))
        character = self.cog.characters.get(character_id)
        if not character:
            await self.clear_current_auction()
            return

        channel_id = state.get("channel_id")
        message_id = state.get("message_id")
        channel = self.cog.bot.get_channel(channel_id) if channel_id else None

        winner_id = state.get("highest_bidder_id")
        bid = int(state.get("bid", 1))
        seller_id = state.get("seller_id")

        if winner_id and bid > 1:
            winner = self.cog.bot.get_user(winner_id)
            if winner:
                price = bid
                owner_before = self.cog.characters.owner_of(character_id)
                if owner_before is not None:
                    self.cog.characters.unassign(character_id)

                await self.cog.economy.finalize_purchase(winner_id)
                await self.cog.economy.add_character(winner_id, character_id)
                await self.cog.characters.assign(character_id, winner_id)

                # Tax payout to original seller if this came from the queue.
                if seller_id:
                    seller_share = int(round(price * (1 - AUCTION_TAX)))
                    await self.cog.economy.deposit(seller_id, seller_share)
                    await self.cog.economy.remove_character(seller_id, character_id)

                embed = AuctionEmbeds.sold(character, winner, price)
            else:
                embed = AuctionEmbeds.no_bids(character)
        else:
            embed = AuctionEmbeds.no_bids(character)

            # A no-bid queue auction should leave the character with the seller.
            # A pool auction should leave the character unowned in the character cache.
            if seller_id:
                self.cog.characters.assign(character_id, seller_id)
            else:
                self.cog.characters.unassign(character_id)

        if channel and message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
            except (discord.NotFound, discord.HTTPException):
                pass

        await self.clear_current_auction()

    async def get_image_url(self, character: dict[str, Any]) -> str | None:
        """Fetch and cache a One Piece Wiki image URL for the character."""
        character_id = int(character.get("id", 0))
        if character_id in self.image_cache:
            return self.image_cache[character_id]

        wiki_title = character.get("wiki")
        if not wiki_title:
            return None

        url = (
            "https://onepiece.fandom.com/api.php"
            "?action=query&prop=pageimages&format=json&origin=*&"
            "piprop=original&titles="
            f"{wiki_title}"
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        pages = payload.get("query", {}).get("pages", {})
        if not pages:
            return None

        for page_data in pages.values():
            image = page_data.get("original", {}).get("source")
            if image:
                self.image_cache[character_id] = image
                return image

        return None
