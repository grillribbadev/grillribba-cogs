from __future__ import annotations

import asyncio
import logging
import random
import re
import uuid
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
    OFFER_TIMEOUT_SECONDS,
    POOL_STARTING_BIDS,
    RARITY_WEIGHTS,
)
from .utils import clean_name, format_berries, format_duration, utc_timestamp
from .views import AuctionEmbeds

log = logging.getLogger("red.opauction")

# These locks are shared by every loaded OPAuction instance in this process.
# A reload may briefly leave an old background task alive while a new cog loads.
_AUCTION_START_LOCK = asyncio.Lock()
_AUCTION_STATE_LOCK = asyncio.Lock()


class AuctionManager:
    """Controller for the active auction lifecycle and bid processing."""

    def __init__(self, cog):
        self.cog = cog
        self.config: Config = cog.config
        self.image_cache: dict[int, str] = {}
        self._runner_id = ""
        self._start_lock = _AUCTION_START_LOCK
        self._state_lock = _AUCTION_STATE_LOCK

    async def activate_runner(self) -> None:
        """Mark this cog instance as the only instance allowed to update auctions."""
        self._runner_id = uuid.uuid4().hex
        async with self._state_lock:
            await self.config.auction_runner_id.set(self._runner_id)

    async def is_active_runner(self) -> bool:
        """Return whether this loaded cog still owns the auction scheduler lease."""
        return bool(self._runner_id) and await self.config.auction_runner_id() == self._runner_id

    async def _write_current_auction(self, state: dict[str, Any]) -> bool:
        """Persist auction state only while this cog owns the scheduler lease."""
        if not await self.is_active_runner():
            return False
        await self.config.current_auction.set(state)
        return True

    async def background_loop(self):
        """Background loop that owns automatic auction scheduling."""
        await self.cog.bot.wait_until_ready()

        while True:
            await asyncio.sleep(1)

            try:
                if not await self.is_active_runner():
                    return
                await self.cog.notify_recollection_due()
                await self.cog.collect_daily_taxes()
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A single bad tick must never kill the whole scheduling loop.
                log.exception("Unhandled error in OPAuction background loop tick")

    async def _tick(self) -> None:
        """Run one iteration of the auction scheduling/countdown logic."""
        if not await self.is_active_runner():
            return

        await self.cog.expire_pending_offers(OFFER_TIMEOUT_SECONDS)

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

            has_bid = bool(current.get("highest_bidder_id"))
            if not has_bid:
                elapsed = now - started_at
            else:
                elapsed = now - int(current.get("last_bid_time", started_at))

            # No bids at all close on the shorter no-bid timer; an active
            # bid closes once "going three" has been reached and held.
            close_after = NO_BID_CLOSE_SECONDS if not has_bid else GOING_THREE_SECONDS
            if elapsed >= close_after:
                await self.finish_auction()
                return

            await self._announce_countdown(current, elapsed)
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
        async with self._state_lock:
            stored_current = await self.get_current_auction()
            if not stored_current or (
                stored_current.get("character_id") != current.get("character_id")
                or stored_current.get("started_at") != current.get("started_at")
            ):
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
                if not await self._write_current_auction(stored_current):
                    return
                await self.update_current_embed(stored_current)

    async def bump_current_embed(self) -> None:
        """Repost the active auction display after a new human channel message."""
        async with self._state_lock:
            state = await self.get_current_auction()
            if state:
                await self.update_current_embed(state)

    async def begin(self) -> bool:
        """Start the automatic auction loop and immediately post a live auction when possible."""
        await self.config.auction_running.set(True)

        current = await self.get_current_auction()
        if current:
            # Only trust leftover state if its message is real and not expired;
            # otherwise it's a stale auction from a prior run and must be cleared.
            if await self._current_auction_is_live(current):
                return True
            await self._release_highest_bid(current)
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
            await self._release_highest_bid(state)
            await self.clear_current_auction()
        await self.start_auction()

    async def start_auction(self) -> bool:
        """Create one auction at a time, even when several triggers arrive together."""
        if not await self.is_active_runner():
            return False
        async with self._start_lock:
            return await self._start_auction()

    async def _start_auction(self) -> bool:
        """Create a new active auction instance."""
        if await self.get_current_auction():
            return False

        queue = await self.config.queue()
        last_source = await self.config.last_auction_source()
        forced_source = await self.config.forced_next_source()
        forced_character_id = await self.config.forced_next_character_id()

        character: dict[str, Any] | None = None
        from_queue = False
        queue_entry = queue[0] if queue else None
        available_pool = [cid for cid in self.cog.characters.all_ids() if not self.cog.characters.owned(cid)]

        if forced_source == "pool" and forced_character_id:
            forced_character_id = int(forced_character_id)
            if forced_character_id in available_pool:
                character = self.cog.characters.get(forced_character_id)

        if forced_source == "pool" and not character and available_pool:
            character = self._choose_pool_character(available_pool)

        if not character and last_source != "queue" and queue_entry:
            queued_character_id = int(queue[0]["character_id"])
            character = self.cog.characters.get(queued_character_id)
            from_queue = character is not None

        if not character and last_source != "pool" and available_pool:
            character = self._choose_pool_character(available_pool)

        if not character and available_pool:
            character = self._choose_pool_character(available_pool)

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
        starting_bid = POOL_STARTING_BIDS.get(str(character.get("rarity", "normal")).lower(), 1)
        if from_queue:
            seller_id = int(queue_entry.get("seller_id", 0) or 0)
            starting_bid = max(1, int(queue_entry.get("starting_bid", 1) or 1))

        state = {
            "character_id": int(character["id"]),
            "bid": starting_bid,
            "starting_bid": starting_bid,
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
        if not from_queue and str(character.get("rarity", "")).lower() == "legendary":
            embed = AuctionEmbeds.legendary_arrival(
                character,
                int(state["ends_at"]),
                starting_bid=starting_bid,
                image_url=image_url,
            )
        else:
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

        if not from_queue:
            await self.cog.notify_rarity_subscribers(channel, str(character.get("rarity", "normal")))

        state["message_id"] = message.id
        if not await self._write_current_auction(state):
            return False
        await self.config.forced_next_source.set(None)
        await self.config.forced_next_character_id.set(None)
        if from_queue:
            queue.pop(0)
            await self.config.queue.set(queue)
            await self.config.last_auction_source.set("queue")
        else:
            await self.config.last_auction_source.set("pool")
        if forced_source == "pool":
            await self.config.forced_next_source.set(None)
        await self.config.last_auction_started.set(utc_timestamp())
        return True

    def _choose_pool_character(self, character_ids: list[int]) -> dict[str, Any] | None:
        """Choose an unowned character using rarity-weighted odds."""
        characters = [self.cog.characters.get(character_id) for character_id in character_ids]
        characters = [character for character in characters if character]
        if not characters:
            return None

        weights = [RARITY_WEIGHTS.get(str(character.get("rarity", "normal")).lower(), 60) for character in characters]
        return random.choices(characters, weights=weights, k=1)[0]

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
        await self._write_current_auction({})

    async def cancel_current_auction(self) -> None:
        """Release the active bidder and remove the current auction state."""
        state = await self.get_current_auction()
        if state:
            await self._release_highest_bid(state)
        await self.clear_current_auction()

    async def _release_highest_bid(self, state: dict[str, Any]) -> None:
        """Release a bidder's held funds when an auction is discarded."""
        highest_bidder_id = state.get("highest_bidder_id")
        if highest_bidder_id:
            await self.cog.economy.release(int(highest_bidder_id))

    async def get_active_channel(self) -> discord.TextChannel | None:
        """Resolve the configured auction text channel."""
        channel_id = await self.config.auction_channel()
        if not channel_id:
            return None
        return await self.resolve_channel(int(channel_id))

    async def handle_bid(self, message: discord.Message) -> bool:
        """Handle a numeric bid sent in the configured auction channel."""
        async with self._state_lock:
            if not await self.is_active_runner():
                return False
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

        if await self.cog.debt_is_overdue(bidder_id):
            await message.reply("Your loan is overdue. Repay it before bidding again.")
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

        if not await self._write_current_auction(state):
            return False
        await self.update_current_embed(state)

        return True

    async def count_invalid_bid(self, state: dict[str, Any], user_id: int) -> None:
        """Track invalid bid attempts without locking the user out of the auction."""
        invalids = state.setdefault("invalid_counts", {})
        invalids[str(user_id)] = int(invalids.get(str(user_id), 0)) + 1
        await self._write_current_auction(state)

    async def update_current_embed(self, state: dict[str, Any]) -> None:
        """Repost the live embed so the active auction remains channel-bottom."""
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
            else AuctionEmbeds.auction_start(
                character,
                int(state["ends_at"]),
                starting_bid=int(state.get("starting_bid", bid)),
                image_url=image_url,
                seller=seller,
            )
        )

        try:
            replacement = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return

        previous_message_id = state.get("message_id")
        state["message_id"] = replacement.id
        if not await self._write_current_auction(state):
            return

        if not previous_message_id:
            return
        try:
            previous = await channel.fetch_message(int(previous_message_id))
            await previous.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def finish_auction(self) -> None:
        """End the current auction, transfer money, and deliver any settlement embeds."""
        async with self._state_lock:
            if not await self.is_active_runner():
                return
            await self._finish_auction()

    async def _finish_auction(self) -> None:
        """Settle the stored auction after all in-flight bids have completed."""
        if not await self.is_active_runner():
            return

        state = await self.get_current_auction()
        if not state:
            return

        now = utc_timestamp()
        has_bid = bool(state.get("highest_bidder_id"))
        no_bid_deadline = int(state.get("started_at", now)) + NO_BID_CLOSE_SECONDS
        settlement_deadline = int(state.get("ends_at", 0)) if has_bid else min(
            int(state.get("ends_at", no_bid_deadline)),
            no_bid_deadline,
        )
        if now < settlement_deadline:
            return

        character_id = int(state.get("character_id"))
        character = self.cog.characters.get(character_id)
        if not character:
            await self._release_highest_bid(state)
            await self.clear_current_auction()
            return

        channel_id = state.get("channel_id")
        message_id = state.get("message_id")
        channel = await self.resolve_channel(int(channel_id)) if channel_id else None

        winner_id = int(state.get("highest_bidder_id", 0) or 0)
        bid = int(state.get("bid", 1))
        seller_id = state.get("seller_id")

        try:
            if winner_id:
                winner = self.cog.bot.get_user(winner_id)
                if not winner:
                    try:
                        winner = await self.cog.bot.fetch_user(winner_id)
                    except (discord.NotFound, discord.HTTPException):
                        winner = None
                if not await self.is_active_runner():
                    return
                price = bid
                owner_before = self.cog.characters.owner_of(character_id)
                if owner_before is not None:
                    self.cog.characters.unassign(character_id)

                await self.cog.economy.finalize_purchase(winner_id, price)
                await self.cog.economy.add_character(winner_id, character_id)
                self.cog.characters.assign(character_id, winner_id)
                last_sale_prices = await self.config.last_sale_prices()
                previous_sale_price = int(last_sale_prices.get(str(character_id), 0) or 0)
                last_sale_prices[str(character_id)] = price
                await self.config.last_sale_prices.set(last_sale_prices)

                # Tax payout to original seller if this came from the queue.
                if seller_id:
                    seller_share = int(round(price * (1 - AUCTION_TAX)))
                    fee = price - seller_share
                    await self.cog.economy.deposit(seller_id, seller_share)
                    await self.cog.economy.remove_character(seller_id, character_id)
                    await self._record_fee(fee)
                    await self.cog.record_fee_paid(int(seller_id), fee)
                    vault_amount = fee
                else:
                    await self._record_fee(price)
                    vault_amount = price

                await self.cog.record_transaction(
                    "sale",
                    buyer_id=winner_id,
                    seller_id=int(seller_id or 0),
                    character_id=character_id,
                    price=price,
                    seller_share=seller_share if seller_id else 0,
                    vault_amount=vault_amount,
                    previous_sale_price=previous_sale_price,
                )

                winner_text = winner.mention if winner else f"<@{winner_id}>"
                seller_text = f"<@{seller_id}>" if seller_id else "🏛️ The Auction House"
                await self.cog.log_transaction(
                    "🔨 Auction Sale",
                    f"Character: **{character['name']}**\nBuyer: {winner_text}\n"
                    f"Seller: {seller_text}\nSale price: **{format_berries(price)}**",
                )

                embed = AuctionEmbeds.sold(character, winner_text, price, image_url=state.get("image_url"))
                sold_text = f"Sold to {winner_text} for {format_berries(price)}."
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
