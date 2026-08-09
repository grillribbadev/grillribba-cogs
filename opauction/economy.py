from __future__ import annotations

import asyncio
from typing import Optional

from redbot.core import Config

from .constants import DAILY_INCOME, STARTING_BALANCE
from .utils import utc_timestamp


_ACCOUNT_LOCK = asyncio.Lock()


class Economy:
    """Handles player economy."""

    def __init__(self, config: Config):
        self.config = config

    async def register_player(self, user_id: int) -> bool:
        """Register a player and repair partial account state without resetting progress."""
        player = self.config.user_from_id(user_id)

        async with _ACCOUNT_LOCK:
            if await player.started():
                return False

            data = await player.all()
            has_existing_data = bool(
                data.get("balance", 0)
                or data.get("reserved", 0)
                or data.get("characters", [])
                or data.get("joined", 0)
                or data.get("last_daily", 0)
                or data.get("cooldowns", {})
            )

            await player.started.set(True)
            if not has_existing_data:
                await player.balance.set(STARTING_BALANCE)
                await player.reserved.set(0)
                await player.characters.set([])
                await player.joined.set(utc_timestamp())
                await player.last_daily.set(0)
                await player.cooldowns.set({})
                return True

            # Retain existing balances and characters when repairing a partial
            # Config record left behind by an interrupted reset or old cog version.
            return False

    async def exists(self, user_id: int) -> bool:
        return await self.config.user_from_id(user_id).started()

    async def balance(self, user_id: int) -> int:
        return await self.config.user_from_id(user_id).balance()

    async def reserved(self, user_id: int) -> int:
        return await self.config.user_from_id(user_id).reserved()

    async def reconcile_reservation(self, user_id: int) -> int:
        """Keep a user's held funds equal to their active highest bid, if any."""
        state = await self.config.current_auction()
        highest_bidder_id = int(state.get("highest_bidder_id", 0) or 0) if state else 0
        expected = int(state.get("bid", 0) or 0) if highest_bidder_id == user_id else 0

        player = self.config.user_from_id(user_id)
        reserved = await player.reserved()
        if reserved != expected:
            await player.reserved.set(expected)
        return expected

    async def available(self, user_id: int) -> int:
        bal = await self.balance(user_id)
        res = await self.reconcile_reservation(user_id)
        return bal - res

    async def available_balance(self, user_id: int) -> int:
        """Alias for the API contract used by the auction manager."""
        return await self.available(user_id)

    async def deposit(self, user_id: int, amount: int):
        player = self.config.user_from_id(user_id)

        bal = await player.balance()
        await player.balance.set(bal + amount)

    async def adjust_balance(self, user_id: int, delta: int) -> int:
        """Apply a positive or negative balance change and return the amount actually applied.

        Negative deltas are clamped so reserved (currently-bid) beri is never touched.
        """
        player = self.config.user_from_id(user_id)

        bal = await player.balance()
        reserved = await player.reserved()

        if delta < 0:
            available = max(bal - reserved, 0)
            delta = -min(-delta, available)

        await player.balance.set(bal + delta)
        return delta

    async def withdraw(self, user_id: int, amount: int) -> bool:
        available = await self.available(user_id)

        if available < amount:
            return False

        player = self.config.user_from_id(user_id)

        bal = await player.balance()
        await player.balance.set(bal - amount)

        return True

    async def reserve(self, user_id: int, amount: int) -> bool:
        available = await self.available(user_id)

        if available < amount:
            return False

        player = self.config.user_from_id(user_id)

        await player.reserved.set(amount)

        return True

    async def release(self, user_id: int):
        await self.config.user_from_id(user_id).reserved.set(0)

    async def finalize_purchase(self, user_id: int, price: int):
        """Charge the recorded winning price and release the bid reservation."""
        player = self.config.user_from_id(user_id)

        bal = await player.balance()
        await player.balance.set(bal - price)
        await player.reserved.set(0)

    async def add_character(self, user_id: int, character_id: int):
        player = self.config.user_from_id(user_id)

        chars = await player.characters()

        if character_id not in chars:
            chars.append(character_id)

        await player.characters.set(chars)

    async def remove_character(self, user_id: int, character_id: int):
        player = self.config.user_from_id(user_id)

        chars = await player.characters()

        if character_id in chars:
            chars.remove(character_id)

        await player.characters.set(chars)

    async def get_characters(self, user_id: int):
        return await self.config.user_from_id(user_id).characters()

    async def claim_daily(self, user_id: int, amount: int) -> int:
        """Grant one daily payment or return the seconds remaining to claim."""
        player = self.config.user_from_id(user_id)

        last = await player.last_daily()
        now = utc_timestamp()
        remaining = 86400 - (now - last)
        if last and remaining > 0:
            return remaining

        bal = await player.balance()
        await player.balance.set(bal + amount)
        await player.last_daily.set(now)
        return 0