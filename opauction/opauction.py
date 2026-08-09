from __future__ import annotations

import io
import itertools
import json
import logging
import random
import re
from pathlib import Path
from typing import Union

import discord

from redbot.core import Config, commands

from .auction import AuctionManager
from .characters import CharacterManager
from .constants import (
    AUCTION_TAX,
    DEFAULT_AUCTION_DURATION,
    DEFAULT_AUCTION_INTERVAL,
    LOAN_GRACE_PERIOD_SECONDS,
    OFFER_TIMEOUT_SECONDS,
    LOAN_INTEREST_RATE,
    MINIGAME_COOLDOWN_SECONDS,
    PRAY_MAX_PENALTY,
    PRAY_MAX_REWARD,
    PRAY_MIN_PENALTY,
    PRAY_MIN_REWARD,
    PRAY_SUCCESS_CHANCE,
    RARITIES,
    STARTING_BALANCE,
    STEAL_MAX_PENALTY,
    STEAL_MAX_REWARD,
    STEAL_MIN_PENALTY,
    STEAL_MIN_REWARD,
    STEAL_SUCCESS_CHANCE,
)
from .economy import Economy
from .leaderboard import BalanceLeaderboardView
from .utils import clean_name, format_berries, format_duration, utc_timestamp
from .views import AuctionEmbeds, AuctionPingView

log = logging.getLogger("red.opauction")


