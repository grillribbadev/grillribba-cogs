from __future__ import annotations

import io
import logging
import random

import discord

from redbot.core import Config, commands

from .auction import AuctionManager
from .characters import CharacterManager
from .constants import (
    DEFAULT_AUCTION_DURATION,
    DEFAULT_AUCTION_INTERVAL,
    MINIGAME_COOLDOWN_SECONDS,
    PRAY_MAX_PENALTY,
    PRAY_MAX_REWARD,
    PRAY_MIN_PENALTY,
    PRAY_MIN_REWARD,
    PRAY_SUCCESS_CHANCE,
    STARTING_BALANCE,
    STEAL_MAX_PENALTY,
    STEAL_MAX_REWARD,
    STEAL_MIN_PENALTY,
    STEAL_MIN_REWARD,
    STEAL_SUCCESS_CHANCE,
)
from .economy import Economy
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
            "auction_running": False,
            "auction_duration": DEFAULT_AUCTION_DURATION,
            "auction_interval": DEFAULT_AUCTION_INTERVAL,
            "current_auction": {},
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
        }

        self.config.register_global(**default_global)
        self.config.register_user(**default_user)

        self.economy = Economy(self.config)
        self.characters = CharacterManager(self)
        self.auction = AuctionManager(self)
        self.auction_task = None

    async def cog_load(self):
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
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(title=title, description=description, color=discord.Color.gold())
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def record_transaction(self, kind: str, **details) -> None:
        """Store a bounded history of completed economy changes."""
        history = await self.config.transaction_history()
        history.append({"kind": kind, "timestamp": utc_timestamp(), "reversed": False, **details})
        await self.config.transaction_history.set(history[-200:])

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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen only in the configured auction channel for bid messages."""
        if message.author.bot:
            return

        if await self.is_blocked(message.author.id):
            return

        if await self.config.auction_running() is False:
            return

        if not await self.economy.exists(message.author.id):
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

    @commands.group(name="auction", invoke_without_command=True)
    async def auction_group(self, ctx):
        """Auction commands."""
        await ctx.send_help()

    @auction_group.command(name="start")
    async def start_game(self, ctx):
        """Register as a player."""

        created = await self.economy.register_player(ctx.author.id)
        if not created:
            return await ctx.send(embed=AuctionEmbeds.error("You have already joined the game."))

        await ctx.send(embed=AuctionEmbeds.success("Welcome to the auction!\nYou received ฿250."))

    @auction_group.command(name="balance", aliases=["wallet", "beri"])
    async def balance(self, ctx, member: discord.Member = None):
        """View your balance, or an admin can view another member's balance."""
        target = member or ctx.author
        if member and member.id != ctx.author.id:
            permissions = ctx.author.guild_permissions if ctx.guild else None
            if not permissions or not (permissions.administrator or permissions.manage_guild):
                return await ctx.send(embed=AuctionEmbeds.error("Only administrators can view another member's balance."))

        if not await self.economy.exists(target.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

        balance = await self.economy.balance(target.id)
        reserved = await self.economy.reconcile_reservation(target.id)
        await ctx.send(embed=AuctionEmbeds.balance(target, balance, reserved))

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

        remaining = await self.economy.claim_daily(ctx.author.id)
        if remaining > 0:
            return await ctx.send(
                embed=AuctionEmbeds.error(
                    f"Your daily beri is available again in {format_duration(remaining)}."
                )
            )

        await self.record_transaction("daily", user_id=ctx.author.id, amount=250)
        await ctx.send(embed=AuctionEmbeds.success(f"You claimed your daily {format_berries(250)}."))

    @auction_group.command(name="ping")
    async def ping(self, ctx):
        """Choose rarity pings for upcoming pool auctions."""
        selected = await self.config.user_from_id(ctx.author.id).ping_rarities()
        await ctx.send(
            embed=AuctionEmbeds.ping_preferences(selected),
            view=AuctionPingView(self, selected),
        )

    @auction_group.command(name="collection")
    async def collection(self, ctx):
        """List the characters owned by the caller."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send("Use `.auction start` first.")

        queue = await self.config.queue()
        queued_ids = {
            int(entry.get("character_id", 0))
            for entry in queue
            if int(entry.get("seller_id", 0) or 0) == ctx.author.id
        }
        current = await self.auction.get_current_auction()
        live_character_id = (
            int(current.get("character_id", 0))
            if current and int(current.get("seller_id", 0) or 0) == ctx.author.id
            else 0
        )

        characters = []
        for character_id in await self.economy.get_characters(ctx.author.id):
            character = self.characters.get(character_id)
            if character:
                status = "Up for auction" if int(character_id) == live_character_id else "Queued for sale" if int(character_id) in queued_ids else "Owned"
                characters.append((character, status))

        await ctx.send(embed=AuctionEmbeds.collection(ctx.author, characters))

    @auction_group.command(name="sell")
    async def sell(self, ctx, *, name: str):
        """Queue a character with an optional trailing starting bid."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send("Use `.auction start` first.")

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
        """Sell an owned character to the auction house for half its last sale price."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send(embed=AuctionEmbeds.error("Use `.auction start` first."))

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

        payout = last_price // 2
        await self.economy.deposit(ctx.author.id, payout)
        await self.economy.remove_character(ctx.author.id, character_id)
        self.characters.unassign(character_id)
        await self.record_transaction(
            "buyback",
            user_id=ctx.author.id,
            character_id=character_id,
            amount=payout,
        )
        await self.log_transaction(
            "🏦 Auction House Buyback",
            f"Character: **{character['name']}**\nSeller: {ctx.author.mention}\n"
            f"Last sale: {format_berries(last_price)}\nBuyback payout: **{format_berries(payout)}**",
        )
        await ctx.send(
            embed=AuctionEmbeds.success(
                f"The Auction House bought **{character['name']}** for {format_berries(payout)}."
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

    @auction_group.command(name="bank")
    async def bank(self, ctx):
        """Show how much beri the auction house has collected in fees."""
        total = await self.config.total_fees()
        await ctx.send(embed=AuctionEmbeds.bank(total))

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
            f"Daily income: {format_berries(250)} every 24 hours\n"
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

    @auction_group.command(name="repairlast")
    @commands.admin_or_permissions(manage_guild=True)
    async def repair_last(self, ctx, count: int = 1):
        """Safely reverse up to the requested number of latest transactions."""
        if count < 1 or count > 50:
            return await ctx.send(embed=AuctionEmbeds.error("Choose a transaction count from 1 to 50."))

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

        if not reversed_count:
            if blocked_reason:
                return await ctx.send(embed=AuctionEmbeds.error(blocked_reason))
            return await ctx.send(embed=AuctionEmbeds.error("There is no unreversed transaction in the ledger."))

        await self.config.transaction_history.set(history)
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
        elif kind == "sale":
            buyer_id = int(entry.get("buyer_id", 0) or 0)
            seller_id = int(entry.get("seller_id", 0) or 0)
            price = int(entry.get("price", 0) or 0)
            seller_share = int(entry.get("seller_share", 0) or 0)
            if self.characters.owner_of(character_id) != buyer_id:
                return "A sold character has changed owners and cannot be safely reversed."
            if seller_id and await self.economy.available_balance(seller_id) < seller_share:
                return "The seller's available balance is too low to reverse a sale."

            if seller_id:
                await self.economy.adjust_balance(seller_id, -seller_share)
            await self.economy.remove_character(buyer_id, character_id)
            await self.economy.deposit(buyer_id, price)
            if seller_id:
                await self.economy.add_character(seller_id, character_id)
                self.characters.assign(character_id, seller_id)
                fee = price - seller_share
                await self.config.total_fees.set(max(0, await self.config.total_fees() - fee))
            else:
                self.characters.unassign(character_id)

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

        users = await self.config.all_users()
        for user_id in list(users.keys()):
            player = self.config.user_from_id(int(user_id))
            await player.clear()

        await self.characters.rebuild_owners()
        await ctx.send(embed=AuctionEmbeds.success("All auction data, including the queue, has been wiped."))

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