from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger(__name__)

# Constants
DEFAULT_GUILD = {
    "channels": [],
    "announce_channel": 0,
    "stats": {},
    "current_override": "",
    "announce_everyone": False,
    "last_announce_month": ""
}

ANNOUNCEMENT_TIME_HOUR = 0
ANNOUNCEMENT_TIME_MINUTE = 5
BOT_READY_SLEEP_SECONDS = 1
REBUILD_PROGRESS_INTERVAL = 100  # Show progress every N messages
MAX_HISTORY_FETCH = 10000  # Limit per channel to prevent excessive API calls


def _month_key_for_dt(dt: Optional[datetime] = None) -> str:
    """Generate a month key string (YYYY-MM) from a datetime object."""
    dt = dt or datetime.now(timezone.utc)
    return f"{dt.year}-{dt.month:02d}"


class ChatterOfMonth(commands.Cog):
    """Track messages per-month in configured channels and announce the top chatter."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC0FFEE1, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self._announcer_task: Optional[asyncio.Task] = None
        self._stats_lock: dict[int, asyncio.Lock] = {}  # Per-guild locks for stats updates
        
        try:
            self._announcer_task = self.bot.loop.create_task(self._monthly_announcer_loop())
        except Exception:
            # bot loop may not be available in some environments; defer until cog_load
            self._announcer_task = None

    def _get_stats_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create a lock for a specific guild's stats."""
        if guild_id not in self._stats_lock:
            self._stats_lock[guild_id] = asyncio.Lock()
        return self._stats_lock[guild_id]

    # ---------- Event listener ------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Track messages in configured channels."""
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
        
        # Use per-guild lock to prevent race conditions
        async with self._get_stats_lock(guild.id):
            async with self.config.guild(guild).stats() as stats:
                month_stats = stats.get(month) or {}
                uid = str(message.author.id)
                month_stats[uid] = month_stats.get(uid, 0) + 1
                stats[month] = month_stats

    async def cog_unload(self) -> None:
        """Clean up tasks when cog is unloaded."""
        if self._announcer_task:
            self._announcer_task.cancel()
            try:
                await self._announcer_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.exception("Error during announcer task cleanup: %s", e)

    async def _monthly_announcer_loop(self) -> None:
        """Background task: wake at UTC midnight and announce previous month's winner on the 1st.

        This avoids announcing immediately after cog reload; announcements only occur
        when the clock passes into a new day (checked at 00:05 UTC) and only for
        the first calendar day of the month.
        """
        # wait for bot readiness
        if hasattr(self.bot, "wait_until_red_ready"):
            await self.bot.wait_until_red_ready()
        else:
            await asyncio.sleep(BOT_READY_SLEEP_SECONDS)

        while True:
            try:
                now = datetime.now(timezone.utc)
                # compute next UTC midnight plus small buffer
                next_midnight = (now + timedelta(days=1)).replace(
                    hour=ANNOUNCEMENT_TIME_HOUR,
                    minute=ANNOUNCEMENT_TIME_MINUTE,
                    second=0,
                    microsecond=0
                )
                sleep_seconds = (next_midnight - now).total_seconds()
                await asyncio.sleep(sleep_seconds)

                # after waking, if it's the first day of the month, announce previous month
                now = datetime.now(timezone.utc)
                if now.day != 1:
                    continue

                prev = (now.replace(day=1) - timedelta(days=1))
                prev_key = f"{prev.year}-{prev.month:02d}"

                for guild in list(self.bot.guilds):
                    try:
                        await self._announce_for_guild(guild, prev_key)
                    except Exception:
                        log.exception("Error during monthly announcement for guild %s", getattr(guild, "id", "?"))

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Monthly announcer loop raised an exception")

    async def _announce_for_guild(self, guild: discord.Guild, prev_key: str) -> None:
        """Handle monthly announcement for a single guild."""
        last = await self.config.guild(guild).last_announce_month()
        if last == prev_key:
            return

        stats = await self.config.guild(guild).stats()
        month_stats = stats.get(prev_key) or {}
        
        embed = discord.Embed(title=f"Chatter of {prev_key}", color=0x00FF00)
        
        if not month_stats:
            embed.description = "No data for this month."
        else:
            top_uid, top_count = max(month_stats.items(), key=lambda kv: kv[1])
            top_uid_int = int(top_uid)
            member = guild.get_member(top_uid_int)
            mention = member.mention if member else f"<@{top_uid_int}>"
            embed.add_field(name="Winner", value=f"{mention} — {top_count} messages", inline=False)
            
            sorted_top = sorted(month_stats.items(), key=lambda kv: kv[1], reverse=True)[:5]
            desc_lines = []
            for uid, cnt in sorted_top:
                uid_i = int(uid)
                m = guild.get_member(uid_i)
                desc_lines.append(f"{(m.mention if m else f'<@{uid_i}>')}: {cnt}")
            embed.add_field(name="Top 5", value="\n".join(desc_lines), inline=False)

        ann = await self.config.guild(guild).announce_channel()
        everyone = await self.config.guild(guild).announce_everyone()
        
        channel = None
        if ann:
            channel = guild.get_channel(ann)
        else:
            channel = guild.system_channel

        if channel:
            # Verify permissions
            if isinstance(channel, discord.TextChannel):
                perms = channel.permissions_for(guild.me)
                if not perms.send_messages or not perms.embed_links:
                    log.warning("Missing permissions in channel %s for guild %s", channel.id, guild.id)
                    return

            try:
                if everyone:
                    await channel.send(content="@everyone", embed=embed)
                else:
                    await channel.send(embed=embed)
            except discord.Forbidden:
                log.error("Forbidden: Cannot send message to channel %s in guild %s", channel.id, guild.id)
            except discord.HTTPException as e:
                log.exception("HTTP error sending announcement to channel %s in guild %s: %s", channel.id, guild.id, e)
            except Exception:
                log.exception("Failed to send monthly announce to channel %s in guild %s", channel.id, guild.id)

        # record announced month regardless of success
        await self.config.guild(guild).last_announce_month.set(prev_key)

    # ---------- Admin commands ------------------------------------------------
    @commands.group(name="chatter")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def chatter_group(self, ctx: commands.Context):
        """Manage chatter-of-the-month settings."""

    @chatter_group.group(name="channels")
    async def chatter_channels(self, ctx: commands.Context):
        """Manage which channels count toward monthly stats."""

    @chatter_channels.command(name="add")
    async def chatter_channels_add(self, ctx: commands.Context, channel: discord.TextChannel):
        """Add a channel to track for monthly chatter stats."""
        # Verify bot has read permissions
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.read_messages or not perms.read_message_history:
            await ctx.send(f"⚠️ I don't have permission to read messages in {channel.mention}. Please grant me `Read Messages` and `Read Message History` permissions.")
            return

        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id in channels:
                await ctx.send(f"{channel.mention} is already being tracked.")
                return
            channels.append(channel.id)
        
        embed = discord.Embed(
            title="Channel Added",
            description=f"Now tracking {channel.mention} for monthly chatter stats.",
            color=0x00FF00
        )
        await ctx.send(embed=embed)

    @chatter_channels.command(name="remove")
    async def chatter_channels_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from monthly chatter tracking."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id not in channels:
                await ctx.send(f"{channel.mention} is not being tracked.")
                return
            channels.remove(channel.id)
        
        embed = discord.Embed(
            title="Channel Removed",
            description=f"No longer tracking {channel.mention} for monthly chatter stats.",
            color=0xFF9900
        )
        await ctx.send(embed=embed)

    @chatter_channels.command(name="list")
    async def chatter_channels_list(self, ctx: commands.Context):
        """List all channels being tracked for monthly chatter stats."""
        channels = await self.config.guild(ctx.guild).channels()
        if not channels:
            await ctx.send("No channels are currently being tracked.")
            return
        
        embed = discord.Embed(title="Tracked Channels", color=0x0099FF)
        channel_mentions = []
        for cid in channels:
            ch = ctx.guild.get_channel(cid)
            if ch:
                channel_mentions.append(ch.mention)
            else:
                channel_mentions.append(f"Unknown channel (ID: {cid})")
        
        embed.description = "\n".join(channel_mentions)
        await ctx.send(embed=embed)

    @chatter_group.group(name="announce")
    async def chatter_announce(self, ctx: commands.Context):
        """Configure where and how monthly announcements are made."""

    @chatter_announce.command(name="channel")
    async def chatter_announce_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the announcement channel. Leave blank to use system channel."""
        if channel:
            # Verify permissions
            perms = channel.permissions_for(ctx.guild.me)
            if not perms.send_messages or not perms.embed_links:
                await ctx.send(f"⚠️ I don't have permission to send messages and embeds in {channel.mention}.")
                return
            
            await self.config.guild(ctx.guild).announce_channel.set(channel.id)
            embed = discord.Embed(
                title="Announcement Channel Set",
                description=f"Monthly announcements will be sent to {channel.mention}.",
                color=0x00FF00
            )
        else:
            await self.config.guild(ctx.guild).announce_channel.set(0)
            sys_ch = ctx.guild.system_channel
            fallback = f" (currently: {sys_ch.mention})" if sys_ch else " (no system channel set)"
            embed = discord.Embed(
                title="Announcement Channel Cleared",
                description=f"Monthly announcements will use the server's system channel{fallback}.",
                color=0xFF9900
            )
        
        await ctx.send(embed=embed)

    @chatter_announce.command(name="everyone")
    async def chatter_announce_everyone(self, ctx: commands.Context, enabled: bool):
        """Toggle @everyone mention in monthly announcements."""
        await self.config.guild(ctx.guild).announce_everyone.set(enabled)
        status = "enabled" if enabled else "disabled"
        embed = discord.Embed(
            title="@everyone Setting Updated",
            description=f"@everyone mentions in announcements are now **{status}**.",
            color=0x00FF00 if enabled else 0xFF9900
        )
        await ctx.send(embed=embed)

    @chatter_group.command(name="leader")
    async def chatter_leader(self, ctx: commands.Context, date: Optional[str] = None):
        """Show the current leader and top 5 for a given month.

        If no date is provided, uses the backdate override (if set) or current month.
        The command shows the top 5 and the current leader for the corresponding calendar month.
        """
        # resolve date: priority -> explicit arg -> guild override -> today
        if date:
            parsed = None
            try:
                parsed = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                try:
                    parsed = datetime.strptime(date, "%Y-%m")
                except ValueError:
                    await ctx.send("Invalid date format. Use `YYYY-MM-DD` or `YYYY-MM`.")
                    return
        else:
            override = await self.config.guild(ctx.guild).current_override()
            if override:
                try:
                    parsed = datetime.strptime(override, "%Y-%m-%d")
                except ValueError:
                    try:
                        parsed = datetime.strptime(override, "%Y-%m")
                    except ValueError:
                        parsed = datetime.now(timezone.utc)
            else:
                parsed = datetime.now(timezone.utc)

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
        
        embed = discord.Embed(
            title=f"Current leader — {month_key}",
            color=0xFFD700
        )
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
            except ValueError:
                # if YYYY-MM provided, normalize to first day of month
                parsed = datetime.strptime(date, "%Y-%m")
                parsed = parsed.replace(day=1)
        except ValueError:
            await ctx.send("Invalid date format. Use `YYYY-MM-DD` or `YYYY-MM`.")
            return
        
        normalized = parsed.strftime("%Y-%m-%d")
        await self.config.guild(ctx.guild).current_override.set(normalized)
        
        embed = discord.Embed(
            title="Backdate Set",
            description=f"Set leader display override to {normalized}.",
            color=0x00FF00
        )
        await ctx.send(embed=embed)

    @chatter_backdate.command(name="clear")
    async def chatter_backdate_clear(self, ctx: commands.Context):
        """Clear the backdate override."""
        await self.config.guild(ctx.guild).current_override.set("")
        
        embed = discord.Embed(
            title="Backdate Cleared",
            description="Cleared backdate override; `chatter leader` will use today's date.",
            color=0xFF9900
        )
        await ctx.send(embed=embed)

    @chatter_group.command(name="show")
    async def chatter_show(self, ctx: commands.Context):
        """Show current config and stats months available."""
        chs = await self.config.guild(ctx.guild).channels()
        ann = await self.config.guild(ctx.guild).announce_channel()
        override = await self.config.guild(ctx.guild).current_override()
        everyone = await self.config.guild(ctx.guild).announce_everyone()
        stats = await self.config.guild(ctx.guild).stats()
        
        embed = discord.Embed(title="Chatter Config & Stats", color=0x0099FF)
        
        channels_display = ', '.join(
            ctx.guild.get_channel(c).mention if ctx.guild.get_channel(c) else f"Unknown ({c})"
            for c in chs
        ) or 'None'
        
        ann_display = (
            ctx.guild.get_channel(ann).mention
            if ann and ctx.guild.get_channel(ann)
            else ('None' if not ann else f'Unknown ({ann})')
        )
        
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
            except ValueError:
                dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            await ctx.send("Invalid month format. Use `YYYY-MM` or `YYYY-MM-DD`.")
            return
        
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # compute first day of next month
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # determine channels to scan: only configured channels unless channels provided
        if channels:
            scan_channels = list(channels)
        else:
            cfg_ch = await self.config.guild(ctx.guild).channels()
            scan_channels = [ctx.guild.get_channel(cid) for cid in cfg_ch if ctx.guild.get_channel(cid)]

        if not scan_channels:
            embed = discord.Embed(
                title="No Configured Channels",
                description="No configured channels to scan. Add channels with `.chatter channels add <channel>` or provide channels to the command.",
                color=0xFF0000
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            title="Rebuild Started",
            description=f"Starting rebuild for {start.strftime('%Y-%m')} across {len(scan_channels)} channel(s). This may take some time.",
            color=0xFFAA00
        )
        status_msg = await ctx.send(embed=embed)
        
        counts: dict[str, int] = {}
        total_messages = 0
        
        for ch in scan_channels:
            try:
                channel_count = 0
                async for msg in ch.history(after=start, before=end, limit=MAX_HISTORY_FETCH):
                    if msg.author.bot:
                        continue
                    uid = str(msg.author.id)
                    counts[uid] = counts.get(uid, 0) + 1
                    channel_count += 1
                    total_messages += 1
                    
                    # Update progress periodically
                    if total_messages % REBUILD_PROGRESS_INTERVAL == 0:
                        try:
                            progress_embed = discord.Embed(
                                title="Rebuild In Progress",
                                description=f"Processed {total_messages} messages across {len(scan_channels)} channel(s)...",
                                color=0xFFAA00
                            )
                            await status_msg.edit(embed=progress_embed)
                        except discord.HTTPException:
                            pass  # Ignore rate limit errors on status updates
                
                log.info("Rebuilt %d messages from channel %s for month %s", channel_count, ch.id, month)
                
            except discord.Forbidden:
                log.error("Missing permissions to read history in channel %s", ch.id)
                err_embed = discord.Embed(
                    title="Channel Scan Failed",
                    description=f"Missing permissions to read history in {ch.mention}.",
                    color=0xFF0000
                )
                await ctx.send(embed=err_embed)
            except Exception as exc:
                log.exception("Failed scanning channel %s: %s", ch.id, exc)
                err_embed = discord.Embed(
                    title="Channel Scan Failed",
                    description=f"Failed scanning {ch.mention}: {exc}",
                    color=0xFF0000
                )
                await ctx.send(embed=err_embed)

        # write to config
        month_key = f"{start.year}-{start.month:02d}"
        async with self._get_stats_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).stats() as stats:
                stats[month_key] = counts

        done_embed = discord.Embed(
            title="Rebuild Complete",
            description=f"Rebuild complete for {month_key}.\n\n📊 **Total messages:** {total_messages}\n👥 **Unique users:** {len(counts)}",
            color=0x00FF00
        )
        await status_msg.edit(embed=done_embed)
