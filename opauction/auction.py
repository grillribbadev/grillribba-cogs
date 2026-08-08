from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any
from urllib.parse import quote

import aiohttp
import discord

from redbot.core import Config

from .constants import (
    ANTI_SNIPE_EXTENSION,
    ANTI_SNIPE_THRESHOLD,
    AUCTION_TAX,
    BID_COOLDOWN,
    DEFAULT_AUCTION_DURATION,
    DEFAULT_AUCTION_INTERVAL,
    GOING_ONCE_SECONDS,
    GOING_THREE_SECONDS,
    GOING_TWICE_SECONDS,
    INVALID_BID_LIMIT,
    MAX_ANTI_SNIPE,
    MINIMUM_BID_INCREMENT,
    NO_BID_CLOSE_SECONDS,
)
from .utils import clean_name, format_berries, format_duration, utc_timestamp
from .views import AuctionEmbeds

log = logging.getLogger("red.opauction")


class AuctionManager:
    """Controller for the active auction lifecycle and bid processing."""

    def __init__(self, cog):
        self.cog = cog
        self.config: Config = cog.config
        self.image_cache: dict[int, str] = {}
        self._start_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

    async def background_loop(self):
        """Background loop that owns automatic auction scheduling."""
        await self.cog.bot.wait_until_ready()

        while True:
            await asyncio.sleep(1)

            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A single bad tick must never kill the whole scheduling loop.
                log.exception("Unhandled error in OPAuction background loop tick")

    async def _tick(self) -> None:
        """Run one iteration of the auction scheduling/countdown logic."""
        if not await self.config.auction_running():
            return

        current = await self.get_current_auction()

        if current:
            now = utc_timestamp()
            started_at = int(current.get("started_at", now))
            ends_at = int(current.get("ends_at", now + 1))

            if now >= ends_at:
                await self.finish_auction()
                return

            bid = int(current.get("bid", 1))
            if bid <= 1:
                elapsed = now - started_at
            else:
                elapsed = now - int(current.get("last_bid_time", started_at))

            await self._announce_countdown(current, elapsed)

            # No bids at all close on the shorter no-bid timer; an active
            # bid closes once "going three" has been reached and held.
            close_after = NO_BID_CLOSE_SECONDS if bid <= 1 else GOING_THREE_SECONDS
            if elapsed >= close_after:
                await self.finish_auction()
            return

        interval = await self.config.auction_interval()
        last_started = await self.config.last_auction_started()
        if not last_started:
            await self.start_auction()
            return

        if utc_timestamp() - last_started >= interval:
            await self.start_auction()

    async def _announce_countdown(self, current: dict[str, Any], elapsed: int) -> None:
        """Send any due 'going once/twice/three' messages, each only once per auction."""
        async with self._start_lock:
            stored_current = await self.get_current_auction()
            if not stored_current or stored_current.get("message_id") != current.get("message_id"):
                return

            stages = (
                (GOING_ONCE_SECONDS, "going_once_issued", "Going once..."),
                (GOING_TWICE_SECONDS, "going_twice_issued", "Going twice..."),
                (GOING_THREE_SECONDS, "going_three_issued", "Going three..."),
            )
            for threshold, flag, text in stages:
                if elapsed < threshold or stored_current.get(flag):
                    continue
                channel = self.cog.bot.get_channel(int(stored_current.get("channel_id", 0)))
                if channel:
                    try:
                        await channel.send(text)
                    except discord.HTTPException:
                        pass
                stored_current[flag] = True
                await self.config.current_auction.set(stored_current)

    async def begin(self) -> bool:
        """Start the automatic auction loop and immediately post a live auction when possible."""
        await self.config.auction_running.set(True)

        current = await self.get_current_auction()
        if current:
            # Only trust leftover state if its message is real and not expired;
            # otherwise it's a stale auction from a prior run and must be cleared.
            if await self._current_auction_is_live(current):
                return True
            await self.clear_current_auction()

        # Don't stamp last_auction_started here: if start_auction fails below,
        # the background loop must be free to retry on its very next tick.
        return await self.start_auction()

    async def _current_auction_is_live(self, current: dict[str, Any]) -> bool:
        """Return True only when the stored auction still has a real, unexpired message."""
        message_id = current.get("message_id")
        channel_id = current.get("channel_id")
        if not message_id or not channel_id:
            return False

        if utc_timestamp() >= int(current.get("ends_at", 0)):
            return False

        channel = await self.resolve_channel(int(channel_id))
        if not channel:
            return False

        try:
            await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

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
        """Create one auction at a time, even when several triggers arrive together."""
        async with self._start_lock:
            return await self._start_auction()

    async def _start_auction(self) -> bool:
        """Create a new active auction instance."""
        if await self.get_current_auction():
            return False

        queue = await self.config.queue()
        last_source = await self.config.last_auction_source()

        character: dict[str, Any] | None = None
        from_queue = False
        queue_entry = queue[0] if queue else None
        available_pool = [cid for cid in self.cog.characters.all_ids() if not self.cog.characters.owned(cid)]

        if last_source != "queue" and queue_entry:
            queued_character_id = int(queue[0]["character_id"])
            character = self.cog.characters.get(queued_character_id)
            from_queue = character is not None

        if not character and last_source != "pool" and available_pool:
            character = self.cog.characters.get(random.choice(available_pool))

        if not character and available_pool:
            character = self.cog.characters.get(random.choice(available_pool))

        if not character and queue_entry:
            queued_character_id = int(queue[0]["character_id"])
            character = self.cog.characters.get(queued_character_id)
            from_queue = character is not None

        if not character:
            return False

        channel_id = await self.config.auction_channel()
        if not channel_id:
            return False

        channel = await self.resolve_channel(channel_id)
        if not channel:
            return False

        if not isinstance(channel, discord.TextChannel):
            return False

        duration = await self.config.auction_duration()
        started_at = utc_timestamp()
        ends_at = started_at + duration

        seller_id = None
        starting_bid = 1
        if from_queue:
            seller_id = int(queue_entry.get("seller_id", 0) or 0)
            starting_bid = max(1, int(queue_entry.get("starting_bid", 1) or 1))

        state = {
            "character_id": int(character["id"]),
            "bid": starting_bid,
            "highest_bidder_id": None,
            "seller_id": seller_id,
            "started_at": started_at,
            "ends_at": ends_at,
            "message_id": None,
            "channel_id": channel_id,
            "invalid_counts": {},
            "ignored_users": [],
            "last_bid_at": {},
            "image_url": await self.get_image_url(character),
            "last_bid_time": started_at,
            "going_once_issued": False,
            "going_twice_issued": False,
            "going_three_issued": False,
            "sold": False,
        }

        image_url = state.get("image_url")
        seller = self.cog.bot.get_user(seller_id) if seller_id else None
        embed = AuctionEmbeds.auction_start(
            character,
            int(state["ends_at"]),
            starting_bid=starting_bid,
            image_url=image_url,
            seller=seller,
        )
        try:
            message = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return False

        state["message_id"] = message.id
        await self.config.current_auction.set(state)
        if from_queue:
            queue.pop(0)
            await self.config.queue.set(queue)
            await self.config.last_auction_source.set("queue")
        else:
            await self.config.last_auction_source.set("pool")
        await self.config.last_auction_started.set(utc_timestamp())
        return True

    async def get_current_auction(self) -> dict[str, Any] | None:
        """Return the active auction configuration dictionary."""
        current = await self.config.current_auction()
        if not current:
            return None
        return current

    async def resolve_channel(self, channel_id: int) -> discord.TextChannel | None:
        """Resolve a configured channel from cache or fetch it from Discord when needed."""
        channel = self.cog.bot.get_channel(channel_id)
        if channel:
            return channel

        try:
            channel = await self.cog.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def clear_current_auction(self) -> None:
        """Clear active auction state from storage."""
        await self.config.current_auction.set({})

    async def get_active_channel(self) -> discord.TextChannel | None:
        """Resolve the configured auction text channel."""
        channel_id = await self.config.auction_channel()
        if not channel_id:
            return None
        return await self.resolve_channel(int(channel_id))

    async def handle_bid(self, message: discord.Message) -> bool:
        """Handle a numeric bid sent in the configured auction channel."""
        async with self._state_lock:
            return await self._handle_bid(message)

    async def _handle_bid(self, message: discord.Message) -> bool:
        """Validate and commit a bid while auction settlement is paused."""
        if message.author.bot:
            return False

        if not await self.config.auction_running():
            await message.reply("Auctions are not currently running.")
            return False

        state = await self.get_current_auction()
        if not state:
            await message.reply("There is no active auction to bid on.")
            return False

        if message.channel.id != state.get("channel_id"):
            return False

        if not message.content or not message.content.strip().isdigit():
            return False

        bid = int(message.content.strip())
        bidder_id = message.author.id

        if not await self.cog.economy.exists(bidder_id):
            await message.reply("Use `.auction start` before bidding.")
            return False

        if utc_timestamp() >= int(state.get("ends_at", 0)):
            await message.reply("This auction has already ended.")
            return False

        character_id = int(state.get("character_id", 0))

        if bidder_id == state.get("seller_id"):
            await self.count_invalid_bid(state, bidder_id)
            await message.reply("You cannot bid on your own character.")
            return False

        owner = self.cog.characters.owner_of(character_id)
        if owner == bidder_id:
            await self.count_invalid_bid(state, bidder_id)
            await message.reply("You already own this character.")
            return False

        if bidder_id == state.get("highest_bidder_id"):
            await self.count_invalid_bid(state, bidder_id)
            await message.reply("You are already the highest bidder.")
            return False

        if len(await self.cog.economy.get_characters(bidder_id)):
            if character_id in await self.cog.economy.get_characters(bidder_id):
                await self.count_invalid_bid(state, bidder_id)
                await message.reply("You already own this character.")
                return False

        if not await self.cog.economy.available_balance(bidder_id) >= bid:
            await self.count_invalid_bid(state, bidder_id)
            available = await self.cog.economy.available_balance(bidder_id)
            await message.reply(
                f"You need {format_berries(bid)} but only have {format_berries(available)} available."
            )
            return False

        if bid < 1:
            await self.count_invalid_bid(state, bidder_id)
            await message.reply("Bids must be at least ฿1.")
            return False

        current_bid = int(state.get("bid", 1))
        minimum_acceptable = (
            current_bid
            if state.get("highest_bidder_id") is None
            else current_bid + MINIMUM_BID_INCREMENT
        )
        if bid < minimum_acceptable:
            await self.count_invalid_bid(state, bidder_id)
            await message.reply(f"The minimum valid bid is {format_berries(minimum_acceptable)}.")
            return False

        last_bid_at = state.get("last_bid_at", {})
        last_bid_key = str(bidder_id)
        if last_bid_key in last_bid_at:
            elapsed = utc_timestamp() - int(last_bid_at.get(last_bid_key, 0))
            if elapsed < BID_COOLDOWN:
                await self.count_invalid_bid(state, bidder_id)
                await message.reply("You are bidding too quickly. Please wait a moment.")
                return False

        if not await self.cog.economy.reserve(bidder_id, bid):
            await self.count_invalid_bid(state, bidder_id)
            await message.reply("You do not have enough available beri for that bid.")
            return False

        old_highest = state.get("highest_bidder_id")
        if old_highest and old_highest != bidder_id:
            await self.cog.economy.release(old_highest)

        state["bid"] = bid
        state["highest_bidder_id"] = bidder_id
        bid_time = utc_timestamp()
        state["last_bid_time"] = bid_time
        state["last_bid_at"][last_bid_key] = bid_time
        state["going_once_issued"] = False
        state["going_twice_issued"] = False
        state["going_three_issued"] = False

        # The auction settles after the countdown from the latest bid, so its
        # visible deadline must be reset with every accepted bid as well.
        state["ends_at"] = bid_time + GOING_THREE_SECONDS

        await self.config.current_auction.set(state)
        await self.update_current_embed(state)

        return True

    async def count_invalid_bid(self, state: dict[str, Any], user_id: int) -> None:
        """Track invalid bid attempts without locking the user out of the auction."""
        invalids = state.setdefault("invalid_counts", {})
        invalids[str(user_id)] = int(invalids.get(str(user_id), 0)) + 1
        await self.config.current_auction.set(state)

    async def update_current_embed(self, state: dict[str, Any]) -> None:
        """Edit the live message embed after a bid update."""
        channel_id = state.get("channel_id")
        message_id = state.get("message_id")
        if not channel_id:
            return

        channel = await self.resolve_channel(int(channel_id))
        if not channel:
            return

        character = self.cog.characters.get(int(state["character_id"]))
        if not character:
            return

        bid = int(state["bid"])
        highest_bidder_id = state.get("highest_bidder_id")
        bidder = self.cog.bot.get_user(highest_bidder_id) if highest_bidder_id else None
        image_url = state.get("image_url")
        seller_id = state.get("seller_id")
        seller = self.cog.bot.get_user(seller_id) if seller_id else None
        embed = (
            AuctionEmbeds.new_bid(character, bidder, bid, int(state["ends_at"]), image_url=image_url, seller=seller)
            if bidder
            else AuctionEmbeds.auction_start(character, int(state["ends_at"]), image_url=image_url, seller=seller)
        )

        if not message_id:
            try:
                message = await channel.send(embed=embed)
                state["message_id"] = message.id
                await self.config.current_auction.set(state)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass
            return

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except discord.NotFound:
            try:
                message = await channel.send(embed=embed)
                state["message_id"] = message.id
                await self.config.current_auction.set(state)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass
        except (discord.HTTPException, discord.Forbidden):
            pass

    async def finish_auction(self) -> None:
        """End the current auction, transfer money, and deliver any settlement embeds."""
        async with self._state_lock:
            await self._finish_auction()

    async def _finish_auction(self) -> None:
        """Settle the stored auction after all in-flight bids have completed."""
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
        channel = await self.resolve_channel(int(channel_id)) if channel_id else None

        winner_id = state.get("highest_bidder_id")
        bid = int(state.get("bid", 1))
        seller_id = state.get("seller_id")

        try:
            if winner_id and bid > 1:
                winner = self.cog.bot.get_user(winner_id)
                if not winner:
                    try:
                        winner = await self.cog.bot.fetch_user(winner_id)
                    except (discord.NotFound, discord.HTTPException):
                        winner = None
                if winner:
                    price = bid
                    owner_before = self.cog.characters.owner_of(character_id)
                    if owner_before is not None:
                        self.cog.characters.unassign(character_id)

                    await self.cog.economy.finalize_purchase(winner_id)
                    await self.cog.economy.add_character(winner_id, character_id)
                    self.cog.characters.assign(character_id, winner_id)

                    # Tax payout to original seller if this came from the queue.
                    if seller_id:
                        seller_share = int(round(price * (1 - AUCTION_TAX)))
                        fee = price - seller_share
                        await self.cog.economy.deposit(seller_id, seller_share)
                        await self.cog.economy.remove_character(seller_id, character_id)
                        await self._record_fee(fee)

                    embed = AuctionEmbeds.sold(character, winner, price, image_url=state.get("image_url"))
                    sold_text = f"Sold to {winner.mention} for {format_berries(price)}."
                else:
                    # The winner could not be resolved at all (left every mutual
                    # guild); free their reserved beri instead of locking it forever.
                    await self.cog.economy.release(winner_id)
                    embed = AuctionEmbeds.no_bids(character, image_url=state.get("image_url"))
                    sold_text = None
            else:
                embed = AuctionEmbeds.no_bids(character, image_url=state.get("image_url"))
                sold_text = None

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
                    if sold_text:
                        await channel.send(sold_text)
                except (discord.NotFound, discord.HTTPException):
                    pass
        finally:
            # Always clear, even on error: an uncleared auction would otherwise
            # re-run this settlement (and re-charge the winner) every tick forever.
            await self.clear_current_auction()

    async def _record_fee(self, amount: int) -> None:
        """Add to the running total of 5% fees skimmed from queue sales."""
        if amount <= 0:
            return
        total = await self.cog.config.total_fees()
        await self.cog.config.total_fees.set(total + amount)

    async def get_image_url(self, character: dict[str, Any]) -> str | None:
        """Fetch and cache a One Piece Wiki image URL for the character."""
        character_id = int(character.get("id", 0))
        if character_id in self.image_cache:
            return self.image_cache[character_id]

        wiki_title = character.get("wiki")
        if not wiki_title:
            return None

        title = quote(str(wiki_title).replace(" ", "_"), safe="")
        api_url = (
            "https://onepiece.fandom.com/api.php"
            "?action=query&prop=pageimages&format=json&origin=*&"
            "piprop=original&titles="
            f"{title}"
        )

        headers = {"User-Agent": "OPAuction/1.0 (+https://github.com/Grillribba)"}

        payload = None
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(api_url, timeout=10) as response:
                    if response.status == 200:
                        payload = await response.json()
                    else:
                        payload = None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            payload = None

        if payload:
            pages = payload.get("query", {}).get("pages", {})
            for page_data in pages.values():
                image = (
                    page_data.get("original", {}).get("source")
                    or page_data.get("thumbnail", {}).get("source")
                )
                if image:
                    self.image_cache[character_id] = image
                    return image

        # Fallback: scrape the wiki page HTML for its OpenGraph image metadata.
        page_url = f"https://onepiece.fandom.com/wiki/{title}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(page_url, timeout=10) as response:
                    if response.status != 200:
                        return None
                    html = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        match = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.IGNORECASE)
        if match:
            image_url = match.group(1)
            self.image_cache[character_id] = image_url
            return image_url

        return None
