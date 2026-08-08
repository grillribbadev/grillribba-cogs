from __future__ import annotations

from typing import Optional

from redbot.core import Config

from .constants import DAILY_INCOME, STARTING_BALANCE
from .utils import utc_timestamp


class Economy:
    """Handles player economy."""

    def __init__(self, config: Config):
        self.config = config

    async def register_player(self, user_id: int) -> bool:
        """Register a player. Returns False if already registered."""
        player = self.config.user_from_id(user_id)

        if await player.started():
            return False

        now = utc_timestamp()

        await player.started.set(True)
        await player.balance.set(STARTING_BALANCE)
        await player.reserved.set(0)
        await player.characters.set([])
        await player.joined.set(now)
        await player.last_daily.set(0)

        return True

    async def exists(self, user_id: int) -> bool:
        return await self.config.user_from_id(user_id).started()

    async def balance(self, user_id: int) -> int:
        return await self.config.user_from_id(user_id).balance()

    async def reserved(self, user_id: int) -> int:
        return await self.config.user_from_id(user_id).reserved()

    async def available(self, user_id: int) -> int:
        bal = await self.balance(user_id)
        res = await self.reserved(user_id)
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

    async def finalize_purchase(self, user_id: int):
        player = self.config.user_from_id(user_id)

        bal = await player.balance()
        reserved = await player.reserved()

        await player.balance.set(bal - reserved)
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

    async def claim_daily(self, user_id: int) -> int:
        """Grant one daily payment or return the seconds remaining to claim."""
        player = self.config.user_from_id(user_id)

        last = await player.last_daily()
        now = utc_timestamp()
        remaining = 86400 - (now - last)
        if last and remaining > 0:
            return remaining

        bal = await player.balance()
        await player.balance.set(bal + DAILY_INCOME)
        await player.last_daily.set(now)
        return 0