class OPAuction(commands.Cog):
    """One Piece Auction"""

    __author__ = "Grillribba"
    __version__ = "1.1.1"

    def __init__(self, bot):
        self.bot = bot

        self.config = Config.get_conf(
            self,
            identifier=948362871,
            force_registration=True,
            

        )

        default_global = {
            "auction_channel": None,
            "log_channel": None,
            "debt_log_channel": None,
            "tax_channel": None,
            "tax_rate": 0,
            "tax_running": False,
            "tax_last_collected": 0,
            "auction_running": False,
            "auction_duration": DEFAULT_AUCTION_DURATION,
            "auction_interval": DEFAULT_AUCTION_INTERVAL,
            "current_auction": {},
            "auction_runner_id": None,
            "character_roster": None,
            "pending_trades": {},
            "pending_loans": {},
            "loan_limit": 0,
            "queue": [],
            "last_auction_started": 0,
            "blocked_users": [],
            "total_fees": 0,
            "last_sale_prices": {},
            "transaction_history": [],
            "next_auction_source": "queue",
            "last_auction_source": "pool",
            "forced_next_source": None,
            "forced_next_character_id": None,
            "sellhouse_rate":70,
            "daily_income":1000,
        }

        default_user = {
            "started": False,
            "balance": 0,
            "reserved": 0,
            "characters": [],
            "joined": 0,
            "last_daily": 0,
            "cooldowns": {},
            "ping_rarities": [],
            "debt": 0,
            "debt_started_at": 0,
            "debt_recollection_notified": False,
            "taxes_paid": 0,
            "fees_paid": 0,
            "charitable_deductions": 0,
        }

        self.config.register_global(**default_global)
        self.config.register_user(**default_user)

        self.economy = Economy(self.config)
        self.characters = CharacterManager(self)
        self.auction = AuctionManager(self)
        self.auction_task = None

    async def cog_load(self):
        await self.auction.activate_runner()
        saved_roster = await self.config.character_roster()
        if saved_roster is None:
            await self.config.character_roster.set(self.characters.all())
        elif not self.characters.load_roster(saved_roster):
            log.warning("Saved OPAuction character roster was invalid; using the bundled roster.")
            await self.config.character_roster.set(self.characters.all())
        for user_id, data in (await self.config.all_users()).items():
            if int(data.get("debt", 0) or 0) and not int(data.get("debt_started_at", 0) or 0):
                await self.config.user_from_id(int(user_id)).debt_started_at.set(utc_timestamp())
        # self.owners is in-memory only; without this, every character looks
        # unowned after a restart until the destructive `wipe` command runs.
        await self.characters.rebuild_owners()
        await self.rebuild_reservations()
        self.bot.add_view(AuctionPingView(self))
        self.auction_task = self.bot.loop.create_task(self.auction.background_loop())

    def cog_unload(self):
        if self.auction_task:
            self.auction_task.cancel()

    async def red_delete_data_for_user(self, **kwargs):
        """Redbot data cleanup hook."""
        pass

    async def cog_command_error(self, ctx, error):
        """Clear stale framework cooldowns left by older cog versions."""
        original = getattr(error, "original", error)
        if (
            isinstance(original, commands.CommandOnCooldown)
            and ctx.command
            and ctx.command.name in {"pray", "steal"}
        ):
            ctx.command.reset_cooldown(ctx)
            return await ctx.reinvoke(restart=False)

        raise error

    async def is_blocked(self, user_id: int) -> bool:
        """Return True when a user is explicitly banned from live auction bidding."""
        blocked = await self.config.blocked_users()
        return int(user_id) in [int(item) for item in blocked]

    async def log_transaction(self, title: str, description: str) -> None:
        """Post a completed sale or house transaction to the configured log channel."""
        channel_id = await self.config.log_channel()
        if not channel_id:
            return

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning("Unable to fetch OPAuction log channel %s", channel_id)
                return
        if not isinstance(channel, discord.TextChannel):
            log.warning("Configured OPAuction log channel %s is not a text channel", channel_id)
            return

        embed = discord.Embed(title=title, description=description, color=discord.Color.gold())
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Unable to send OPAuction transaction log to channel %s", channel_id)

    async def log_overdue_debts(self, entries: list[tuple[int, int]]) -> bool:
        """Post the current overdue-debt report to the dedicated debt log channel."""
        channel_id = await self.config.debt_log_channel()
        if not channel_id:
            return False

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False
        if not isinstance(channel, discord.TextChannel):
            return False

        embed = discord.Embed(title="⚠️ Overdue Auction House Debt", color=discord.Color.red())
        if entries:
            embed.description = "\n".join(
                f"• <@{user_id}> owes **{format_berries(debt)}**" for user_id, debt in entries
            )
        else:
            embed.description = "No members currently have overdue Auction House debt."
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Unable to send OPAuction overdue debt report to channel %s", channel_id)
            return False
        return True

    async def log_recollection_due(self, entries: list[tuple[int, int]]) -> bool:
        """Post one-time notices for debts that have reached recollection eligibility."""
        channel_id = await self.config.debt_log_channel()
        if not channel_id:
            return False

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False
        if not isinstance(channel, discord.TextChannel):
            return False

        embed = discord.Embed(title="⚠️ Debt Recollection Due", color=discord.Color.red())
        embed.description = "\n".join(
            f"• <@{user_id}> owes **{format_berries(debt)}** and is eligible for collection."
            for user_id, debt in entries
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Unable to send OPAuction recollection notice to channel %s", channel_id)
            return False
        return True

    async def record_transaction(self, kind: str, **details) -> None:
        """Store a bounded history of completed economy changes."""
        history = await self.config.transaction_history()
        history.append({"kind": kind, "timestamp": utc_timestamp(), "reversed": False, **details})
        await self.config.transaction_history.set(history)

    async def record_tax_paid(self, user_id: int, amount: int) -> None:
        """Add a daily tax payment to a member's cumulative tax ledger."""
        if amount > 0:
            player = self.config.user_from_id(user_id)
            await player.taxes_paid.set(int(await player.taxes_paid() or 0) + amount)

    async def record_tax_json(
        self,
        user_id: int,
        amount: int,
        rate: float,
        deduction_used: int,
    ) -> None:
        """Append one daily tax payment to the JSON audit ledger."""
        path = Path(__file__).parent / "data" / "tax_records.json"
        try:
            records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"payments": [], "totals": {}}
            if not isinstance(records, dict):
                records = {"payments": [], "totals": {}}
            payments = records.setdefault("payments", [])
            totals = records.setdefault("totals", {})
            payments.append(
                {
                    "timestamp": utc_timestamp(),
                    "user_id": user_id,
                    "amount": amount,
                    "rate": rate,
                    "deduction_used": deduction_used,
                }
            )
            records["payments"] = payments
            user_key = str(user_id)
            totals[user_key] = int(totals.get(user_key, 0) or 0) + amount
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            log.exception("Unable to write OPAuction tax JSON ledger")

    async def record_fee_paid(self, user_id: int, amount: int) -> None:
        """Add an Auction House fee to a member's cumulative fee ledger."""
        if amount > 0:
            player = self.config.user_from_id(user_id)
            await player.fees_paid.set(int(await player.fees_paid() or 0) + amount)

    async def rebuild_tax_fee_ledgers(self) -> dict[str, int] | None:
        """Rebuild tax totals from log embeds and fee totals from transaction history."""
        users = await self.config.all_users()
        totals = {
            int(user_id): {"taxes": 0, "fees": 0}
            for user_id in users
        }
        tax_payments = []

        channel_id = await self.config.log_channel() or 1535757050682933388
        if not channel_id:
            return None
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if not isinstance(channel, discord.TextChannel):
            return None

        log_messages = 0
        fee_payments = 0
        fee_logs_skipped = 0
        try:
            async for message in channel.history(limit=None, oldest_first=True):
                log_messages += 1
                for embed in message.embeds:
                    if embed.title != "🏦 Daily Auction Tax" or not embed.description:
                        if embed.title == "🔨 Auction Sale" and embed.description:
                            seller_match = re.search(r"Seller:\s*<@!?(\d+)>", embed.description)
                            price_match = re.search(r"Sale price:\s*\*\*[^\d]*([\d,]+)\*\*", embed.description)
                            if not seller_match or not price_match:
                                fee_logs_skipped += 1
                                continue
                            seller_id = int(seller_match.group(1))
                            price = int(price_match.group(1).replace(",", ""))
                            fee = price - int(round(price * (1 - AUCTION_TAX)))
                            totals.setdefault(seller_id, {"taxes": 0, "fees": 0})["fees"] += fee
                            fee_payments += 1
                        elif embed.title == "🤝 Auction House Trade" and embed.description:
                            swap_fees = re.findall(
                                r"\*\*[^\d]*([\d,]+)\*\*\s*from\s*<@!?(\d+)>",
                                embed.description,
                            )
                            if swap_fees:
                                for amount_text, user_id_text in swap_fees:
                                    user_id = int(user_id_text)
                                    amount = int(amount_text.replace(",", ""))
                                    totals.setdefault(user_id, {"taxes": 0, "fees": 0})["fees"] += amount
                                    fee_payments += 1
                                continue
                            seller_match = re.search(r"from\s*<@!?(\d+)>\s*for", embed.description)
                            cut_match = re.search(r"Auction House cut:\s*\*\*[^\d]*([\d,]+)\*\*", embed.description)
                            if not seller_match or not cut_match:
                                fee_logs_skipped += 1
                                continue
                            seller_id = int(seller_match.group(1))
                            amount = int(cut_match.group(1).replace(",", ""))
                            totals.setdefault(seller_id, {"taxes": 0, "fees": 0})["fees"] += amount
                            fee_payments += 1
                        continue
                    member_match = re.search(r"Member:\s*<@!?(\d+)>", embed.description)
                    amount_match = re.search(r"Paid:\s*\*\*[^\d]*([\d,]+)\*\*", embed.description)
                    rate_match = re.search(r"Tax rate:\s*\*\*([\d.]+)%\*\*", embed.description)
                    deduction_match = re.search(
                        r"Charitable deduction used:\s*\*\*[^\d]*([\d,]+)\*\*",
                        embed.description,
                    )
                    if not member_match or not amount_match:
                        continue
                    user_id = int(member_match.group(1))
                    amount = int(amount_match.group(1).replace(",", ""))
                    totals.setdefault(user_id, {"taxes": 0, "fees": 0})["taxes"] += amount
                    tax_payments.append(
                        {
                            "timestamp": int(message.created_at.timestamp()),
                            "user_id": user_id,
                            "amount": amount,
                            "rate": float(rate_match.group(1)) if rate_match else 0,
                            "deduction_used": int(deduction_match.group(1).replace(",", "")) if deduction_match else 0,
                        }
                    )
        except (discord.Forbidden, discord.HTTPException):
            return None

        path = Path(__file__).parent / "data" / "tax_records.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "payments": tax_payments,
                    "totals": {str(user_id): amounts["taxes"] for user_id, amounts in totals.items()},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        for user_id, amounts in totals.items():
            player = self.config.user_from_id(user_id)
            await player.taxes_paid.set(amounts["taxes"])
            await player.fees_paid.set(amounts["fees"])

        return {
            "members": len(totals),
            "tax_payments": len(tax_payments),
            "fee_payments": fee_payments,
            "fee_logs_skipped": fee_logs_skipped,
            "log_messages": log_messages,
        }

    async def notify_rarity_subscribers(self, channel: discord.TextChannel, rarity: str) -> None:
        """Mention users who opted in to this pool-auction rarity."""
        rarity = rarity.lower()
        subscribers = [
            int(user_id)
            for user_id, data in (await self.config.all_users()).items()
            if rarity in data.get("ping_rarities", [])
        ]
        for start in range(0, len(subscribers), 50):
            mentions = " ".join(f"<@{user_id}>" for user_id in subscribers[start:start + 50])
            try:
                await channel.send(
                    f"{mentions}\nA **{rarity.title()}** character has appeared in the auction!",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except (discord.Forbidden, discord.HTTPException):
                return

    async def rebuild_reservations(self) -> None:
        """Clear stale holds and preserve only the current highest bid."""
        state = await self.auction.get_current_auction()
        highest_bidder_id = int(state.get("highest_bidder_id", 0) or 0) if state else 0
        highest_bid = int(state.get("bid", 0)) if state else 0

        for user_id in (await self.config.all_users()):
            reserved = highest_bid if int(user_id) == highest_bidder_id else 0
            await self.config.user_from_id(int(user_id)).reserved.set(reserved)

    async def start_cooldown(self, user_id: int, key: str, seconds: int) -> int:
        """Return 0 and start the named cooldown, or the seconds remaining if still active.

        Stored in Config (per user, keyed by command name) so it survives restarts
        and each minigame command tracks its own independent timer.
        """
        player = self.config.user_from_id(user_id)
        cooldowns = await player.cooldowns()
        last_used = int(cooldowns.get(key, 0))
        now = utc_timestamp()
        remaining = seconds - (now - last_used)
        if remaining > 0:
            return remaining

        cooldowns[key] = now
        await player.cooldowns.set(cooldowns)
        return 0

    async def cooldown_remaining(self, user_id: int, key: str, seconds: int) -> int:
        """Return remaining time for one named cooldown without changing it."""
        cooldowns = await self.config.user_from_id(user_id).cooldowns()
        last_used = int(cooldowns.get(key, 0))
        return max(0, seconds - (utc_timestamp() - last_used))

    async def debt_is_overdue(self, user_id: int) -> bool:
        """Return whether a user's unpaid loan has passed its 48-hour grace period."""
        player = self.config.user_from_id(user_id)
        debt = int(await player.debt() or 0)
        started_at = int(await player.debt_started_at() or 0)
        return debt > 0 and started_at > 0 and utc_timestamp() - started_at >= LOAN_GRACE_PERIOD_SECONDS

    async def debt_blocks_sales(self, user_id: int) -> bool:
        """Return whether overdue debt blocks a user from selling characters."""
        return await self.debt_is_overdue(user_id)

    async def notify_recollection_due(self) -> None:
        """Notify the debt log once when unpaid debt passes its grace period."""
        due_entries = []
        for user_id, data in (await self.config.all_users()).items():
            user_id = int(user_id)
            if (
                int(data.get("debt", 0) or 0) > 0
                and not data.get("debt_recollection_notified", False)
                and await self.debt_is_overdue(user_id)
            ):
                due_entries.append((user_id, int(data["debt"])))

        if not due_entries or not await self.log_recollection_due(due_entries):
            return
        for user_id, _ in due_entries:
            await self.config.user_from_id(user_id).debt_recollection_notified.set(True)

    async def collect_daily_taxes(self, *, force: bool = False) -> None:
        """Collect the configured percentage of each active player's available beri every 24 hours."""
        if not await self.config.tax_running():
            return

        now = utc_timestamp()
        last_collected = int(await self.config.tax_last_collected() or 0)
        if not force and now - last_collected < 24 * 60 * 60:
            return

        tax_rate = float(await self.config.tax_rate() or 0)
        if tax_rate <= 0:
            return

        collected = []
        async with self.auction._state_lock:
            # Recheck after waiting for the auction lock so only one runner can collect.
            last_collected = int(await self.config.tax_last_collected() or 0)
            if utc_timestamp() - last_collected < 24 * 60 * 60:
                return
            for user_id, data in (await self.config.all_users()).items():
                if not data.get("started"):
                    continue
                user_id = int(user_id)
                available = await self.economy.available_balance(user_id)
                deduction = int(data.get("charitable_deductions", 0) or 0)
                deduction_used = min(available, deduction)
                taxable_balance = available - deduction_used
                amount = int(taxable_balance * tax_rate / 100)
                if deduction_used:
                    await self.config.user_from_id(user_id).charitable_deductions.set(
                        deduction - deduction_used
                    )
                if amount < 1:
                    continue
                await self.economy.adjust_balance(user_id, -amount)
                await self.record_tax_paid(user_id, amount)
                await self.record_transaction(
                    "daily_tax_payment",
                    user_id=user_id,
                    amount=amount,
                    rate=tax_rate,
                    deduction_used=deduction_used,
                )
                await self.record_tax_json(user_id, amount, tax_rate, deduction_used)
                collected.append((user_id, amount, deduction_used))

            total_collected = sum(amount for _, amount, _ in collected)
            vault_balance = await self.config.total_fees()
            await self.config.total_fees.set(vault_balance + total_collected)
            await self.config.tax_last_collected.set(utc_timestamp())
            await self.record_transaction(
                "daily_tax",
                rate=tax_rate,
                amount=total_collected,
                payers=len(collected),
            )

        for user_id, amount, deduction_used in collected:
            await self.log_transaction(
                "🏦 Daily Auction Tax",
            f"Member: <@{user_id}>\nTax rate: **{tax_rate:g}%**\n"
            f"Charitable deduction used: **{format_berries(deduction_used)}**\n"
            f"Paid: **{format_berries(amount)}**",
            )

        channel_id = await self.config.tax_channel()
        if not channel_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    embed=AuctionEmbeds.success(
                        f"Daily taxes have been collected at **{tax_rate:g}%**. "
                        f"{len(collected)} member(s) paid a total of {format_berries(total_collected)}."
                    )
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Unable to announce OPAuction daily taxes in channel %s", channel_id)

    async def expire_pending_offers(self, timeout: int) -> None:
        """Remove unanswered trade and loan offers after their response window ends."""
        now = utc_timestamp()
        pending_loans = await self.config.pending_loans()
        expired_loans = [
            user_id for user_id, loan in pending_loans.items()
            if now - int(loan.get("created_at", now)) >= timeout
        ]
        for user_id in expired_loans:
            pending_loans.pop(user_id, None)
        if expired_loans:
            await self.config.pending_loans.set(pending_loans)

        pending_trades = await self.config.pending_trades()
        expired_trades = [
            user_id for user_id, trade in pending_trades.items()
            if now - int(trade.get("created_at", now)) >= timeout
        ]
        for user_id in expired_trades:
            pending_trades.pop(user_id, None)
        if expired_trades:
            await self.config.pending_trades.set(pending_trades)

    async def edit_offer_status(
        self,
        channel: discord.TextChannel,
        message_id: int,
        message: str,
        *,
        error: bool = False,
    ) -> None:
        """Replace a completed offer's embed and disable its reaction controls."""
        try:
            offer_message = await channel.fetch_message(message_id)
            await offer_message.edit(embed=AuctionEmbeds.error(message) if error else AuctionEmbeds.success(message))
            await offer_message.clear_reactions()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen only in the configured auction channel for bid messages."""
        if message.author.bot:
            return

        if await self.is_blocked(message.author.id):
            return

        if await self.config.auction_running() is False:
            return

        state = await self.auction.get_current_auction()
        if not state:
            return

        if message.channel.id != int(state.get("channel_id", 0)):
            return

        if message.content and message.content.strip().isdigit():
            bid_accepted = await self.auction.handle_bid(message)
            if bid_accepted:
                try:
                    await message.add_reaction("✅")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Ignore message edits for anti-troll safety."""
        return

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Ignore message deletions for anti-troll safety."""
        return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Accept or decline pending trade and loan offers from message reactions."""
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) not in {"✅", "❌"}:
            return

        pending_loans = await self.config.pending_loans()
        loan = pending_loans.get(str(payload.user_id))
        if loan and int(loan.get("message_id", 0) or 0) == payload.message_id:
            channel = self.bot.get_channel(payload.channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(payload.channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return
            if not isinstance(channel, discord.TextChannel):
                return

            if utc_timestamp() - int(loan.get("created_at", 0) or 0) >= OFFER_TIMEOUT_SECONDS:
                pending_loans.pop(str(payload.user_id), None)
                await self.config.pending_loans.set(pending_loans)
                await self.edit_offer_status(
                    channel,
                    payload.message_id,
                    f"<@{payload.user_id}>'s loan offer expired after one minute.",
                    error=True,
                )
                return

            if str(payload.emoji) == "❌":
                pending_loans.pop(str(payload.user_id), None)
                await self.config.pending_loans.set(pending_loans)
                await self.edit_offer_status(
                    channel,
                    payload.message_id,
                    f"<@{payload.user_id}> declined their Auction House loan offer.",
                )
                return

            await self._accept_loan(payload.user_id, loan, channel, payload.message_id)
            return

        pending_trades = await self.config.pending_trades()
        offerer_id = next(
            (
                int(user_id)
                for user_id, trade in pending_trades.items()
                if int(trade.get("message_id", 0) or 0) == payload.message_id
            ),
            None,
        )
        if offerer_id is None:
            return

        trade = pending_trades[str(offerer_id)]
        if int(trade.get("recipient_id", 0)) != payload.user_id:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if not isinstance(channel, discord.TextChannel):
            return

        if utc_timestamp() - int(trade.get("created_at", 0) or 0) >= OFFER_TIMEOUT_SECONDS:
            pending_trades.pop(str(offerer_id), None)
            await self.config.pending_trades.set(pending_trades)
            await self.edit_offer_status(
                channel,
                payload.message_id,
                f"<@{offerer_id}>'s trade offer expired after one minute.",
                error=True,
            )
            return

        if str(payload.emoji) == "❌":
            pending_trades.pop(str(offerer_id), None)
            await self.config.pending_trades.set(pending_trades)
            await self.edit_offer_status(
                channel,
                payload.message_id,
                f"<@{payload.user_id}> declined <@{offerer_id}>'s trade offer.",
            )
            return

        offerer = self.bot.get_user(offerer_id)
        recipient = self.bot.get_user(payload.user_id)
        if not offerer:
            try:
                offerer = await self.bot.fetch_user(offerer_id)
            except (discord.NotFound, discord.HTTPException):
                return
        if not recipient:
            try:
                recipient = await self.bot.fetch_user(payload.user_id)
            except (discord.NotFound, discord.HTTPException):
                return
        if not offerer or not recipient:
            return

        class ReactionContext:
            def __init__(self, author, response_channel):
                self.author = author
                self._response_channel = response_channel

            async def send(self, *args, **kwargs):
                return await self._response_channel.send(*args, **kwargs)

        await self._accept_trade(ReactionContext(recipient, channel), offerer, payload.message_id)

    @commands.group(name="auction", aliases=["ac"], invoke_without_command=True)
    async def auction_group(self, ctx):
        """Auction commands."""
        await ctx.send_help()

    @auction_group.command(name="start")
    async def start_game(self, ctx):
        """Register as a player."""

        created = await self.economy.register_player(ctx.author.id)
        if not created:
            return await ctx.send(embed=AuctionEmbeds.success("Your Auction account is ready."))

        await ctx.send(embed=AuctionEmbeds.success("Welcome to the auction!\nYou received ฿1000."))

    @auction_group.command(name="balance", aliases=["wallet", "beri", "bal"])
    async def balance(self, ctx, member: Union[discord.Member, str] = None):
        """View your balance, or an admin can view another member's balance."""
        if isinstance(member, str):
            if member.lower() != "top":
                return await ctx.send(embed=AuctionEmbeds.error("Use a member mention or `.auction balance top`."))

            users = await self.config.all_users()
            entries = sorted(
                (
                    (int(user_id), int(data.get("balance", 0) or 0))
                    for user_id, data in users.items()
                    if data.get("started")
                ),
                key=lambda entry: entry[1],
                reverse=True,
            )
            view = BalanceLeaderboardView(entries, owner_id=ctx.author.id)
            return await ctx.send(
                embed=AuctionEmbeds.leaderboard(entries, page=0),
                view=view,
            )

        target = member or ctx.author
        if member and member.id != ctx.author.id:
            permissions = ctx.author.guild_permissions if ctx.guild else None
            if not permissions or not (permissions.administrator or permissions.manage_guild):
                return await ctx.send(embed=AuctionEmbeds.error("Only administrators can view another member's balance."))

        if not await self.economy.exists(target.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        balance = await self.economy.balance(target.id)
        reserved = await self.economy.reconcile_reservation(target.id)
        debt = await self.config.user_from_id(target.id).debt()
        await ctx.send(embed=AuctionEmbeds.balance(target, balance, reserved, debt))

    @auction_group.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    async def add_beri(self, ctx, amount: int, member: discord.Member):
        """Add beri to a registered player's auction balance."""
        if amount < 1:
            return await ctx.send(embed=AuctionEmbeds.error("Amount must be at least ฿1."))
        if not await self.economy.exists(member.id):
            return await ctx.send(embed=AuctionEmbeds.error("That member has not started the auction game."))

        await self.economy.deposit(member.id, amount)
        await self.record_transaction("admin_add", user_id=member.id, amount=amount)
        await ctx.send(embed=AuctionEmbeds.success(f"Added {format_berries(amount)} to {member.mention}."))

    @auction_group.command(name="subtract", aliases=["sub"])
    @commands.admin_or_permissions(manage_guild=True)
    async def subtract_beri(self, ctx, amount: int, member: discord.Member):
        """Subtract spendable beri from a registered player's auction balance."""
        if amount < 1:
            return await ctx.send(embed=AuctionEmbeds.error("Amount must be at least ฿1."))
        if not await self.economy.exists(member.id):
            return await ctx.send(embed=AuctionEmbeds.error("That member has not started the auction game."))

        available = await self.economy.available_balance(member.id)
        if available < amount:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    f"{member.mention} has only {format_berries(available)} available to subtract."
                )
            )

        await self.economy.adjust_balance(member.id, -amount)
        await self.record_transaction("admin_subtract", user_id=member.id, amount=amount)
        await ctx.send(embed=AuctionEmbeds.success(f"Subtracted {format_berries(amount)} from {member.mention}."))

    @auction_group.command(name="daily")
    async def daily(self, ctx):
        """Claim the daily beri payment every 24 hours."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        daily_income = int(await self.config.daily_income() or 1000)
        remaining = await self.economy.claim_daily(ctx.author.id, daily_income)
        if remaining > 0:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    f"Your daily beri is available again in {format_duration(remaining)}."
                )
            )

        await self.record_transaction("daily", user_id=ctx.author.id, amount=daily_income)
        await ctx.send(embed=AuctionEmbeds.success(f"You claimed your daily {format_berries(daily_income)}."))

    @auction_group.command(name="ping")
    async def ping(self, ctx):
        """Choose rarity pings for upcoming pool auctions."""
        selected = await self.config.user_from_id(ctx.author.id).ping_rarities()
        await ctx.send(
            embed=AuctionEmbeds.ping_preferences(selected),
            view=AuctionPingView(self, selected),
        )

    @auction_group.command(name="collection", aliases=["col", "inv", "inventory"])
    async def collection(self, ctx, member: discord.Member = None):
        """List your collection, or an administrator can view another member's."""
        target = member or ctx.author
        if member and member.id != ctx.author.id:
            permissions = ctx.author.guild_permissions if ctx.guild else None
            if not permissions or not (permissions.administrator or permissions.manage_guild):
                return await ctx.send(embed=AuctionEmbeds.error("Only administrators can view another member's collection."))

        if not await self.economy.exists(target.id):
            return await ctx.send("Use `.auction start` first.")

        queue = await self.config.queue()
        queued_ids = {
            int(entry.get("character_id", 0))
            for entry in queue
            if int(entry.get("seller_id", 0) or 0) == target.id
        }
        current = await self.auction.get_current_auction()
        live_character_id = (
            int(current.get("character_id", 0))
            if current and int(current.get("seller_id", 0) or 0) == target.id
            else 0
        )
        last_sale_prices = await self.config.last_sale_prices()

        characters = []
        for character_id in await self.economy.get_characters(target.id):
            character = self.characters.get(character_id)
            if character:
                status = "Up for auction" if int(character_id) == live_character_id else "Queued for sale" if int(character_id) in queued_ids else "Owned"
                last_bought_value = int(last_sale_prices.get(str(character_id), 0) or 0)
                characters.append((character, status, last_bought_value))

        await ctx.send(embed=AuctionEmbeds.collection(target, characters))

    @auction_group.command(name="view")
    async def view_character(self, ctx, *, name: str):
        """Show a character's artwork, tier, and current auction status."""
        character = self.characters.get_by_name(clean_name(name))
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

        character_id = int(character["id"])
        current = await self.auction.get_current_auction()
        queue = await self.config.queue()
        owner_id = self.characters.owner_of(character_id)

        if current and int(current.get("character_id", 0)) == character_id:
            status = "Live auction"
        elif any(int(entry.get("character_id", 0)) == character_id for entry in queue):
            status = "Queued for auction"
        elif owner_id:
            status = f"Owned by <@{owner_id}>"
        else:
            status = "Available in the Auction House pool"

        last_sale_prices = await self.config.last_sale_prices()
        last_sale_price = int(last_sale_prices.get(str(character_id), 0) or 0)
        image_url = await self.auction.get_image_url(character)
        await ctx.send(
            embed=AuctionEmbeds.character_view(
                character,
                status,
                last_sale_price,
                image_url=image_url,
            )
        )

    @auction_group.command(name="owner", aliases=["whoowns"])
    async def character_owner(self, ctx, *, name: str):
        """Show the member who currently owns a character."""
        character = self.characters.get_by_name(clean_name(name))
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

        owner_id = self.characters.owner_of(int(character["id"]))
        if owner_id:
            return await ctx.send(
                embed=AuctionEmbeds.success(f"**{character['name']}** is currently owned by <@{owner_id}>.")
            )

        await ctx.send(
            embed=AuctionEmbeds.success(
                f"**{character['name']}** is currently unowned and belongs to the Auction House pool."
            )
        )

    @auction_group.group(name="char", invoke_without_command=True)
    async def character_group(self, ctx):
        """Character information commands."""
        await ctx.send_help(ctx.command)

    @character_group.command(name="info")
    async def character_info(self, ctx, *, name: str):
        """Show a character's rarity, sale history, owner, and artwork."""
        character = self.characters.get_by_name(clean_name(name))
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find one unambiguous character matching that name."))

        character_id = int(character["id"])
        owner_id = self.characters.owner_of(character_id)
        owner_text = f"<@{owner_id}>" if owner_id else "Auction House pool"
        last_sale_prices = await self.config.last_sale_prices()
        last_sale_price = int(last_sale_prices.get(str(character_id), 0) or 0)
        image_url = await self.auction.get_image_url(character)
        await ctx.send(
            embed=AuctionEmbeds.character_info(
                character,
                owner_text,
                last_sale_price,
                image_url=image_url,
            )
        )

    @auction_group.command(name="trade")
    async def trade(self, ctx, member: discord.Member, *, offer: str):
        """Offer a character swap or sell one character to another member for beri."""
        if member.id == ctx.author.id:
            return await ctx.send(embed=AuctionEmbeds.error("You cannot trade with yourself."))
        if not await self.economy.exists(ctx.author.id) or not await self.economy.exists(member.id):
            return await ctx.send(embed=AuctionEmbeds.error("Both members must use `.auction start` before trading."))
        if int(await self.config.user_from_id(ctx.author.id).debt() or 0) or int(await self.config.user_from_id(member.id).debt() or 0):
            return await ctx.send(embed=AuctionEmbeds.error("Members with outstanding loan debt cannot trade characters."))

        parts = [part.strip() for part in offer.split("|")]
        if len(parts) != 2:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    "Use `.auction trade @member Your Character | Their Character` for a swap, or "
                    "`.auction trade @member Your Character | cash amount` to sell for beri."
                )
            )

        requested_character = None
        requested_id = None
        cash_amount = 0
        offerer_fee = 0
        recipient_fee = 0
        trade_type = "swap"
        if parts[0].isdigit() and parts[1].isdigit():
            return await ctx.send(embed=AuctionEmbeds.error("A trade must include at least one character."))

        if parts[1].isdigit():
            offered_character = self.characters.get_by_name(clean_name(parts[0]))
            if not offered_character:
                return await ctx.send(embed=AuctionEmbeds.error("I could not find the character you are offering."))
            offered_id = int(offered_character["id"])
            if self.characters.owner_of(offered_id) != ctx.author.id:
                return await ctx.send(embed=AuctionEmbeds.error("You do not own the character you are offering."))
            cash_amount = int(parts[1])
            if cash_amount < 1:
                return await ctx.send(embed=AuctionEmbeds.error("The cash amount must be at least ฿1."))
            if await self.economy.available_balance(member.id) < cash_amount:
                return await ctx.send(embed=AuctionEmbeds.error(f"{member.mention} does not have enough available beri."))
            trade_type = "sale"
        elif parts[0].isdigit():
            requested_character = self.characters.get_by_name(clean_name(parts[1]))
            if not requested_character:
                return await ctx.send(embed=AuctionEmbeds.error("I could not find the character you want to buy."))
            offered_character = requested_character
            offered_id = int(offered_character["id"])
            if self.characters.owner_of(offered_id) != member.id:
                return await ctx.send(embed=AuctionEmbeds.error(f"{member.mention} does not own that character."))
            cash_amount = int(parts[0])
            if cash_amount < 1:
                return await ctx.send(embed=AuctionEmbeds.error("The cash amount must be at least ฿1."))
            if await self.economy.available_balance(ctx.author.id) < cash_amount:
                return await ctx.send(embed=AuctionEmbeds.error("You do not have enough available beri for that offer."))
            trade_type = "purchase"
        else:
            first_character = self.characters.get_by_name(clean_name(parts[0]))
            second_character = self.characters.get_by_name(clean_name(parts[1]))
            if not first_character or not second_character:
                return await ctx.send(embed=AuctionEmbeds.error("I could not find one of the requested characters."))

            if self.characters.owner_of(int(first_character["id"])) == ctx.author.id and self.characters.owner_of(int(second_character["id"])) == member.id:
                offered_character, requested_character = first_character, second_character
            elif self.characters.owner_of(int(first_character["id"])) == member.id and self.characters.owner_of(int(second_character["id"])) == ctx.author.id:
                offered_character, requested_character = second_character, first_character
            else:
                return await ctx.send(embed=AuctionEmbeds.error("Each member must own one of the characters in the swap."))

            offered_id = int(offered_character["id"])
            requested_id = int(requested_character["id"])
            if offered_id == requested_id:
                return await ctx.send(embed=AuctionEmbeds.error("A trade must contain two different characters."))

            last_sale_prices = await self.config.last_sale_prices()
            offered_value = int(last_sale_prices.get(str(offered_id), 0) or 0)
            requested_value = int(last_sale_prices.get(str(requested_id), 0) or 0)
            total_trade_value = offered_value + requested_value
            split_fee = round(total_trade_value * 0.05)
            offerer_fee = split_fee
            recipient_fee = split_fee
            if await self.economy.available_balance(ctx.author.id) < offerer_fee:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"You need {format_berries(offerer_fee)} available for the Auction House trade fee."
                    )
                )
            if await self.economy.available_balance(member.id) < recipient_fee:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"{member.mention} needs {format_berries(recipient_fee)} available for the Auction House trade fee."
                    )
                )

        queue = await self.config.queue()
        current = await self.auction.get_current_auction()
        busy_ids = {int(entry.get("character_id", 0)) for entry in queue}
        if current:
            busy_ids.add(int(current.get("character_id", 0)))
        if offered_id in busy_ids or (requested_id is not None and requested_id in busy_ids):
            return await ctx.send(embed=AuctionEmbeds.error("Characters in the queue or live auction cannot be traded."))

        pending_trades = await self.config.pending_trades()
        trade = {
            "recipient_id": member.id,
            "offered_character_id": offered_id,
            "requested_character_id": requested_id,
            "cash_amount": cash_amount,
            "offerer_fee": offerer_fee,
            "recipient_fee": recipient_fee,
            "trade_type": trade_type,
            "created_at": utc_timestamp(),
        }

        if trade_type == "swap":
            detail = (
                f"**{offered_character['name']}** for **{requested_character['name']}**. "
                f"The Auction House takes 10% of both characters' combined last sale value, split evenly: "
                f"{format_berries(offerer_fee)} from you and {format_berries(recipient_fee)} from {member.mention}"
            )
        elif trade_type == "sale":
            house_cut = round(cash_amount * 0.40)
            seller_amount = cash_amount - house_cut
            detail = (
                f"**{offered_character['name']}** for {format_berries(cash_amount)}. The Auction House takes "
                f"40% ({format_berries(house_cut)}); you receive {format_berries(seller_amount)}"
            )
        else:
            house_cut = round(cash_amount * 0.40)
            seller_amount = cash_amount - house_cut
            detail = (
                f"{format_berries(cash_amount)} for **{offered_character['name']}**. The Auction House takes "
                f"40% ({format_berries(house_cut)}); {member.mention} receives {format_berries(seller_amount)}"
            )
        offer_message = await ctx.send(
            embed=AuctionEmbeds.success(
                f"Trade offer sent to {member.mention}: {detail}.\n"
                "React with ✅ to accept or ❌ to decline."
            )
        )
        trade["message_id"] = offer_message.id
        pending_trades[str(ctx.author.id)] = trade
        await self.config.pending_trades.set(pending_trades)
        try:
            await offer_message.add_reaction("✅")
            await offer_message.add_reaction("❌")
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _accept_trade(self, ctx, member: discord.abc.User, offer_message_id: int):
        """Accept a pending trade offer from another member."""
        pending_trades = await self.config.pending_trades()
        trade = pending_trades.get(str(member.id))
        if not trade or int(trade.get("recipient_id", 0)) != ctx.author.id:
            return await ctx.send(embed=AuctionEmbeds.error("That member has no pending trade offer for you."))

        offered_id = int(trade["offered_character_id"])
        requested_id = trade.get("requested_character_id")
        requested_id = int(requested_id) if requested_id is not None else None
        cash_amount = int(trade["cash_amount"])
        offerer_fee = int(trade.get("offerer_fee", 0) or 0)
        recipient_fee = int(trade.get("recipient_fee", 0) or 0)
        trade_type = trade.get("trade_type", "swap")
        async with self.auction._state_lock:
            pending_trades = await self.config.pending_trades()
            if str(member.id) not in pending_trades:
                return await ctx.send(embed=AuctionEmbeds.error("That trade offer has already been handled."))
            character_seller_id = ctx.author.id if trade_type == "purchase" else member.id
            character_buyer_id = member.id if trade_type == "purchase" else ctx.author.id
            cash_buyer_id = member.id if trade_type == "purchase" else ctx.author.id
            cash_seller_id = ctx.author.id if trade_type == "purchase" else member.id
            if self.characters.owner_of(offered_id) != character_seller_id:
                return await ctx.send(embed=AuctionEmbeds.error("One of the offered characters is no longer owned by the trading member."))
            if trade_type == "swap" and self.characters.owner_of(requested_id) != ctx.author.id:
                return await ctx.send(embed=AuctionEmbeds.error("One of the offered characters is no longer owned by the trading member."))
            if trade_type == "swap" and await self.economy.available_balance(member.id) < offerer_fee:
                return await ctx.send(embed=AuctionEmbeds.error("The offering member no longer has enough beri for the trade fee."))
            if trade_type == "swap" and await self.economy.available_balance(ctx.author.id) < recipient_fee:
                return await ctx.send(embed=AuctionEmbeds.error("You no longer have enough beri for the trade fee."))
            if trade_type in {"sale", "purchase"} and await self.economy.available_balance(cash_buyer_id) < cash_amount:
                return await ctx.send(embed=AuctionEmbeds.error("The buyer no longer has enough available beri to buy this character."))

            queue = await self.config.queue()
            current = await self.auction.get_current_auction()
            busy_ids = {int(entry.get("character_id", 0)) for entry in queue}
            if current:
                busy_ids.add(int(current.get("character_id", 0)))
            if offered_id in busy_ids or (requested_id is not None and requested_id in busy_ids):
                return await ctx.send(embed=AuctionEmbeds.error("Characters in the queue or live auction cannot be traded."))

            if trade_type == "swap":
                await self.economy.remove_character(member.id, offered_id)
                await self.economy.add_character(ctx.author.id, offered_id)
                self.characters.assign(offered_id, ctx.author.id)
                await self.economy.remove_character(ctx.author.id, requested_id)
                await self.economy.add_character(member.id, requested_id)
                self.characters.assign(requested_id, member.id)
                house_cut = offerer_fee + recipient_fee
                seller_amount = 0
                await self.economy.adjust_balance(member.id, -offerer_fee)
                await self.economy.adjust_balance(ctx.author.id, -recipient_fee)
                await self.record_fee_paid(member.id, offerer_fee)
                await self.record_fee_paid(ctx.author.id, recipient_fee)
                vault_balance = await self.config.total_fees()
                await self.config.total_fees.set(vault_balance + house_cut)
            else:
                house_cut = round(cash_amount * 0.40)
                seller_amount = cash_amount - house_cut
                await self.economy.remove_character(character_seller_id, offered_id)
                await self.economy.add_character(character_buyer_id, offered_id)
                self.characters.assign(offered_id, character_buyer_id)
                await self.economy.adjust_balance(cash_buyer_id, -cash_amount)
                await self.economy.deposit(cash_seller_id, seller_amount)
                await self.record_fee_paid(cash_seller_id, house_cut)
                vault_balance = await self.config.total_fees()
                await self.config.total_fees.set(vault_balance + house_cut)

            pending_trades.pop(str(member.id), None)
            await self.config.pending_trades.set(pending_trades)
            await self.record_transaction(
                "trade",
                offerer_id=member.id,
                recipient_id=ctx.author.id,
                offered_character_id=offered_id,
                requested_character_id=requested_id,
                cash_amount=cash_amount,
                vault_amount=house_cut,
                trade_type=trade_type,
                fee_payments=(
                    [
                        {"user_id": member.id, "amount": offerer_fee},
                        {"user_id": ctx.author.id, "amount": recipient_fee},
                    ]
                    if trade_type == "swap"
                    else [{"user_id": cash_seller_id, "amount": house_cut}]
                ),
            )

        offered_character = self.characters.get(offered_id)
        if trade_type == "swap":
            requested_character = self.characters.get(requested_id)
            detail = (
                f"{member.mention} traded **{offered_character['name']}** for **{requested_character['name']}** "
                f"from {ctx.author.mention}.\nAuction House trade fees: **{format_berries(offerer_fee)}** "
                f"from {member.mention} and **{format_berries(recipient_fee)}** from {ctx.author.mention}."
            )
            result = (
                f"Trade complete. {ctx.author.mention} received **{offered_character['name']}** and "
                f"{member.mention} received **{requested_character['name']}**. The Auction House collected "
                f"{format_berries(house_cut)} in trade fees."
            )
        else:
            buyer_text = member.mention if trade_type == "purchase" else ctx.author.mention
            seller_text = ctx.author.mention if trade_type == "purchase" else member.mention
            detail = f"{buyer_text} bought **{offered_character['name']}** from {seller_text} for {format_berries(cash_amount)}.\nAuction House cut: **{format_berries(house_cut)}**"
            result = f"Trade complete. {buyer_text} received **{offered_character['name']}**; {seller_text} received {format_berries(seller_amount)}. The Auction House collected {format_berries(house_cut)}."
        await self.log_transaction("🤝 Auction House Trade", detail)
        await self.edit_offer_status(ctx._response_channel, offer_message_id, result)

    @auction_group.command(name="sell")
    async def sell(self, ctx, *, name: str):
        """Queue a character with an optional trailing starting bid."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send("Use `.auction start` first.")
        if await self.debt_blocks_sales(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Your loan is overdue. Repay it before selling characters."))

        parts = name.rsplit(maxsplit=1)
        starting_bid = 1
        if len(parts) == 2 and parts[1].isdigit():
            name, starting_bid_text = parts
            starting_bid = int(starting_bid_text)

        if starting_bid < 1:
            return await ctx.send(embed=AuctionEmbeds.error("Starting bid must be at least ฿1."))

        normalized_name = clean_name(name)
        character = self.characters.get_by_name(normalized_name)
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

        if not self.characters.owner_of(int(character["id"])) == ctx.author.id:
            return await ctx.send(embed=AuctionEmbeds.error("You do not own that character."))

        queue = await self.config.queue()
        if any(item.get("character_id") == int(character["id"]) for item in queue):
            return await ctx.send(embed=AuctionEmbeds.error("That character is already queued."))

        queue.append(
            {
                "character_id": int(character["id"]),
                "seller_id": ctx.author.id,
                "starting_bid": starting_bid,
            }
        )
        await self.config.queue.set(queue)

        await ctx.send(
            embed=AuctionEmbeds.success(
                f"{character['name']} has been added to the auction queue with a starting bid of {format_berries(starting_bid)}."
            )
        )

    @auction_group.command(name="unsell", aliases=["withdrawsale"])
    async def unsell(self, ctx, *, name: str):
        """Withdraw one of your queued characters before its auction begins."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        character = self.characters.get_by_name(clean_name(name))
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

        character_id = int(character["id"])
        current = await self.auction.get_current_auction()
        if current and int(current.get("character_id", 0)) == character_id:
            return await ctx.send(embed=AuctionEmbeds.error("You cannot withdraw a character during its live auction."))

        queue = await self.config.queue()
        entry_index = next(
            (
                index
                for index, entry in enumerate(queue)
                if int(entry.get("character_id", 0)) == character_id
                and int(entry.get("seller_id", 0) or 0) == ctx.author.id
            ),
            None,
        )
        if entry_index is None:
            return await ctx.send(embed=AuctionEmbeds.error("That character is not in your auction queue."))

        queue.pop(entry_index)
        await self.config.queue.set(queue)
        await ctx.send(embed=AuctionEmbeds.success(f"Removed **{character['name']}** from your auction queue."))

    @auction_group.command(name="sellhouse", aliases=["housesell"])
    async def sell_house(self, ctx, *, name: str):
        """Sell a character, all characters, or a rarity tier to the Auction House."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))
        if await self.debt_blocks_sales(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Your loan is overdue. Repay it before selling characters."))

        selector, _, exclusion_text = name.partition("-")
        selector = clean_name(selector)
        rarity_selectors = {rarity.lower() for rarity in RARITIES}
        if selector == "all" or selector in rarity_selectors:
            excluded_names = {
                clean_name(part)
                for part in exclusion_text.split("-")
                if clean_name(part)
            }
            return await self._sell_house_bulk(ctx, selector, excluded_names)

        character = self.characters.get_by_name(clean_name(name))
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

        character_id = int(character["id"])
        if self.characters.owner_of(character_id) != ctx.author.id:
            return await ctx.send(embed=AuctionEmbeds.error("You do not own that character."))

        queue = await self.config.queue()
        if any(int(entry.get("character_id", 0)) == character_id for entry in queue):
            return await ctx.send(embed=AuctionEmbeds.error("Remove this character from the queue before selling it to the auction house."))

        current = await self.auction.get_current_auction()
        if current and int(current.get("character_id", 0)) == character_id:
            return await ctx.send(embed=AuctionEmbeds.error("This character is currently being auctioned."))

        last_sale_prices = await self.config.last_sale_prices()
        last_price = int(last_sale_prices.get(str(character_id), 0) or 0)
        if last_price < 1:
            return await ctx.send(embed=AuctionEmbeds.error("This character has no completed auction sale price yet."))

        rate = int(await self.config.sell_house_rate() or 70)
        payout = max(1, last_price * rate // 100)
        vault_balance = await self.config.total_fees()
        if vault_balance < payout:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    f"The Auction House Vault has only {format_berries(vault_balance)} available for this buyback."
                )
            )

        await self.economy.deposit(ctx.author.id, payout)
        await self.economy.remove_character(ctx.author.id, character_id)
        self.characters.unassign(character_id)
        await self.config.total_fees.set(vault_balance - payout)
        await self.record_transaction(
            "buyback",
            user_id=ctx.author.id,
            character_id=character_id,
            amount=payout,
            vault_amount=payout,
        )
        await self.log_transaction(
            "🏦 Auction House Buyback",
            f"Character: **{character['name']}**\nSeller: {ctx.author.mention}\n"
            f"Last sale: {format_berries(last_price)}\nBuyback payout: **{format_berries(payout)}**\n"
            f"Returned to the Auction House pool.",
        )
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"The Auction House bought **{character['name']}** for {format_berries(payout)}."
            )
        )

    async def _sell_house_bulk(self, ctx, selector: str, excluded_names: set[str]) -> None:
        """Sell all eligible owned characters matching a bulk selector in one atomic buyback."""
        queue = await self.config.queue()
        queued_ids = {int(entry.get("character_id", 0)) for entry in queue}
        current = await self.auction.get_current_auction()
        live_id = int(current.get("character_id", 0)) if current else 0
        last_sale_prices = await self.config.last_sale_prices()
        selected = []
        skipped = 0

        for character_id in await self.economy.get_characters(ctx.author.id):
            character_id = int(character_id)
            character = self.characters.get(character_id)
            if not character:
                continue
            character_name = clean_name(character["name"])
            if character_name in excluded_names:
                continue
            if selector != "all" and clean_name(character.get("rarity", "")) != selector:
                continue
            if character_id in queued_ids or character_id == live_id:
                skipped += 1
                continue
            last_price = int(last_sale_prices.get(str(character_id), 0) or 0)
            if last_price < 1:
                skipped += 1
                continue
            selected.append((character_id, character, last_price, max(1, last_price * 70 // 100)))

        if not selected:
            return await ctx.send(embed=AuctionEmbeds.error("No eligible characters matched that sell-to-house selection."))

        total_payout = sum(payout for _, _, _, payout in selected)
        async with self.auction._state_lock:
            vault_balance = await self.config.total_fees()
            if vault_balance < total_payout:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"The Auction House Vault has only {format_berries(vault_balance)} available, but this buyback costs {format_berries(total_payout)}."
                    )
                )
            for character_id, _, _, _ in selected:
                await self.economy.remove_character(ctx.author.id, character_id)
                self.characters.unassign(character_id)
            await self.economy.deposit(ctx.author.id, total_payout)
            await self.config.total_fees.set(vault_balance - total_payout)
            await self.record_transaction(
                "bulk_buyback",
                user_id=ctx.author.id,
                character_ids=[character_id for character_id, _, _, _ in selected],
                amount=total_payout,
                vault_amount=total_payout,
            )

        names = ", ".join(character["name"] for _, character, _, _ in selected)
        await self.log_transaction(
            "🏦 Auction House Bulk Buyback",
            f"Seller: {ctx.author.mention}\nCharacters: **{names}**\n"
            f"Buyback payout: **{format_berries(total_payout)}**\nReturned to the Auction House pool.",
        )
        skipped_text = f" Skipped {skipped} queued, live, or unpriced character(s)." if skipped else ""
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"The Auction House bought {len(selected)} character(s) for {format_berries(total_payout)}.{skipped_text}"
            )
        )

    @auction_group.command(name="queue")
    async def queue_list(self, ctx):
        """Show the characters currently queued for auction."""
        queue = await self.config.queue()

        entries = []
        for item in queue:
            character = self.characters.get(int(item.get("character_id", 0)))
            if not character:
                continue

            seller_id = int(item.get("seller_id", 0) or 0)
            seller = (ctx.guild.get_member(seller_id) if ctx.guild else None) or self.bot.get_user(seller_id)
            seller_text = seller.mention if seller else f"<@{seller_id}>"
            entries.append((character, seller_text))

        await ctx.send(embed=AuctionEmbeds.queue(entries))

    @auction_group.command(name="clearqueue")
    @commands.admin_or_permissions(manage_guild=True)
    async def clear_queue(self, ctx):
        """Remove every pending listing from the auction queue."""
        async with self.auction._state_lock:
            queue = await self.config.queue()
            if not queue:
                return await ctx.send(embed=AuctionEmbeds.error("The auction queue is already empty."))
            await self.config.queue.set([])

        await ctx.send(embed=AuctionEmbeds.success(f"Cleared {len(queue)} queued auction listing(s)."))

    @auction_group.command(name="bank")
    async def bank(self, ctx):
        """Show how much beri the auction house has collected in fees."""
        total = await self.config.total_fees()
        await ctx.send(embed=AuctionEmbeds.bank(total))

    @auction_group.command(name="bankdeposit", aliases=["depositbank"])
    @commands.admin_or_permissions(manage_guild=True)
    async def bank_deposit(self, ctx, amount: int):
        """Move an administrator's available beri into the Auction House Vault."""
        if amount < 1:
            return await ctx.send(embed=AuctionEmbeds.error("Amount must be at least ฿1."))
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        available = await self.economy.available_balance(ctx.author.id)
        if available < amount:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    f"You have only {format_berries(available)} available to deposit."
                )
            )

        await self.economy.adjust_balance(ctx.author.id, -amount)
        vault_balance = await self.config.total_fees()
        await self.config.total_fees.set(vault_balance + amount)
        await self.record_transaction("bank_deposit", user_id=ctx.author.id, amount=amount)
        await self.log_transaction(
            "🏦 Vault Deposit",
            f"Administrator: {ctx.author.mention}\nAmount: **{format_berries(amount)}**\n"
            f"Vault balance: **{format_berries(vault_balance + amount)}**",
        )
        await ctx.send(embed=AuctionEmbeds.success(f"Deposited {format_berries(amount)} into the Auction House Vault."))

    @auction_group.command(name="bankwithdraw", aliases=["withdrawbank"])
    @commands.admin_or_permissions(manage_guild=True)
    async def bank_withdraw(self, ctx, amount: int, member: discord.Member):
        """Pay a registered member from the Auction House Vault."""
        if amount < 1:
            return await ctx.send(embed=AuctionEmbeds.error("Amount must be at least ฿1."))
        if not await self.economy.exists(member.id):
            return await ctx.send(embed=AuctionEmbeds.error("That member has not started the auction game."))

        vault_balance = await self.config.total_fees()
        if vault_balance < amount:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    f"The Auction House Vault has only {format_berries(vault_balance)} available."
                )
            )

        await self.config.total_fees.set(vault_balance - amount)
        await self.economy.deposit(member.id, amount)
        await self.record_transaction("bank_withdraw", user_id=member.id, amount=amount)
        await self.log_transaction(
            "🏦 Vault Withdrawal",
            f"Administrator: {ctx.author.mention}\nRecipient: {member.mention}\n"
            f"Amount: **{format_berries(amount)}**\n"
            f"Vault balance: **{format_berries(vault_balance - amount)}**",
        )
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Withdrew {format_berries(amount)} from the Auction House Vault to {member.mention}."
            )
        )

    @auction_group.command(name="loan")
    async def loan(self, ctx, amount: int):
        """Borrow beri from the Auction House Vault at 25 percent interest."""
        if amount < 1:
            return await ctx.send(embed=AuctionEmbeds.error("Loan amount must be at least ฿1."))
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        async with self.auction._state_lock:
            loan_limit = int(await self.config.loan_limit() or 0)
            if loan_limit and amount > loan_limit:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"The maximum loan principal is {format_berries(loan_limit)}."
                    )
                )
            player = self.config.user_from_id(ctx.author.id)
            existing_debt = int(await player.debt() or 0)
            if existing_debt:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"You already owe {format_berries(existing_debt)}. Repay it before taking another loan."
                    )
                )
            vault_balance = await self.config.total_fees()
            if vault_balance < amount:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"The Auction House Vault can lend only {format_berries(vault_balance)} right now."
                    )
                )
            debt = round(amount * (1 + LOAN_INTEREST_RATE))
            pending_loans = await self.config.pending_loans()
            if str(ctx.author.id) in pending_loans:
                return await ctx.send(embed=AuctionEmbeds.error("You already have a loan offer waiting for a response."))

        offer_message = await ctx.send(
            embed=AuctionEmbeds.success(
                f"Loan offer: receive **{format_berries(amount)}** now and repay **{format_berries(debt)}** "
                f"at 25% interest.\nReact with ✅ to accept or ❌ to decline."
            )
        )
        pending_loans = await self.config.pending_loans()
        pending_loans[str(ctx.author.id)] = {
            "amount": amount,
            "debt": debt,
            "message_id": offer_message.id,
            "created_at": utc_timestamp(),
        }
        await self.config.pending_loans.set(pending_loans)
        try:
            await offer_message.add_reaction("✅")
            await offer_message.add_reaction("❌")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @auction_group.command(name="loanlimit", aliases=["setloanlimit"])
    @commands.admin_or_permissions(manage_guild=True)
    async def loan_limit(self, ctx, amount: int = None):
        """View or set the maximum loan principal. Set to 0 for no cap."""
        if amount is None:
            current_limit = int(await self.config.loan_limit() or 0)
            limit_text = format_berries(current_limit) if current_limit else "No limit"
            return await ctx.send(embed=AuctionEmbeds.success(f"Current maximum loan principal: {limit_text}."))
        if amount < 0:
            return await ctx.send(embed=AuctionEmbeds.error("The loan limit cannot be negative."))

        await self.config.loan_limit.set(amount)
        limit_text = format_berries(amount) if amount else "No limit"
        await ctx.send(embed=AuctionEmbeds.success(f"Maximum loan principal set to {limit_text}."))

    @auction_group.command(name="loaninfo", aliases=["debtinfo"])
    async def loan_info(self, ctx):
        """Explain Auction House loans, debt, and overdue collection."""
        embed = discord.Embed(title="🏦 Auction House Loans", color=discord.Color.gold())
        embed.add_field(
            name="Taking a Loan",
            value=(
                "Use `.auction loan <amount>`, then react with ✅ to accept or ❌ to decline. "
                "The Vault must have enough beri to fund the loan."
            ),
            inline=False,
        )
        embed.add_field(
            name="Interest and Repayment",
            value=(
                "Loans charge **25% interest**. For example, borrowing ฿1,000 creates a debt of ฿1,250. "
                "Use `.auction repayloan [amount]` for a full or partial repayment."
            ),
            inline=False,
        )
        embed.add_field(
            name="While You Owe Debt",
            value="You cannot take another loan or participate in character trades.",
            inline=False,
        )
        embed.add_field(
            name="After 48 Hours",
            value=(
                "If debt remains unpaid after the 48-hour grace period, you also cannot bid, queue characters for sale, "
                "or sell characters to the Auction House."
            ),
            inline=False,
        )
        embed.add_field(
            name="If You Cannot Pay",
            value=(
                "Administrators can collect your available beri, repossess an eligible character at its last sale value, "
                "or forgive debt in exceptional cases. Queued and live-auction characters cannot be repossessed."
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    async def _accept_loan(
        self,
        user_id: int,
        loan: dict,
        channel: discord.TextChannel,
        offer_message_id: int,
    ) -> None:
        """Issue a reaction-confirmed loan after rechecking all mutable state."""
        amount = int(loan["amount"])
        debt = int(loan["debt"])
        async with self.auction._state_lock:
            pending_loans = await self.config.pending_loans()
            current_loan = pending_loans.get(str(user_id))
            if not current_loan or int(current_loan.get("message_id", 0) or 0) != int(loan.get("message_id", 0) or 0):
                return

            player = self.config.user_from_id(user_id)
            if int(await player.debt() or 0):
                pending_loans.pop(str(user_id), None)
                await self.config.pending_loans.set(pending_loans)
                return await self.edit_offer_status(
                    channel,
                    offer_message_id,
                    f"<@{user_id}> already has outstanding loan debt; this offer was cancelled.",
                    error=True,
                )
            vault_balance = await self.config.total_fees()
            if vault_balance < amount:
                pending_loans.pop(str(user_id), None)
                await self.config.pending_loans.set(pending_loans)
                return await self.edit_offer_status(
                    channel,
                    offer_message_id,
                    f"<@{user_id}>'s loan offer expired because the vault has only {format_berries(vault_balance)} available.",
                    error=True,
                )

            await self.config.total_fees.set(vault_balance - amount)
            await self.economy.deposit(user_id, amount)
            await player.debt.set(debt)
            await player.debt_started_at.set(utc_timestamp())
            await player.debt_recollection_notified.set(False)
            await self.record_transaction("loan", user_id=user_id, amount=amount, debt=debt)
            pending_loans.pop(str(user_id), None)
            await self.config.pending_loans.set(pending_loans)

        await self.log_transaction(
            "🏦 Auction House Loan",
            f"Borrower: <@{user_id}>\nPrincipal: **{format_berries(amount)}**\n"
            f"Debt due: **{format_berries(debt)}**\nInterest: **25%**",
        )
        await self.edit_offer_status(
            channel,
            offer_message_id,
            f"<@{user_id}> accepted the loan and received {format_berries(amount)}. "
            f"Total debt: {format_berries(debt)}.",
        )

    @auction_group.command(name="repayloan", aliases=["repay"])
    async def repay_loan(self, ctx, amount: int = None):
        """Repay all or part of your Auction House loan debt."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        async with self.auction._state_lock:
            player = self.config.user_from_id(ctx.author.id)
            debt = int(await player.debt() or 0)
            if debt < 1:
                return await ctx.send(embed=AuctionEmbeds.error("You do not have an outstanding Auction House loan."))

            repayment = debt if amount is None else amount
            if repayment < 1:
                return await ctx.send(embed=AuctionEmbeds.error("Repayment amount must be at least ฿1."))
            repayment = min(repayment, debt)
            available = await self.economy.available_balance(ctx.author.id)
            if available < repayment:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"You have only {format_berries(available)} available to repay your loan."
                    )
                )

            await self.economy.adjust_balance(ctx.author.id, -repayment)
            await player.debt.set(debt - repayment)
            if debt - repayment == 0:
                await player.debt_started_at.set(0)
            vault_balance = await self.config.total_fees()
            await self.config.total_fees.set(vault_balance + repayment)
            await self.record_transaction(
                "loan_repayment",
                user_id=ctx.author.id,
                amount=repayment,
                remaining_debt=debt - repayment,
            )

        await self.log_transaction(
            "🏦 Loan Repayment",
            f"Borrower: {ctx.author.mention}\nRepaid: **{format_berries(repayment)}**\n"
            f"Remaining debt: **{format_berries(debt - repayment)}**",
        )
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"You repaid {format_berries(repayment)}. Remaining loan debt: {format_berries(debt - repayment)}."
            )
        )

    @auction_group.command(name="debttimer", aliases=["collectiontimer"])
    async def debt_timer(self, ctx, member: discord.Member = None):
        """Show how long remains before a member's debt is eligible for recollection."""
        member = member or ctx.author
        can_manage_guild = bool(getattr(ctx.author.guild_permissions, "manage_guild", False))
        if member.id != ctx.author.id and not can_manage_guild:
            return await ctx.send(embed=AuctionEmbeds.error("Only administrators can view another member's debt timer."))

        player = self.config.user_from_id(member.id)
        debt = int(await player.debt() or 0)
        if debt < 1:
            return await ctx.send(embed=AuctionEmbeds.error("That member has no outstanding Auction House debt."))

        started_at = int(await player.debt_started_at() or 0)
        remaining = max(0, LOAN_GRACE_PERIOD_SECONDS - (utc_timestamp() - started_at)) if started_at else 0
        status = "Eligible for recollection now." if remaining == 0 else f"Eligible for recollection in **{format_duration(remaining)}**."
        embed = discord.Embed(
            title=f"Debt Timer: {member.display_name}",
            description=f"Outstanding debt: **{format_berries(debt)}**\n{status}",
            color=discord.Color.red() if remaining == 0 else discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    @auction_group.command(name="paydebt", aliases=["repayfor"])
    async def pay_debt(self, ctx, member: discord.Member, amount: Union[int, str] = None):
        """Pay all or part of another member's loan debt with no additional fees."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        async with self.auction._state_lock:
            borrower = self.config.user_from_id(member.id)
            debt = int(await borrower.debt() or 0)
            if debt < 1:
                return await ctx.send(embed=AuctionEmbeds.error("That member has no outstanding Auction House debt."))

            if isinstance(amount, str) and amount.lower() not in {"remaining", "remianing"}:
                return await ctx.send(embed=AuctionEmbeds.error("Use a beri amount or `remaining`."))
            payment = debt if amount is None or isinstance(amount, str) else amount
            if payment < 1:
                return await ctx.send(embed=AuctionEmbeds.error("Payment amount must be at least ฿1."))
            payment = min(payment, debt)
            available = await self.economy.available_balance(ctx.author.id)
            if available < payment:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"You have only {format_berries(available)} available to pay toward their debt."
                    )
                )

            remaining_debt = debt - payment
            await self.economy.adjust_balance(ctx.author.id, -payment)
            await borrower.debt.set(remaining_debt)
            if remaining_debt == 0:
                await borrower.debt_started_at.set(0)
            vault_balance = await self.config.total_fees()
            await self.config.total_fees.set(vault_balance + payment)
            await self.record_transaction(
                "third_party_debt_payment",
                payer_id=ctx.author.id,
                user_id=member.id,
                amount=payment,
                remaining_debt=remaining_debt,
            )

        await self.log_transaction(
            "🏦 Debt Paid By Another Member",
            f"Payer: {ctx.author.mention}\nBorrower: {member.mention}\n"
            f"Payment: **{format_berries(payment)}**\nRemaining debt: **{format_berries(remaining_debt)}**",
        )
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Paid {format_berries(payment)} toward {member.mention}'s debt. "
                f"Remaining debt: {format_berries(remaining_debt)}."
            )
        )

    @auction_group.command(name="donate", aliases=["donatebank", "bankdonate"])
    async def donate_to_bank(self, ctx, amount: Union[int, str]):
        """Donate available beri to the Auction House Vault with no fee."""
        if isinstance(amount, str) and amount.lower() not in {"remaining", "remianing"}:
            return await ctx.send(embed=AuctionEmbeds.error("Use a beri amount or `remaining`."))
        if isinstance(amount, int) and amount < 1:
            return await ctx.send(embed=AuctionEmbeds.error("Donation amount must be at least ฿1."))
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        async with self.auction._state_lock:
            available = await self.economy.available_balance(ctx.author.id)
            donation = available if isinstance(amount, str) else amount
            if donation < 1:
                return await ctx.send(embed=AuctionEmbeds.error("You have no available beri to donate."))
            if available < donation:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"You have only {format_berries(available)} available to donate."
                    )
                )

            await self.economy.adjust_balance(ctx.author.id, -donation)
            vault_balance = await self.config.total_fees()
            await self.config.total_fees.set(vault_balance + donation)
            player = self.config.user_from_id(ctx.author.id)
            await player.charitable_deductions.set(
                int(await player.charitable_deductions() or 0) + donation
            )
            await self.record_transaction("vault_donation", user_id=ctx.author.id, amount=donation)

        await self.log_transaction(
            "🏦 Vault Donation",
            f"Donor: {ctx.author.mention}\nAmount: **{format_berries(donation)}**",
        )
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Donated {format_berries(donation)} to the Auction House Vault. "
                "The donation has been added to your charitable tax deductions."
            )
        )

    @auction_group.command(name="collectdebt")
    @commands.admin_or_permissions(manage_guild=True)
    async def collect_debt(self, ctx, member: discord.Member):
        """Collect as much of a member's available beri as possible toward their debt."""
        async with self.auction._state_lock:
            player = self.config.user_from_id(member.id)
            debt = int(await player.debt() or 0)
            if debt < 1:
                return await ctx.send(embed=AuctionEmbeds.error("That member has no outstanding debt."))

            collected = min(debt, await self.economy.available_balance(member.id))
            if collected < 1:
                return await ctx.send(embed=AuctionEmbeds.error("That member has no available beri to collect."))

            await self.economy.adjust_balance(member.id, -collected)
            remaining_debt = debt - collected
            await player.debt.set(remaining_debt)
            if remaining_debt == 0:
                await player.debt_started_at.set(0)
            vault_balance = await self.config.total_fees()
            await self.config.total_fees.set(vault_balance + collected)
            await self.record_transaction(
                "debt_collection",
                user_id=member.id,
                amount=collected,
                remaining_debt=remaining_debt,
            )

        await self.log_transaction(
            "🏦 Debt Collection",
            f"Member: {member.mention}\nCollected: **{format_berries(collected)}**\n"
            f"Remaining debt: **{format_berries(remaining_debt)}**",
        )
        await ctx.send(embed=AuctionEmbeds.success(f"Collected {format_berries(collected)} from {member.mention}. Remaining debt: {format_berries(remaining_debt)}."))

    @auction_group.command(name="debt")
    @commands.admin_or_permissions(manage_guild=True)
    async def debt_details(self, ctx, member: discord.Member):
        """Show an indebted member's characters and their last completed sale values."""
        player = self.config.user_from_id(member.id)
        debt = int(await player.debt() or 0)
        if debt < 1:
            return await ctx.send(embed=AuctionEmbeds.error("That member has no outstanding debt."))

        owned_ids = [int(character_id) for character_id in await player.characters()]
        last_sale_prices = await self.config.last_sale_prices()
        queue = await self.config.queue()
        current = await self.auction.get_current_auction()
        unavailable_ids = {int(entry.get("character_id", 0)) for entry in queue}
        if current:
            unavailable_ids.add(int(current.get("character_id", 0)))

        lines = []
        for character_id in owned_ids:
            character = self.characters.get(character_id)
            if not character:
                continue
            value = int(last_sale_prices.get(str(character_id), 0) or 0)
            status = " - unavailable (queued/live)" if character_id in unavailable_ids else ""
            lines.append(
                f"**{character['name']}** ({character.get('rarity', 'Unknown')}) - "
                f"{format_berries(value) if value else 'No completed sale value'}{status}"
            )

        embed = discord.Embed(
            title=f"Debt Repossession Review: {member.display_name}",
            description=f"Outstanding debt: **{format_berries(debt)}**",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Owned Characters",
            value="\n".join(lines) if lines else "No characters owned.",
            inline=False,
        )
        await ctx.send(embed=embed)

    @auction_group.command(name="repossess")
    @commands.admin_or_permissions(manage_guild=True)
    async def repossess_character(self, ctx, member: discord.Member, *, name: str):
        """Repossess one named character or enough eligible characters to recover a debt amount."""
        target = name.strip()
        if target.lower() == "all":
            debt = int(await self.config.user_from_id(member.id).debt() or 0)
            return await self._repossess_value(ctx, member, debt, take_all=True)
        if target.isdigit():
            return await self._repossess_value(ctx, member, int(target))

        character = self.characters.get_by_name(clean_name(name))
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

        character_id = int(character["id"])
        async with self.auction._state_lock:
            player = self.config.user_from_id(member.id)
            debt = int(await player.debt() or 0)
            if debt < 1:
                return await ctx.send(embed=AuctionEmbeds.error("That member has no outstanding debt."))
            if self.characters.owner_of(character_id) != member.id:
                return await ctx.send(embed=AuctionEmbeds.error("That member does not own this character."))

            queue = await self.config.queue()
            current = await self.auction.get_current_auction()
            if any(int(entry.get("character_id", 0)) == character_id for entry in queue) or (
                current and int(current.get("character_id", 0)) == character_id
            ):
                return await ctx.send(embed=AuctionEmbeds.error("Queued or live-auction characters cannot be repossessed."))

            last_sale_prices = await self.config.last_sale_prices()
            value = int(last_sale_prices.get(str(character_id), 0) or 0)
            if value < 1:
                return await ctx.send(embed=AuctionEmbeds.error("This character has no completed sale value and cannot be repossessed automatically."))

            recovered = min(value, debt)
            remaining_debt = debt - recovered
            await self.economy.remove_character(member.id, character_id)
            self.characters.unassign(character_id)
            await player.debt.set(remaining_debt)
            if remaining_debt == 0:
                await player.debt_started_at.set(0)
            vault_balance = await self.config.total_fees()
            await self.config.total_fees.set(vault_balance + recovered)
            await self.record_transaction(
                "repossession",
                user_id=member.id,
                character_id=character_id,
                amount=recovered,
                remaining_debt=remaining_debt,
            )

        await self.log_transaction(
            "🏦 Character Repossession",
            f"Member: {member.mention}\nCharacter: **{character['name']}**\n"
            f"Recovered: **{format_berries(recovered)}**\nRemaining debt: **{format_berries(remaining_debt)}**",
        )
        await ctx.send(embed=AuctionEmbeds.success(f"Repossessed **{character['name']}** from {member.mention}, recovering {format_berries(recovered)}. Remaining debt: {format_berries(remaining_debt)}."))

    async def _repossess_value(
        self,
        ctx,
        member: discord.Member,
        amount: int,
        *,
        take_all: bool = False,
    ) -> None:
        """Recover an exact debt amount using eligible character values, returning any overage."""
        if amount < 1:
            return await ctx.send(embed=AuctionEmbeds.error("The repossession amount must be at least ฿1."))

        async with self.auction._state_lock:
            player = self.config.user_from_id(member.id)
            debt = int(await player.debt() or 0)
            if debt < 1:
                return await ctx.send(embed=AuctionEmbeds.error("That member has no outstanding debt."))
            if amount > debt:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        f"The requested recovery exceeds the member's debt of {format_berries(debt)}."
                    )
                )

            queue = await self.config.queue()
            current = await self.auction.get_current_auction()
            unavailable_ids = {int(entry.get("character_id", 0)) for entry in queue}
            if current:
                unavailable_ids.add(int(current.get("character_id", 0)))
            last_sale_prices = await self.config.last_sale_prices()
            eligible = []
            for character_id in await player.characters():
                character_id = int(character_id)
                value = int(last_sale_prices.get(str(character_id), 0) or 0)
                if (
                    value > 0
                    and character_id not in unavailable_ids
                    and self.characters.owner_of(character_id) == member.id
                ):
                    eligible.append((character_id, value))

            if not eligible:
                return await ctx.send(
                    embed=AuctionEmbeds.error("That member has no eligible valued characters to repossess.")
                )
            if not take_all and sum(value for _, value in eligible) < amount:
                return await ctx.send(
                    embed=AuctionEmbeds.error(
                        "That member does not have enough eligible character value to recover that amount."
                    )
                )

            selected = eligible if take_all else None
            if not take_all and len(eligible) <= 20:
                best_score = None
                for count in range(1, len(eligible) + 1):
                    for choice in itertools.combinations(eligible, count):
                        total_value = sum(value for _, value in choice)
                        if total_value < amount:
                            continue
                        score = (total_value, count)
                        if best_score is None or score < best_score:
                            selected = list(choice)
                            best_score = score
                    if best_score and best_score[0] == amount:
                        break
            elif not take_all:
                remaining = amount
                candidates = eligible.copy()
                selected = []
                while remaining > 0:
                    below_remaining = [entry for entry in candidates if entry[1] <= remaining]
                    choice = max(below_remaining, key=lambda entry: entry[1]) if below_remaining else min(candidates, key=lambda entry: entry[1])
                    selected.append(choice)
                    candidates.remove(choice)
                    remaining -= choice[1]

            total_value = sum(value for _, value in selected)
            recovered = min(total_value, amount)
            surplus = total_value - recovered
            for character_id, _ in selected:
                await self.economy.remove_character(member.id, character_id)
                self.characters.unassign(character_id)

            remaining_debt = debt - recovered
            await player.debt.set(remaining_debt)
            if remaining_debt == 0:
                await player.debt_started_at.set(0)
            vault_balance = await self.config.total_fees()
            await self.config.total_fees.set(vault_balance + recovered)
            if surplus:
                await self.economy.deposit(member.id, surplus)
            await self.record_transaction(
                "bulk_repossession",
                user_id=member.id,
                character_ids=[character_id for character_id, _ in selected],
                amount=recovered,
                surplus=surplus,
                remaining_debt=remaining_debt,
            )

        character_names = ", ".join(
            self.characters.get(character_id)["name"] for character_id, _ in selected
        )
        await self.log_transaction(
            "🏦 Value Repossession",
            f"Member: {member.mention}\nCharacters: **{character_names}**\n"
            f"Recovered: **{format_berries(recovered)}**\nSurplus returned: **{format_berries(surplus)}**\n"
            f"Remaining debt: **{format_berries(remaining_debt)}**",
        )
        surplus_text = f" Returned {format_berries(surplus)} surplus to {member.mention}." if surplus else ""
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Repossessed {len(selected)} character(s) from {member.mention}, recovering {format_berries(recovered)}. "
                f"Remaining debt: {format_berries(remaining_debt)}.{surplus_text}"
            )
        )

    @auction_group.command(name="forgivedebt")
    @commands.admin_or_permissions(manage_guild=True)
    async def forgive_debt(self, ctx, member: discord.Member):
        """Clear a member's remaining Auction House loan debt."""
        player = self.config.user_from_id(member.id)
        debt = int(await player.debt() or 0)
        if debt < 1:
            return await ctx.send(embed=AuctionEmbeds.error("That member has no outstanding debt."))

        await player.debt.set(0)
        await player.debt_started_at.set(0)
        await self.record_transaction("debt_forgiveness", user_id=member.id, amount=debt)
        await self.log_transaction("🏦 Debt Forgiven", f"Member: {member.mention}\nForgiven debt: **{format_berries(debt)}**")
        await ctx.send(embed=AuctionEmbeds.success(f"Forgave {format_berries(debt)} of debt for {member.mention}."))

    @auction_group.command(name="pray")
    async def pray(self, ctx):
        """Pray for berries. Usually pays off, but it can backfire."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        remaining = await self.cooldown_remaining(ctx.author.id, "pray", MINIGAME_COOLDOWN_SECONDS)
        if remaining > 0:
            return await ctx.send(embed=AuctionEmbeds.error(f"You must wait {format_duration(remaining)} before praying again."))

        try:
            if random.random() < PRAY_SUCCESS_CHANCE:
                amount = random.randint(PRAY_MIN_REWARD, PRAY_MAX_REWARD)
                await self.economy.adjust_balance(ctx.author.id, amount)
                await ctx.send(embed=AuctionEmbeds.success(f"🙏 Your prayer was answered! You received {format_berries(amount)}."))
            else:
                amount = random.randint(PRAY_MIN_PENALTY, PRAY_MAX_PENALTY)
                taken = -await self.economy.adjust_balance(ctx.author.id, -amount)
                await ctx.send(embed=AuctionEmbeds.error(f"⚡ Your prayer angered the heavens! You lost {format_berries(taken)}."))
            await self.start_cooldown(ctx.author.id, "pray", MINIGAME_COOLDOWN_SECONDS)
        except Exception:
            log.exception("pray command failed for user %s", ctx.author.id)
            await ctx.send(embed=AuctionEmbeds.error("Something went wrong with your prayer. Please try again."))

    @auction_group.command(name="steal")
    async def steal(self, ctx):
        """Attempt to steal some berries. Usually pays off, but it can backfire."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        remaining = await self.cooldown_remaining(ctx.author.id, "steal", MINIGAME_COOLDOWN_SECONDS)
        if remaining > 0:
            return await ctx.send(embed=AuctionEmbeds.error(f"You must wait {format_duration(remaining)} before stealing again."))

        try:
            if random.random() < STEAL_SUCCESS_CHANCE:
                amount = random.randint(STEAL_MIN_REWARD, STEAL_MAX_REWARD)
                await self.economy.adjust_balance(ctx.author.id, amount)
                await ctx.send(embed=AuctionEmbeds.success(f"🗡️ The heist paid off! You made off with {format_berries(amount)}."))
            else:
                amount = random.randint(STEAL_MIN_PENALTY, STEAL_MAX_PENALTY)
                taken = -await self.economy.adjust_balance(ctx.author.id, -amount)
                await ctx.send(embed=AuctionEmbeds.error(f"🚨 You got caught! You paid a {format_berries(taken)} fine."))
            await self.start_cooldown(ctx.author.id, "steal", MINIGAME_COOLDOWN_SECONDS)
        except Exception:
            log.exception("steal command failed for user %s", ctx.author.id)
            await ctx.send(embed=AuctionEmbeds.error("Something went wrong with your heist. Please try again."))

    @auction_group.command(name="resetcooldowns", aliases=["resetcd"])
    @commands.admin_or_permissions(manage_guild=True)
    async def reset_cooldowns(self, ctx, member: discord.Member = None):
        """Reset everyone's (or one member's) pray/steal cooldowns."""
        if member is not None:
            await self.config.user_from_id(member.id).cooldowns.set({})
            return await ctx.send(
                embed=AuctionEmbeds.success(f"Cleared pray/steal cooldowns for {member.mention}.")
            )

        players = await self.config.all_users()
        cleared = 0
        for user_id, data in players.items():
            if not data.get("started"):
                continue

            await self.config.user_from_id(int(user_id)).cooldowns.set({})
            cleared += 1

        await ctx.send(
            embed=AuctionEmbeds.success(f"Cleared pray/steal cooldowns for {cleared} player(s).")
        )

    @auction_group.command(name="resetdaily")
    @commands.admin_or_permissions(manage_guild=True)
    async def reset_daily(self, ctx, member: discord.Member = None):
        """Reset the daily claim timer for everyone or one member."""
        if member is not None:
            await self.config.user_from_id(member.id).last_daily.set(0)
            return await ctx.send(
                embed=AuctionEmbeds.success(f"Reset the daily claim timer for {member.mention}.")
            )

        players = await self.config.all_users()
        reset_count = 0
        for user_id, data in players.items():
            if not data.get("started"):
                continue
            await self.config.user_from_id(int(user_id)).last_daily.set(0)
            reset_count += 1

        await ctx.send(
            embed=AuctionEmbeds.success(f"Reset the daily claim timer for {reset_count} player(s).")
        )

    @auction_group.command(name="info")
    async def info(self, ctx):
        """Show the current OPAuction status summary."""
        status = await self.auction.status()
        desc = (
            f"Version: {self.__version__}\n"
            f"Registered players are not automatic.\n"
            f"Starting balance: {format_berries(STARTING_BALANCE)}\n"
            f"Daily income: {format_berries(1000)} every 24 hours\n"
            f"Auction channel: {'configured' if status['channel'] else 'not configured'}\n"
            f"Auction interval: {status['interval']} seconds\n"
            f"Auction duration: {status['duration']} seconds"
        )
        await ctx.send(embed=AuctionEmbeds.success(desc))

    @auction_group.command(name="begin")
    @commands.admin_or_permissions(manage_guild=True)
    async def begin_auction(self, ctx):
        """Start the automatic auction loop and post the first live auction."""
        if not await self.config.auction_channel():
            return await ctx.send(embed=AuctionEmbeds.error("No auction channel has been configured yet. Use `auction channel #...` first."))

        started = await self.auction.begin()
        if not started:
            return await ctx.send(embed=AuctionEmbeds.error("Auction automation is running, but no auction embed could be posted to the configured channel."))

        current = await self.auction.get_current_auction()
        if current and current.get("message_id"):
            await ctx.send(embed=AuctionEmbeds.success("Auction automation has started and the first auction is live."))
        else:
            await ctx.send(embed=AuctionEmbeds.error("Auction automation is supposed to be live, but the live auction message is not materialized."))

    @auction_group.command(name="stop")
    @commands.admin_or_permissions(manage_guild=True)
    async def stop_auction(self, ctx):
        """Stop the automatic auction loop."""
        await self.auction.stop()
        await ctx.send(embed=AuctionEmbeds.success("Auction automation has stopped."))

    @auction_group.command(name="force")
    @commands.admin_or_permissions(manage_guild=True)
    async def force_auction(self, ctx):
        """Force the next auction immediately."""
        await self.auction.force()
        await ctx.send(embed=AuctionEmbeds.success("Auction forced."))

    @auction_group.command(name="nextpool", aliases=["poolnext"])
    @commands.admin_or_permissions(manage_guild=True)
    async def next_pool(self, ctx, *, name: str = None):
        """Make the next auction draw a random or specified pool character."""
        available_pool = await self.characters.available_pool()
        if not available_pool:
            return await ctx.send(embed=AuctionEmbeds.error("There are no unowned characters in the pool."))

        character_id = None
        if name:
            character = self.characters.get_by_name(clean_name(name))
            if not character:
                return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

            character_id = int(character["id"])
            if character_id not in available_pool:
                return await ctx.send(embed=AuctionEmbeds.error("That character is not currently in the pool."))

            queue = await self.config.queue()
            if any(int(entry.get("character_id", 0)) == character_id for entry in queue):
                return await ctx.send(embed=AuctionEmbeds.error("That character is already queued for sale."))

        await self.config.forced_next_source.set("pool")
        await self.config.forced_next_character_id.set(character_id)
        message = (
            f"The next auction will use **{character['name']}** from the pool."
            if name
            else "The next auction will use a random character from the pool."
        )
        await ctx.send(embed=AuctionEmbeds.success(message))

    @auction_group.command(name="skip")
    @commands.admin_or_permissions(manage_guild=True)
    async def skip_auction(self, ctx):
        """Skip the current auction and start the next."""
        await self.auction.skip()
        await ctx.send(embed=AuctionEmbeds.success("Current auction skipped."))

    @auction_group.command(name="logchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for completed sale and buyback logs."""
        await self.config.log_channel.set(channel.id)
        await ctx.send(embed=AuctionEmbeds.success(f"Auction transactions will be logged in {channel.mention}."))

    @auction_group.command(name="clearlogchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def clear_log_channel(self, ctx):
        """Disable transaction logging."""
        await self.config.log_channel.set(None)
        await ctx.send(embed=AuctionEmbeds.success("Auction transaction logging has been disabled."))

    @auction_group.command(name="debtlogchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def debt_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel used for overdue debt reports."""
        await self.config.debt_log_channel.set(channel.id)
        await ctx.send(embed=AuctionEmbeds.success(f"Overdue debt reports will be sent to {channel.mention}."))

    @auction_group.command(name="cleardebtlogchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def clear_debt_log_channel(self, ctx):
        """Disable the dedicated overdue debt report channel."""
        await self.config.debt_log_channel.set(None)
        await ctx.send(embed=AuctionEmbeds.success("Overdue debt report logging has been disabled."))

    @auction_group.command(name="taxrate", aliases=["settax"])
    @commands.admin_or_permissions(manage_guild=True)
    async def tax_rate(self, ctx, percent: float = None):
        """View or set the daily tax percentage charged from available beri."""
        if percent is None:
            rate = float(await self.config.tax_rate() or 0)
            return await ctx.send(embed=AuctionEmbeds.success(f"Current daily tax rate: **{rate:g}%**."))
        if percent <= 0 or percent > 100:
            return await ctx.send(embed=AuctionEmbeds.error("The daily tax rate must be greater than 0 and at most 100%."))

        await self.config.tax_rate.set(percent)
        await ctx.send(embed=AuctionEmbeds.success(f"Daily tax rate set to **{percent:g}%**."))

    @auction_group.command(name="sellhouserate", aliases=["sethousesellrate"])
    @commands.admin_or_permissions(manage_guild=True)
    async def sellhouse_rate(self, ctx, percent: float = None):
        """View or set the Auction house buyback percentage."""
        if percent is None: 
            rate = int(await self.config.sellhouse_rate() or 70)
            return await ctx.send(
                embed=AuctionEmbeds.success(
                    f"Current sellhouse buyback rate: **{rate}%**."
                )
            )
        if percent < 1 or percent > 100:
            return await ctx.send(
                embed=AuctionEmbeds.error("The sellhouse rate must be between 1% and 100%.")
            )
        await self.config.sellhouse_rate.set(percent)
        await ctx.send(embed=AuctionEmbeds.success(
            f"Sellhouse buyback rate set to **{percent}%**."
            )
        )

    @auction_group.command(name="dailyrate", aliases=["setdaily"])
    @commands.admin_or_permissions(manage_guild=True)
    async def daily_rate(self, ctx, amount: int = None):
        """View or set the daily beri reward."""
        if amount is None:
            current = int(await self.config.daily_income() or 1000)
            return await ctx.send(
                embed=AuctionEmbeds.success(
                    f"Current daily reward: **{format_berries(current)}**."
                )
            )

        await self.config.daily_income.set(amount)
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Daily reward set to **{format_berries(amount)}**."
            )
        )
    

    @auction_group.command(name="taxchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def tax_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel that receives daily tax collection announcements."""
        await self.config.tax_channel.set(channel.id)
        await ctx.send(embed=AuctionEmbeds.success(f"Daily tax collections will be announced in {channel.mention}."))

    @auction_group.command(name="taxes", aliases=["fees", "taxespaid"])
    async def taxes_paid(self, ctx, member: discord.Member = None):
        """Show your cumulative daily taxes and Auction House fees paid."""
        target = member or ctx.author
        if member and member.id != ctx.author.id:
            permissions = ctx.author.guild_permissions if ctx.guild else None
            if not permissions or not (permissions.administrator or permissions.manage_guild):
                return await ctx.send(embed=AuctionEmbeds.error("Only administrators can view another member's totals."))
        player = self.config.user_from_id(target.id)
        if not await self.economy.exists(target.id) and not (
            int(await player.taxes_paid() or 0) or int(await player.fees_paid() or 0)
        ):
            return await ctx.send(embed=AuctionEmbeds.error("That member has not started the auction game."))

        taxes_paid = int(await player.taxes_paid() or 0)
        fees_paid = int(await player.fees_paid() or 0)
        charitable_deductions = int(await player.charitable_deductions() or 0)
        embed = discord.Embed(
            title=f"Taxes and Fees: {target.display_name}",
            description=(
                f"Daily taxes paid: **{format_berries(taxes_paid)}**\n"
                f"Auction House fees paid: **{format_berries(fees_paid)}**\n\n"
                f"**Total paid: {format_berries(taxes_paid + fees_paid)}**\n"
                f"Charitable deduction remaining: **{format_berries(charitable_deductions)}**"
            ),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    @auction_group.command(name="rebuildtaxesfees", aliases=["rebuildtotals"])
    @commands.admin_or_permissions(manage_guild=True)
    async def rebuild_taxes_fees(self, ctx):
        """Rebuild tax totals from the transaction log and fees from stored history."""
        async with self.auction._state_lock:
            result = await self.rebuild_tax_fee_ledgers()

        if result is None:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    "Set a readable transaction log channel first with `.ac logchannel #channel`."
                )
            )

        caveat_text = (
            f"{result['fee_logs_skipped']} fee log(s) did not contain a readable payer and amount."
            if result["fee_logs_skipped"]
            else "All matching fee logs were attributable."
        )
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Scanned {result['log_messages']} log message(s).\n"
                f"Rebuilt {result['tax_payments']} tax payment(s) and {result['fee_payments']} fee payment(s) "
                f"for {result['members']} member(s).\n{caveat_text}"
            )
        )

    @auction_group.command(name="taxstart")
    @commands.admin_or_permissions(manage_guild=True)
    async def start_taxes(self, ctx):
        """Start the 24-hour daily tax schedule."""
        rate = float(await self.config.tax_rate() or 0)
        if rate <= 0:
            return await ctx.send(embed=AuctionEmbeds.error("Set a daily tax rate first with `.auction taxrate <percent>`."))
        if not await self.config.tax_channel():
            return await ctx.send(embed=AuctionEmbeds.error("Set a tax announcement channel first with `.auction taxchannel #channel`."))
        if not await self.config.log_channel():
            return await ctx.send(embed=AuctionEmbeds.error("Set the transaction log channel first with `.auction logchannel #channel`."))

        await self.config.tax_running.set(True)
        await self.config.tax_last_collected.set(0)
        await self.collect_daily_taxes(force=True)
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Daily taxes started at **{rate:g}%** and have been collected now. "
                "The next collection will occur in 24 hours."
            )
        )

    @auction_group.command(name="taxstop")
    @commands.admin_or_permissions(manage_guild=True)
    async def stop_taxes(self, ctx):
        """Stop future automatic daily tax collections."""
        await self.config.tax_running.set(False)
        await ctx.send(embed=AuctionEmbeds.success("Daily taxes have been stopped."))

    @auction_group.command(name="overduedebts", aliases=["debtreport"])
    @commands.admin_or_permissions(manage_guild=True)
    async def overdue_debts(self, ctx):
        """Post a report of members whose 48-hour loan grace period has expired."""
        entries = []
        for user_id, data in (await self.config.all_users()).items():
            user_id = int(user_id)
            debt = int(data.get("debt", 0) or 0)
            if debt and await self.debt_is_overdue(user_id):
                entries.append((user_id, debt))

        entries.sort(key=lambda entry: entry[1], reverse=True)
        if not await self.log_overdue_debts(entries):
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    "Set a valid debt report channel first with `.auction debtlogchannel #channel`."
                )
            )
        await ctx.send(embed=AuctionEmbeds.success(f"Posted an overdue debt report for {len(entries)} member(s)."))

    @auction_group.command(name="block")
    @commands.admin_or_permissions(manage_guild=True)
    async def block_user(self, ctx, member: discord.Member):
        """Block a member from bidding in auction chat."""
        blocked = await self.config.blocked_users()
        if member.id in [int(item) for item in blocked]:
            return await ctx.send(embed=AuctionEmbeds.error(f"{member.mention} is already blocked."))

        blocked.append(member.id)
        await self.config.blocked_users.set(blocked)
        await ctx.send(embed=AuctionEmbeds.success(f"{member.mention} has been blocked from auctioning."))

    @auction_group.command(name="unblock")
    @commands.admin_or_permissions(manage_guild=True)
    async def unblock_user(self, ctx, member: discord.Member):
        """Remove a member from the auction block list."""
        blocked = await self.config.blocked_users()
        if member.id not in [int(item) for item in blocked]:
            return await ctx.send(embed=AuctionEmbeds.error(f"{member.mention} is not blocked."))

        blocked = [user_id for user_id in blocked if int(user_id) != member.id]
        await self.config.blocked_users.set(blocked)
        await ctx.send(embed=AuctionEmbeds.success(f"{member.mention} has been removed from the block list."))

    @auction_group.command(name="blocklist")
    @commands.admin_or_permissions(manage_guild=True)
    async def blocklist(self, ctx):
        """Show the configured auction block list."""
        blocked = await self.config.blocked_users()
        if not blocked:
            return await ctx.send(embed=AuctionEmbeds.success("The auction block list is empty."))

        lines = []
        for user_id in blocked:
            user = self.bot.get_user(int(user_id))
            lines.append(f"• <@{user_id}>" if user else f"• `{user_id}`")

        embed = discord.Embed(title="Auction Block List", color=discord.Color.orange())
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @auction_group.command(name="status")
    @commands.admin_or_permissions(manage_guild=True)
    async def status(self, ctx):
        """Show the current auction configuration and run state."""
        state = await self.auction.status()
        embed = discord.Embed(title="Auction Status", color=discord.Color.blurple())
        embed.add_field(name="Running", value=str(state["running"]))
        embed.add_field(name="Channel", value=f"<#{state['channel']}>" if state['channel'] else "Unconfigured")
        embed.add_field(name="Duration", value=str(state["duration"]))
        embed.add_field(name="Interval", value=str(state["interval"]))
        embed.add_field(name="Current", value="Yes" if state["current"] else "No")
        await ctx.send(embed=embed)

    @auction_group.command(name="addcharacter", aliases=["addchar"])
    @commands.admin_or_permissions(manage_guild=True)
    async def add_character(self, ctx, name: str, rarity: str = "Common", arc: str = "Unknown", wiki: str = ""):
        """Add a new character to the persistent roster."""
        character = self.characters.add(name=name, rarity=rarity, arc=arc, wiki=wiki)
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("That character already exists or the name was empty."))
        await self.config.character_roster.set(self.characters.all())
        await ctx.send(embed=AuctionEmbeds.success(f"Added {character['name']} to the roster."))

    @auction_group.command(name="grantcharacter", aliases=["grantchar"])
    @commands.admin_or_permissions(manage_guild=True)
    async def grant_character(self, ctx, member: discord.Member, *, name: str):
        """Grant an unowned pool character to a registered player."""
        if not await self.economy.exists(member.id):
            return await ctx.send(embed=AuctionEmbeds.error("That member has not started the auction game."))

        character = self.characters.get_by_name(clean_name(name))
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character in the roster."))

        character_id = int(character["id"])
        if self.characters.owned(character_id):
            return await ctx.send(embed=AuctionEmbeds.error("That character is already owned."))

        queue = await self.config.queue()
        if any(int(entry.get("character_id", 0)) == character_id for entry in queue):
            return await ctx.send(embed=AuctionEmbeds.error("That character is currently queued for sale."))

        current = await self.auction.get_current_auction()
        if current and int(current.get("character_id", 0)) == character_id:
            return await ctx.send(embed=AuctionEmbeds.error("That character is currently being auctioned."))

        await self.economy.add_character(member.id, character_id)
        self.characters.assign(character_id, member.id)
        await self.record_transaction("grant", user_id=member.id, character_id=character_id)
        await ctx.send(embed=AuctionEmbeds.success(f"Granted **{character['name']}** to {member.mention}."))

    @auction_group.command(name="audit")
    @commands.admin_or_permissions(manage_guild=True)
    async def audit(self, ctx):
        """Repair reservations and owner cache, then report duplicate collections."""
        await self.rebuild_reservations()
        users = await self.config.all_users()
        claims: dict[int, list[int]] = {}
        for user_id, data in users.items():
            if not data.get("started"):
                continue
            for character_id in data.get("characters", []):
                claims.setdefault(int(character_id), []).append(int(user_id))

        await self.characters.rebuild_owners()
        duplicates = sum(1 for owners in claims.values() if len(owners) > 1)
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Audit completed. Reservations and owner cache were rebuilt. Duplicate collection claims found: {duplicates}."
            )
        )

    @auction_group.command(name="repaircollections", aliases=["synccollections"])
    @commands.admin_or_permissions(manage_guild=True)
    async def repair_collections(self, ctx):
        """Globally synchronize persisted collection lists with the current ownership cache."""
        async with self.auction._state_lock:
            users = await self.config.all_users()
            valid_character_ids = set(self.characters.all_ids())
            owned_by_user: dict[int, list[int]] = {}
            for character_id, owner_id in self.characters.owners.items():
                if character_id in valid_character_ids:
                    owned_by_user.setdefault(int(owner_id), []).append(int(character_id))

            changed_users = 0
            removed_entries = 0
            restored_entries = 0
            for user_id, data in users.items():
                user_id = int(user_id)
                if not data.get("started"):
                    continue

                old_collection = [int(character_id) for character_id in data.get("characters", [])]
                new_collection = sorted(owned_by_user.get(user_id, []))
                if old_collection == new_collection:
                    continue

                removed_entries += len(set(old_collection) - set(new_collection))
                restored_entries += len(set(new_collection) - set(old_collection))
                await self.config.user_from_id(user_id).characters.set(new_collection)
                changed_users += 1

            await self.characters.rebuild_owners()

        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Collections synchronized. Updated {changed_users} user(s), removed {removed_entries} stale character entry(s), "
                f"and restored {restored_entries} missing owned character entry(s)."
            )
        )

    @auction_group.command(name="repairlast")
    @commands.admin_or_permissions(manage_guild=True)
    async def repair_last(self, ctx, count: int = 1, confirmation: str = ""):
        """Safely reverse up to the requested number of latest transactions."""
        if count < 1 or count > 50:
            return await ctx.send(embed=AuctionEmbeds.error("Choose a transaction count from 1 to 50."))

        if confirmation != "CONFIRM":
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    f"This reverses the latest {count} ledger transaction(s), including completed sales. "
                    f"Use `.auction repairlast {count} CONFIRM` to proceed."
                )
            )

        async with self.auction._state_lock:
            history = await self.config.transaction_history()
            reversed_count = 0
            blocked_reason = None

            for entry_index in range(len(history) - 1, -1, -1):
                if history[entry_index].get("reversed"):
                    continue

                error = await self._reverse_transaction(history[entry_index])
                if error:
                    blocked_reason = error
                    break

                history[entry_index]["reversed"] = True
                reversed_count += 1
                if reversed_count >= count:
                    break

            if reversed_count:
                await self.config.transaction_history.set(history)

        if not reversed_count:
            if blocked_reason:
                return await ctx.send(embed=AuctionEmbeds.error(blocked_reason))
            return await ctx.send(embed=AuctionEmbeds.error("There is no unreversed transaction in the ledger."))

        detail = f" Reversed {reversed_count} transaction(s)."
        if blocked_reason:
            detail += f" Stopped: {blocked_reason}"
        await ctx.send(embed=AuctionEmbeds.success(detail.strip()))

    async def _reverse_transaction(self, entry: dict) -> str | None:
        """Reverse one ledger entry, or return why its current state is unsafe."""
        kind = entry.get("kind")
        user_id = int(entry.get("user_id", 0) or 0)
        character_id = int(entry.get("character_id", 0) or 0)
        amount = int(entry.get("amount", 0) or 0)

        if kind == "daily":
            if await self.economy.available_balance(user_id) < amount:
                return "The user's available balance is too low to reverse a daily claim."
            await self.economy.adjust_balance(user_id, -amount)
        elif kind == "grant":
            if self.characters.owner_of(character_id) != user_id:
                return "A granted character has changed owners and cannot be safely reversed."
            await self.economy.remove_character(user_id, character_id)
            self.characters.unassign(character_id)
        elif kind == "buyback":
            if self.characters.owned(character_id):
                return "A buyback character is no longer in the pool and cannot be safely reversed."
            if await self.economy.available_balance(user_id) < amount:
                return "The seller's available balance is too low to reverse a buyback."
            await self.economy.adjust_balance(user_id, -amount)
            await self.economy.add_character(user_id, character_id)
            self.characters.assign(character_id, user_id)
            vault_amount = int(entry.get("vault_amount", amount) or 0)
            await self.config.total_fees.set((await self.config.total_fees()) + vault_amount)
        elif kind == "sale":
            buyer_id = int(entry.get("buyer_id", 0) or 0)
            seller_id = int(entry.get("seller_id", 0) or 0)
            price = int(entry.get("price", 0) or 0)
            seller_share = int(entry.get("seller_share", 0) or 0)
            if self.characters.owner_of(character_id) != buyer_id:
                return "A sold character has changed owners and cannot be safely reversed."
            if seller_id and await self.economy.available_balance(seller_id) < seller_share:
                return "The seller's available balance is too low to reverse a sale."

            vault_amount = int(entry.get("vault_amount", price - seller_share) or 0)
            vault_balance = await self.config.total_fees()
            if vault_balance < vault_amount:
                return "The Auction House Vault no longer has enough balance to reverse this sale."

            if seller_id:
                await self.economy.adjust_balance(seller_id, -seller_share)
            await self.economy.remove_character(buyer_id, character_id)
            await self.economy.deposit(buyer_id, price)
            if seller_id:
                await self.economy.add_character(seller_id, character_id)
                self.characters.assign(character_id, seller_id)
            else:
                self.characters.unassign(character_id)

            await self.config.total_fees.set(vault_balance - vault_amount)

            last_sale_prices = await self.config.last_sale_prices()
            last_sale_prices[str(character_id)] = int(entry.get("previous_sale_price", 0) or 0)
            await self.config.last_sale_prices.set(last_sale_prices)
        else:
            return "The latest transaction type cannot be safely reversed automatically."

        return None

    @auction_group.command(name="removecharacter", aliases=["rmchar"])
    @commands.admin_or_permissions(manage_guild=True)
    async def remove_character(self, ctx, *, name: str):
        """Remove a character from the persistent roster."""
        target = self.characters.get_by_name(name)
        if not target:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character in the roster."))

        self.characters.remove(int(target["id"]))
        await self.config.character_roster.set(self.characters.all())
        await ctx.send(embed=AuctionEmbeds.success(f"Removed {target['name']} from the roster."))

    @auction_group.command(name="exportcharacters", aliases=["exportchars", "exportjson"])
    @commands.admin_or_permissions(manage_guild=True)
    async def export_characters(self, ctx):
        """Export the full roster as characters.json."""
        payload = self.characters.export_json().encode("utf-8")
        await ctx.send(file=discord.File(io.BytesIO(payload), filename="characters.json"))

    @auction_group.command(name="importcharacters", aliases=["importchars", "importjson"])
    @commands.admin_or_permissions(manage_guild=True)
    async def import_characters(self, ctx, *, payload: str = None):
        """Import a characters.json payload from a command argument or attachment."""
        if not payload:
            if ctx.message.attachments:
                attachment = ctx.message.attachments[0]
                payload = (await attachment.read()).decode("utf-8")
            else:
                return await ctx.send(embed=AuctionEmbeds.error("Provide a JSON payload or attach a characters.json file."))

        if not self.characters.import_json(payload):
            return await ctx.send(embed=AuctionEmbeds.error("The supplied payload is not a valid character JSON array."))

        await self.config.character_roster.set(self.characters.all())
        await ctx.send(embed=AuctionEmbeds.success("Character roster imported successfully."))

    @auction_group.command(name="wipe", aliases=["resetusers", "reset"])
    @commands.admin_or_permissions(manage_guild=True)
    async def wipe(self, ctx):
        """Reset all player, queue, and auction state in the cog."""
        await self.config.auction_running.set(False)
        await self.auction.cancel_current_auction()
        await self.config.queue.set([])
        await self.config.last_auction_started.set(0)
        await self.config.last_auction_source.set("pool")
        await self.config.next_auction_source.set("queue")
        await self.config.forced_next_source.set(None)
        await self.config.forced_next_character_id.set(None)
        await self.config.total_fees.set(0)
        await self.config.last_sale_prices.set({})
        await self.config.transaction_history.set([])
        await self.config.pending_trades.set({})

        users = await self.config.all_users()
        for user_id in list(users.keys()):
            player = self.config.user_from_id(int(user_id))
            await player.clear()

        await self.characters.rebuild_owners()
        await ctx.send(embed=AuctionEmbeds.success("All auction data, including the queue, has been wiped."))

    @auction_group.command(name="wipeuser", aliases=["resetuser"])
    @commands.admin_or_permissions(manage_guild=True)
    async def wipe_user(self, ctx, member: discord.Member):
        """Remove one member's auction account, collection, and queued listings."""
        async with self.auction._state_lock:
            current = await self.auction.get_current_auction()
            if current:
                live_seller_id = int(current.get("seller_id", 0) or 0)
                highest_bidder_id = int(current.get("highest_bidder_id", 0) or 0)
                if member.id in {live_seller_id, highest_bidder_id}:
                    return await ctx.send(
                        embed=AuctionEmbeds.error(
                            "You cannot wipe this member while they are involved in the live auction."
                        )
                    )

            queue = await self.config.queue()
            remaining_queue = [
                entry for entry in queue if int(entry.get("seller_id", 0) or 0) != member.id
            ]
            removed_listings = len(queue) - len(remaining_queue)
            await self.config.queue.set(remaining_queue)

            await self.config.user_from_id(member.id).clear()
            await self.characters.rebuild_owners()

        await ctx.send(
            embed=AuctionEmbeds.success(
                f"Wiped all Auction data for {member.mention}, including {removed_listings} queued listing(s)."
            )
        )

    @auction_group.command(name="channel")
    @commands.admin_or_permissions(manage_guild=True)
    async def set_channel(self, ctx, channel: discord.TextChannel):
        """Set the auction channel."""
        await self.config.auction_channel.set(channel.id)
        await ctx.send(f"Auction channel set to {channel.mention}")

    @auction_group.command(name="duration")
    @commands.admin_or_permissions(manage_guild=True)
    async def set_duration(self, ctx, duration: int):
        """Set the auction duration in seconds."""
        if duration <= 0:
            return await ctx.send(embed=AuctionEmbeds.error("Duration must be positive."))
        await self.config.auction_duration.set(duration)
        await ctx.send(f"Auction duration set to {duration} seconds.")

    @auction_group.command(name="interval")
    @commands.admin_or_permissions(manage_guild=True)
    async def set_interval(self, ctx, interval: int):
        """Set the automatic auction interval in seconds."""
        if interval <= 0:
            return await ctx.send(embed=AuctionEmbeds.error("Interval must be positive."))
        await self.config.auction_interval.set(interval)
        await ctx.send(f"Auction interval set to {interval} seconds.")