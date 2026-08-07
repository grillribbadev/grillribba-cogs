from __future__ import annotations

import discord

from redbot.core import Config, commands

from .auction import AuctionManager
from .characters import CharacterManager
from .constants import (
    DEFAULT_AUCTION_DURATION,
    DEFAULT_AUCTION_INTERVAL,
    STARTING_BALANCE,
)
from .economy import Economy
from .utils import clean_name, format_berries, utc_timestamp
from .views import AuctionEmbeds


class OPAuction(commands.Cog):
    """One Piece Auction"""

    __author__ = "Grillribba"
    __version__ = "1.0.0"

    def __init__(self, bot):
        self.bot = bot

        self.config = Config.get_conf(
            self,
            identifier=948362871,
            force_registration=True,
        )

        default_global = {
            "auction_channel": None,
            "auction_running": False,
            "auction_duration": DEFAULT_AUCTION_DURATION,
            "auction_interval": DEFAULT_AUCTION_INTERVAL,
            "current_auction": {},
            "queue": [],
            "last_auction_started": 0,
        }

        default_user = {
            "started": False,
            "balance": 0,
            "reserved": 0,
            "characters": [],
            "joined": 0,
            "last_daily": 0,
        }

        self.config.register_global(**default_global)
        self.config.register_user(**default_user)

        self.economy = Economy(self.config)
        self.characters = CharacterManager(self)
        self.auction = AuctionManager(self)

        self.auction_task = self.bot.loop.create_task(self.auction.background_loop())

    def cog_unload(self):
        self.auction_task.cancel()

    async def red_delete_data_for_user(self, **kwargs):
        """Redbot data cleanup hook."""
        pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen only in the configured auction channel for bid messages."""
        if message.author.bot:
            return

        if await self.config.auction_running() is False:
            return

        state = await self.auction.get_current_auction()
        if not state:
            return

        if message.channel.id != int(state.get("channel_id", 0)):
            return

        if message.content and message.content.strip().isdigit():
            await self.auction.handle_bid(message)

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
            return await ctx.send("You have already joined the game.")

        await ctx.send("Welcome to the auction!\nYou received ฿250.")

    @auction_group.command(name="balance")
    async def balance(self, ctx):
        """View your balance."""

        if not await self.economy.exists(ctx.author.id):
            return await ctx.send("Use `.auction start` first.")

        balance = await self.economy.balance(ctx.author.id)
        await ctx.send(f"Balance: ฿{balance}")

    @auction_group.command(name="collection")
    async def collection(self, ctx):
        """List the characters owned by the caller."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send("Use `.auction start` first.")

        characters = []
        for character_id in await self.economy.get_characters(ctx.author.id):
            character = self.characters.get(character_id)
            if character:
                characters.append(character)

        await ctx.send(embed=AuctionEmbeds.collection(ctx.author, characters))

    @auction_group.command(name="sell")
    async def sell(self, ctx, *, name: str):
        """Queue a character owned by the caller for auction."""
        if not await self.economy.exists(ctx.author.id):
            return await ctx.send("Use `.auction start` first.")

        normalized_name = clean_name(name)
        character = self.characters.get_by_name(normalized_name)
        if not character:
            return await ctx.send(embed=AuctionEmbeds.error("I could not find that character."))

        if not self.characters.owner_of(int(character["id"])) == ctx.author.id:
            return await ctx.send(embed=AuctionEmbeds.error("You do not own that character."))

        queue = await self.config.queue()
        if any(item.get("character_id") == int(character["id"]) for item in queue):
            return await ctx.send(embed=AuctionEmbeds.error("That character is already queued."))

        queue.append({"character_id": int(character["id"]), "seller_id": ctx.author.id})
        await self.config.queue.set(queue)

        await ctx.send(embed=AuctionEmbeds.success(f"{character['name']} has been added to the auction queue."))

    @auction_group.command(name="info")
    async def info(self, ctx):
        """Show the current OPAuction status summary."""
        status = await self.auction.status()
        desc = (
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
        """Start the automatic auction loop."""
        await self.auction.begin()
        await ctx.send(embed=AuctionEmbeds.success("Auction automation has started."))

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

    @auction_group.command(name="skip")
    @commands.admin_or_permissions(manage_guild=True)
    async def skip_auction(self, ctx):
        """Skip the current auction and start the next."""
        await self.auction.skip()
        await ctx.send(embed=AuctionEmbeds.success("Current auction skipped."))

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