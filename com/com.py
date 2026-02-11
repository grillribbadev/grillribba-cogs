from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger(__name__)


DEFAULT_GUILD = {"channels": [], "announce_channel": 0, "stats": {}}


def _month_key_for_dt(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.utcnow()
    return f"{dt.year}-{dt.month:02d}"


class ChatterOfMonth(commands.Cog):
    """Track messages per-month in configured channels and announce the top chatter."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC0FFEE1, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

    # ---------- Event listener ------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        guild = message.guild
        if guild is None:
            return
        cfg = await self.config.guild(guild).channels()
        if not cfg:
            return
        if message.channel.id not in cfg:
            return
        month = _month_key_for_dt()
        async with self.config.guild(guild).stats() as stats:
            month_stats = stats.get(month) or {}
            uid = str(message.author.id)
            month_stats[uid] = month_stats.get(uid, 0) + 1
            stats[month] = month_stats

    # ---------- Admin commands ------------------------------------------------
    @commands.group(name="chatter")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def chatter_group(self, ctx: commands.Context):
        """Manage chatter-of-the-month settings."""

    @chatter_group.group(name="channels")
    async def chatter_channels(self, ctx: commands.Context):
        """Manage which channels are counted."""

    @chatter_channels.command(name="add")
    async def chatter_channels_add(self, ctx: commands.Context, channel: discord.TextChannel):
        """Add a channel to be counted."""
        async with self.config.guild(ctx.guild).channels() as chs:
            if channel.id in chs:
                await ctx.send(f"{channel.mention} is already being counted.")
                return
            chs.append(channel.id)
        await ctx.send(f"Now counting messages in {channel.mention}.")

    @chatter_channels.command(name="remove")
    async def chatter_channels_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from counting."""
        async with self.config.guild(ctx.guild).channels() as chs:
            try:
                chs.remove(channel.id)
            except ValueError:
                await ctx.send(f"{channel.mention} was not configured.")
                return
        await ctx.send(f"Stopped counting messages in {channel.mention}.")

    @chatter_channels.command(name="list")
    async def chatter_channels_list(self, ctx: commands.Context):
        """List configured counting channels."""
        chs = await self.config.guild(ctx.guild).channels()
        if not chs:
            await ctx.send("No channels configured.")
            return
        out = []
        for cid in chs:
            c = ctx.guild.get_channel(cid)
            out.append(c.mention if c else f"(deleted channel `{cid}`)")
        await ctx.send("Counting channels:\n" + "\n".join(out))

    @chatter_group.group(name="announce")
    async def chatter_announce(self, ctx: commands.Context):
        """Manage announce channel."""

    @chatter_announce.command(name="set")
    async def chatter_announce_set(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where winners will be announced."""
        await self.config.guild(ctx.guild).announce_channel.set(channel.id)
        await ctx.send(f"Announcements will be posted to {channel.mention}.")

    @chatter_announce.command(name="clear")
    async def chatter_announce_clear(self, ctx: commands.Context):
        """Clear the announce channel (uses command channel)."""
        await self.config.guild(ctx.guild).announce_channel.set(0)
        await ctx.send("Cleared announce channel; announcements will use the command channel.")

    @chatter_announce.command(name="show")
    async def chatter_announce_show(self, ctx: commands.Context):
        """Show the announce channel."""
        cid = await self.config.guild(ctx.guild).announce_channel()
        if not cid:
            await ctx.send("No announce channel set (will use command channel).")
            return
        ch = ctx.guild.get_channel(cid)
        if ch:
            await ctx.send(f"Announcements go to {ch.mention}.")
        else:
            await ctx.send(f"Announce channel is set to unknown channel id `{cid}`.")

    @chatter_group.command(name="winner")
    async def chatter_winner(self, ctx: commands.Context, month: Optional[str] = None):
        """Show the top chatter for a month. Month format `YYYY-MM`. Defaults to previous month."""
        if month is None:
            now = datetime.utcnow()
            first = now.replace(day=1)
            prev = first - timedelta(days=1)
            month = _month_key_for_dt(prev)
        stats = await self.config.guild(ctx.guild).stats()
        month_stats = stats.get(month) or {}
        if not month_stats:
            await ctx.send(f"No data for {month}.")
            return
        # find top
        top_uid, top_count = max(month_stats.items(), key=lambda kv: kv[1])
        top_uid_int = int(top_uid)
        member = ctx.guild.get_member(top_uid_int)
        mention = member.mention if member else f"<@{top_uid_int}>"
        embed = discord.Embed(title=f"Chatter of {month}", color=0x00FF00)
        embed.add_field(name="Winner", value=f"{mention} — {top_count} messages", inline=False)
        # include top 5
        sorted_top = sorted(month_stats.items(), key=lambda kv: kv[1], reverse=True)[:5]
        desc_lines = []
        for uid, cnt in sorted_top:
            uid_i = int(uid)
            m = ctx.guild.get_member(uid_i)
            desc_lines.append(f"{(m.mention if m else f'<@{uid_i}>')}: {cnt}")
        embed.add_field(name="Top 5", value="\n".join(desc_lines), inline=False)
        # send to announce channel if set
        ann = await self.config.guild(ctx.guild).announce_channel()
        if ann:
            ch = ctx.guild.get_channel(ann)
            if ch:
                await ch.send(embed=embed)
                await ctx.send(f"Announced winner for {month} in {ch.mention}.")
                return
        await ctx.send(embed=embed)

    @chatter_group.command(name="show")
    async def chatter_show(self, ctx: commands.Context):
        """Show current config and stats months available."""
        chs = await self.config.guild(ctx.guild).channels()
        ann = await self.config.guild(ctx.guild).announce_channel()
        stats = await self.config.guild(ctx.guild).stats()
        lines = [f"Counting channels: {', '.join(str(ctx.guild.get_channel(c).mention) if ctx.guild.get_channel(c) else str(c) for c in chs) or 'None'}"]
        lines.append(f"Announce channel: {ctx.guild.get_channel(ann).mention if ann and ctx.guild.get_channel(ann) else ('None' if not ann else str(ann))}")
        months = sorted(stats.keys(), reverse=True)[:6]
        lines.append(f"Months with data (recent): {', '.join(months) if months else 'None'}")
        await ctx.send("\n".join(lines))
