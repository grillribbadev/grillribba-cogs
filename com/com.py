from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger(__name__)


DEFAULT_GUILD = {"channels": [], "announce_channel": 0, "stats": {}, "current_override": "", "announce_everyone": False}


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
            embed = discord.Embed(title="Counting channels", description="No channels configured.")
            await ctx.send(embed=embed)
            return
        out = []
        for cid in chs:
            c = ctx.guild.get_channel(cid)
            out.append(c.mention if c else f"(deleted channel `{cid}`)")
        embed = discord.Embed(title="Counting channels", description="\n".join(out))
        await ctx.send(embed=embed)

    @chatter_group.group(name="announce")
    async def chatter_announce(self, ctx: commands.Context):
        """Manage announce channel."""

    @chatter_announce.command(name="set")
    async def chatter_announce_set(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where winners will be announced."""
        await self.config.guild(ctx.guild).announce_channel.set(channel.id)
        embed = discord.Embed(title="Announce Channel Set", description=f"Announcements will be posted to {channel.mention}.")
        await ctx.send(embed=embed)

    @chatter_announce.command(name="clear")
    async def chatter_announce_clear(self, ctx: commands.Context):
        """Clear the announce channel (uses command channel)."""
        await self.config.guild(ctx.guild).announce_channel.set(0)
        embed = discord.Embed(title="Announce Channel Cleared", description="Announcements will use the command channel.")
        await ctx.send(embed=embed)

    @chatter_announce.command(name="show")
    async def chatter_announce_show(self, ctx: commands.Context):
        """Show the announce channel."""
        cid = await self.config.guild(ctx.guild).announce_channel()
        if not cid:
            embed = discord.Embed(title="Announce Channel", description="No announce channel set (will use command channel).")
            await ctx.send(embed=embed)
            return
        ch = ctx.guild.get_channel(cid)
        if ch:
            embed = discord.Embed(title="Announce Channel", description=f"Announcements go to {ch.mention}.")
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(title="Announce Channel", description=f"Announce channel is set to unknown channel id `{cid}`.")
            await ctx.send(embed=embed)

    @chatter_group.command(name="winner")
    async def chatter_winner(self, ctx: commands.Context, month: Optional[str] = None):
        """Show the top chatter for a month. Month format `YYYY-MM`. Defaults to previous month."""
        # Determine month: explicit arg wins, otherwise respect guild backdate override, else previous month
        if month is None:
            override = await self.config.guild(ctx.guild).current_override()
            if override:
                # override stored as YYYY-MM-DD; use its month
                try:
                    odt = datetime.strptime(override, "%Y-%m-%d")
                    month = _month_key_for_dt(odt)
                except Exception:
                    # fallback to previous month if parsing fails
                    now = datetime.utcnow()
                    first = now.replace(day=1)
                    prev = first - timedelta(days=1)
                    month = _month_key_for_dt(prev)
            else:
                now = datetime.utcnow()
                first = now.replace(day=1)
                prev = first - timedelta(days=1)
                month = _month_key_for_dt(prev)
        stats = await self.config.guild(ctx.guild).stats()
        month_stats = stats.get(month) or {}
        if not month_stats:
            embed = discord.Embed(title="No Data", description=f"No data for {month}.")
            await ctx.send(embed=embed)
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
        # send to announce channel if set. Always send embed; optionally mention everyone.
        ann = await self.config.guild(ctx.guild).announce_channel()
        everyone = await self.config.guild(ctx.guild).announce_everyone()
        if ann:
            ch = ctx.guild.get_channel(ann)
            if ch:
                if everyone:
                    await ch.send(content="@everyone", embed=embed)
                else:
                    await ch.send(embed=embed)
                done_embed = discord.Embed(title="Winner Announced", description=f"Announced winner for {month} in {ch.mention}.")
                await ctx.send(embed=done_embed)
                return
        # fallback to command channel
        if everyone:
            await ctx.send(content="@everyone", embed=embed)
        else:
            await ctx.send(embed=embed)

    @chatter_group.command(name="leader")
    async def chatter_leader(self, ctx: commands.Context, date: Optional[str] = None):
        """Show who's currently in lead for a month or a specific date.

        `date` accepts `YYYY-MM-DD` or `YYYY-MM`. If omitted and an override is set
        via `chatter backdate set`, that date will be used; otherwise today's date is used.
        The command shows the top 5 and the current leader for the corresponding calendar month.
        """
        # resolve date: priority -> explicit arg -> guild override -> today
        if date:
            parsed = None
            try:
                parsed = datetime.strptime(date, "%Y-%m-%d")
            except Exception:
                try:
                    parsed = datetime.strptime(date, "%Y-%m")
                except Exception:
                    await ctx.send("Invalid date format. Use `YYYY-MM-DD` or `YYYY-MM`.")
                    return
        else:
            override = await self.config.guild(ctx.guild).current_override()
            if override:
                try:
                    parsed = datetime.strptime(override, "%Y-%m-%d")
                except Exception:
                    try:
                        parsed = datetime.strptime(override, "%Y-%m")
                    except Exception:
                        parsed = datetime.utcnow()
            else:
                parsed = datetime.utcnow()

        month_key = f"{parsed.year}-{parsed.month:02d}"
        stats = await self.config.guild(ctx.guild).stats()
        month_stats = stats.get(month_key) or {}
        if not month_stats:
            await ctx.send(f"No data for {month_key}.")
            return
        # compute top and top 5
        top_uid, top_count = max(month_stats.items(), key=lambda kv: kv[1])
        top_uid_int = int(top_uid)
        member = ctx.guild.get_member(top_uid_int)
        mention = member.mention if member else f"<@{top_uid_int}>"
        embed = discord.Embed(title=f"Current leader — {month_key}")
        embed.add_field(name="Leader", value=f"{mention} — {top_count} messages", inline=False)
        sorted_top = sorted(month_stats.items(), key=lambda kv: kv[1], reverse=True)[:5]
        desc_lines = []
        for uid, cnt in sorted_top:
            uid_i = int(uid)
            m = ctx.guild.get_member(uid_i)
            desc_lines.append(f"{(m.mention if m else f'<@{uid_i}>')}: {cnt}")
        embed.add_field(name="Top 5", value="\n".join(desc_lines), inline=False)
        await ctx.send(embed=embed)

    @chatter_group.group(name="backdate")
    async def chatter_backdate(self, ctx: commands.Context):
        """Manage a display backdate override used by `chatter leader` when no date is provided."""

    @chatter_backdate.command(name="set")
    async def chatter_backdate_set(self, ctx: commands.Context, date: str):
        """Set a backdate override (format `YYYY-MM-DD` or `YYYY-MM`)."""
        # validate
        parsed = None
        try:
            try:
                parsed = datetime.strptime(date, "%Y-%m-%d")
            except Exception:
                # if YYYY-MM provided, normalize to first day of month
                parsed = datetime.strptime(date, "%Y-%m")
                parsed = parsed.replace(day=1)
        except Exception:
            await ctx.send("Invalid date format. Use `YYYY-MM-DD` or `YYYY-MM`.")
            return
        normalized = parsed.strftime("%Y-%m-%d")
        await self.config.guild(ctx.guild).current_override.set(normalized)
        embed = discord.Embed(title="Backdate Set", description=f"Set leader display override to {normalized}.")
        await ctx.send(embed=embed)

    @chatter_backdate.command(name="clear")
    async def chatter_backdate_clear(self, ctx: commands.Context):
        """Clear the backdate override."""
        await self.config.guild(ctx.guild).current_override.set("")
        embed = discord.Embed(title="Backdate Cleared", description="Cleared backdate override; `chatter leader` will use today's date.")
        await ctx.send(embed=embed)

    @chatter_group.command(name="show")
    async def chatter_show(self, ctx: commands.Context):
        """Show current config and stats months available."""
        chs = await self.config.guild(ctx.guild).channels()
        ann = await self.config.guild(ctx.guild).announce_channel()
        override = await self.config.guild(ctx.guild).current_override()
        everyone = await self.config.guild(ctx.guild).announce_everyone()
        stats = await self.config.guild(ctx.guild).stats()
        embed = discord.Embed(title="Chatter Config & Stats")
        channels_display = ', '.join(str(ctx.guild.get_channel(c).mention) if ctx.guild.get_channel(c) else str(c) for c in chs) or 'None'
        ann_display = ctx.guild.get_channel(ann).mention if ann and ctx.guild.get_channel(ann) else ('None' if not ann else str(ann))
        months = sorted(stats.keys(), reverse=True)[:6]
        months_display = ', '.join(months) if months else 'None'
        embed.add_field(name="Counting channels", value=channels_display, inline=False)
        embed.add_field(name="Announce channel", value=ann_display, inline=False)
        embed.add_field(name="Backdate override", value=(override or 'None'), inline=False)
        embed.add_field(name="Announce @everyone", value=('Yes' if everyone else 'No'), inline=False)
        embed.add_field(name="Months with data (recent)", value=months_display, inline=False)
        await ctx.send(embed=embed)

    @chatter_group.command(name="rebuild")
    @commands.admin_or_permissions(manage_guild=True)
    async def chatter_rebuild(self, ctx: commands.Context, month: str, *channels: discord.TextChannel):
        """Rebuild counts for a month by rescanning message history.

        `month` is `YYYY-MM` or `YYYY-MM-DD` (day ignored). If channels are provided,
        those will be scanned; otherwise the cog uses configured counting channels.
        WARNING: this can be slow for large servers.
        """
        # parse month -> start and end datetimes (UTC)
        try:
            try:
                dt = datetime.strptime(month, "%Y-%m-%d")
            except Exception:
                dt = datetime.strptime(month, "%Y-%m")
        except Exception:
            await ctx.send("Invalid month format. Use `YYYY-MM` or `YYYY-MM-DD`.")
            return
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # compute first day of next month
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # determine channels to scan
        if channels:
            scan_channels = list(channels)
        else:
            cfg_ch = await self.config.guild(ctx.guild).channels()
            scan_channels = [ctx.guild.get_channel(cid) for cid in cfg_ch if ctx.guild.get_channel(cid)]

        if not scan_channels:
            await ctx.send("No channels to scan (configured channels are empty or not visible).")
            return

        embed = discord.Embed(title="Rebuild Started", description=f"Starting rebuild for {start.strftime('%Y-%m')} across {len(scan_channels)} channel(s). This may take some time.")
        await ctx.send(embed=embed)
        counts: dict[str, int] = {}
        for ch in scan_channels:
            try:
                async for msg in ch.history(after=start, before=end, limit=None):
                    if msg.author.bot:
                        continue
                    uid = str(msg.author.id)
                    counts[uid] = counts.get(uid, 0) + 1
            except Exception as exc:
                log.exception("Failed scanning channel %s: %s", ch.id, exc)
                err_embed = discord.Embed(title="Channel Scan Failed", description=f"Failed scanning {ch.mention}: {exc}")
                await ctx.send(embed=err_embed)

        # write to config
        month_key = f"{start.year}-{start.month:02d}"
        async with self.config.guild(ctx.guild).stats() as stats:
            stats[month_key] = counts

        done_embed = discord.Embed(title="Rebuild Complete", description=f"Rebuild complete for {month_key}. Counted messages for {len(counts)} users.")
        await ctx.send(embed=done_embed)
