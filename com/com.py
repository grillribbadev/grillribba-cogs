from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

log = logging.getLogger(__name__)


DEFAULT_GUILD = {"channels": [], "announce_channel": 0, "stats": {}, "current_override": "", "announce_everyone": False, "last_announce_month": ""}
LEADERBOARD_PAGE_SIZE = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month_key_for_dt(dt: Optional[datetime] = None) -> str:
    dt = dt or _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return f"{dt.year}-{dt.month:02d}"


class ChatterLeaderPaginationView(discord.ui.View):
    def __init__(
        self,
        cog: "ChatterOfMonth",
        ctx: commands.Context,
        month_key: str,
        leader_mention: str,
        top_count: int,
        sorted_top: list[tuple[str, int]],
        total_pages: int,
        uncapped_total_pages: int,
        current_page: int,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.month_key = month_key
        self.leader_mention = leader_mention
        self.top_count = top_count
        self.sorted_top = sorted_top
        self.total_pages = total_pages
        self.uncapped_total_pages = uncapped_total_pages
        self.current_page = current_page
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_btn.disabled = self.current_page <= 1
        self.next_btn.disabled = self.current_page >= self.total_pages

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command author can use these buttons.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.primary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1:
            self.current_page -= 1
        self._sync_buttons()
        embed = self.cog._build_leaderboard_embed(
            guild=self.ctx.guild,
            month_key=self.month_key,
            leader_mention=self.leader_mention,
            top_count=self.top_count,
            sorted_top=self.sorted_top,
            page=self.current_page,
            total_pages=self.total_pages,
            uncapped_total_pages=self.uncapped_total_pages,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
        self._sync_buttons()
        embed = self.cog._build_leaderboard_embed(
            guild=self.ctx.guild,
            month_key=self.month_key,
            leader_mention=self.leader_mention,
            top_count=self.top_count,
            sorted_top=self.sorted_top,
            page=self.current_page,
            total_pages=self.total_pages,
            uncapped_total_pages=self.uncapped_total_pages,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)


class ChatterOfMonth(commands.Cog):
    """Track messages per-month in configured channels and announce the top chatter."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC0FFEE1, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self._announcer_task: Optional[asyncio.Task] = None
        try:
            self._announcer_task = self.bot.loop.create_task(self._monthly_announcer_loop())
        except Exception:
            # bot loop may not be available in some environments; defer until cog_load
            self._announcer_task = None

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

    async def cog_unload(self) -> None:
        if self._announcer_task:
            self._announcer_task.cancel()
            try:
                await self._announcer_task
            except Exception:
                pass

    async def _monthly_announcer_loop(self) -> None:
        """Background task: wake at UTC midnight and announce previous month's winner on the 1st.

        This avoids announcing immediately after cog reload; announcements only occur
        when the clock passes into a new day (checked at 00:05 UTC) and only for
        the first calendar day of the month.
        """
        # wait for bot readiness/supporting method
        if hasattr(self.bot, "wait_until_red_ready"):
            await self.bot.wait_until_red_ready()
        else:
            await asyncio.sleep(1)

        while True:
            try:
                now = _utc_now()
                # compute next UTC midnight plus small buffer (00:05)
                next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
                sleep_seconds = (next_midnight - now).total_seconds()
                await asyncio.sleep(sleep_seconds)

                # after waking, if it's the first day of the month, announce previous month
                now = _utc_now()
                if now.day != 1:
                    continue

                prev = (now.replace(day=1) - timedelta(days=1))
                prev_key = f"{prev.year}-{prev.month:02d}"

                for guild in list(self.bot.guilds):
                    try:
                        last = await self.config.guild(guild).last_announce_month()
                        if last == prev_key:
                            continue

                        stats = await self.config.guild(guild).stats()
                        month_stats = stats.get(prev_key) or {}
                        embed = self._build_month_announcement_embed(guild=guild, month_key=prev_key, month_stats=month_stats)

                        ann = await self.config.guild(guild).announce_channel()
                        everyone = await self.config.guild(guild).announce_everyone()
                        if ann:
                            ch = guild.get_channel(ann)
                            if ch:
                                try:
                                    if everyone:
                                        await ch.send(content="@everyone", embed=embed)
                                    else:
                                        await ch.send(embed=embed)
                                except Exception:
                                    log.exception("Failed to send monthly announce to channel %s in guild %s", ann, guild.id)
                        else:
                            ch = guild.system_channel
                            if ch:
                                perms = ch.permissions_for(guild.me)
                                if perms.send_messages:
                                    try:
                                        if everyone:
                                            await ch.send(content="@everyone", embed=embed)
                                        else:
                                            await ch.send(embed=embed)
                                    except Exception:
                                        log.exception("Failed to send monthly announce to system_channel in guild %s", guild.id)

                        # record announced month regardless of success
                        await self.config.guild(guild).last_announce_month.set(prev_key)
                    except Exception:
                        log.exception("Error during monthly announcement for guild %s", getattr(guild, "id", "?"))

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Monthly announcer loop raised an exception")

    def _build_month_announcement_embed(self, guild: discord.Guild, month_key: str, month_stats: dict[str, int]) -> discord.Embed:
        guild_icon = guild.icon.url if guild.icon else None
        embed = discord.Embed(
            title=f"Chatter of {month_key}",
            description="Final results from tracked channels.",
            color=discord.Color.gold(),
            timestamp=_utc_now(),
        )
        embed.set_author(name=guild.name, icon_url=guild_icon)
        if not month_stats:
            embed.description = "No tracked messages were recorded for this month."
            embed.set_footer(text="Channels can be managed with chatter channels commands.")
            return embed

        top_uid, top_count = max(month_stats.items(), key=lambda kv: kv[1])
        top_uid_int = int(top_uid)
        member = guild.get_member(top_uid_int)
        mention = member.mention if member else f"<@{top_uid_int}>"
        if member is not None:
            embed.set_thumbnail(url=member.display_avatar.url)

        total_messages = sum(month_stats.values())
        participant_count = len(month_stats)

        embed.add_field(name="Winner", value=f"{mention}\n{top_count:,} messages", inline=False)
        embed.add_field(name="Tracked Messages", value=f"{total_messages:,}", inline=True)
        embed.add_field(name="Active Chatters", value=f"{participant_count:,}", inline=True)

        sorted_top = sorted(month_stats.items(), key=lambda kv: kv[1], reverse=True)[:5]
        lines = []
        for i, (uid, cnt) in enumerate(sorted_top, start=1):
            uid_i = int(uid)
            m = guild.get_member(uid_i)
            rank_label = f"#{i}"
            lines.append(f"{rank_label} {(m.mention if m else f'<@{uid_i}>')} - {cnt:,}")
        embed.add_field(name="Top 5", value="\n".join(lines), inline=False)
        embed.set_footer(text="Only configured tracking channels are counted.")
        return embed

    def _build_leaderboard_embed(
        self,
        guild: discord.Guild,
        month_key: str,
        leader_mention: str,
        top_count: int,
        sorted_top: list[tuple[str, int]],
        page: int,
        total_pages: int,
        uncapped_total_pages: int,
    ) -> discord.Embed:
        start_index = (page - 1) * LEADERBOARD_PAGE_SIZE
        page_slice = sorted_top[start_index:start_index + LEADERBOARD_PAGE_SIZE]
        guild_icon = guild.icon.url if guild.icon else None
        total_entries = len(sorted_top)
        shown_start = start_index + 1
        shown_end = min(start_index + LEADERBOARD_PAGE_SIZE, total_entries)

        embed = discord.Embed(
            title=f"Live Leaderboard - {month_key}",
            description=f"Showing ranks {shown_start}-{shown_end} of {total_entries:,}",
            color=discord.Color.blurple(),
            timestamp=_utc_now(),
        )
        embed.set_author(name=guild.name, icon_url=guild_icon)
        embed.add_field(name="Current Leader", value=f"{leader_mention} - {top_count:,} messages", inline=False)
        desc_lines = []
        for offset, (uid, cnt) in enumerate(page_slice, start=start_index + 1):
            uid_i = int(uid)
            m = guild.get_member(uid_i)
            desc_lines.append(f"#{offset} {(m.mention if m else f'<@{uid_i}>')} - {cnt:,}")
        embed.add_field(name=f"Leaderboard (Page {page}/{total_pages})", value="\n".join(desc_lines), inline=False)
        embed.set_footer(text="Use Prev/Next to browse pages. Close removes the buttons.")
        return embed

    # ---------- Admin commands ------------------------------------------------
    @commands.group(name="chatter", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def chatter_group(self, ctx: commands.Context):
        """Manage chatter-of-the-month settings."""
        if ctx.invoked_subcommand is not None:
            return

        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Chatter Control Panel",
            description="Track monthly activity, preview results, and manage announcement behavior.",
            color=discord.Color.blurple(),
            timestamp=_utc_now(),
        )
        if ctx.guild and ctx.guild.icon:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)

        embed.add_field(
            name="Setup",
            value=(
                f"{prefix}chatter channels add #channel\n"
                f"{prefix}chatter announce set #channel\n"
                f"{prefix}chatter show"
            ),
            inline=False,
        )
        embed.add_field(
            name="Monthly Results",
            value=(
                f"{prefix}chatter results YYYY-MM\n"
                f"{prefix}chatter winner YYYY-MM\n"
                f"{prefix}chatter leader"
            ),
            inline=False,
        )
        embed.add_field(
            name="Management",
            value=(
                f"{prefix}chatter channels list\n"
                f"{prefix}chatter backdate set YYYY-MM-DD\n"
                f"{prefix}chatter rebuild YYYY-MM"
            ),
            inline=False,
        )
        embed.set_footer(text="Tip: Use chatter leader to open the interactive Prev/Next leaderboard.")
        await ctx.send(embed=embed)

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
                    now = _utc_now()
                    first = now.replace(day=1)
                    prev = first - timedelta(days=1)
                    month = _month_key_for_dt(prev)
            else:
                now = _utc_now()
                first = now.replace(day=1)
                prev = first - timedelta(days=1)
                month = _month_key_for_dt(prev)
        else:
            try:
                parsed_month = datetime.strptime(month, "%Y-%m")
                month = f"{parsed_month.year}-{parsed_month.month:02d}"
            except Exception:
                await ctx.send("Invalid month format. Use `YYYY-MM`.")
                return

        now = _utc_now()
        current_month_key = _month_key_for_dt(now)
        first_of_current = now.replace(day=1)
        prev_month_key = _month_key_for_dt(first_of_current - timedelta(days=1))

        stats = await self.config.guild(ctx.guild).stats()
        month_stats = stats.get(month) or {}

        # Never finalize or announce current/future months.
        if month >= current_month_key:
            if not month_stats:
                embed = discord.Embed(
                    title="No Data",
                    description=f"No tracked messages found for {month}.",
                    color=discord.Color.orange(),
                )
            else:
                embed = self._build_month_announcement_embed(guild=ctx.guild, month_key=month, month_stats=month_stats)

            status_text = (
                "This month is still in progress, so winner announcements are not final yet."
                if month == current_month_key
                else "That month has not started yet, so a winner cannot be announced."
            )
            embed.add_field(name="Status", value=status_text, inline=False)
            embed.set_footer(text="Preview only. Use chatter results for non-final months.")
            await ctx.send(embed=embed)
            return

        if not month_stats:
            embed = discord.Embed(
                title="No Data",
                description=f"No tracked messages found for {month}.",
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)
            return
        embed = self._build_month_announcement_embed(guild=ctx.guild, month_key=month, month_stats=month_stats)
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

                # If previous month was manually announced, avoid duplicate auto-post on the 1st.
                if month == prev_month_key:
                    await self.config.guild(ctx.guild).last_announce_month.set(month)

                done_embed = discord.Embed(
                    title="Announcement Sent",
                    description=f"Posted the {month} winner announcement in {ch.mention}.",
                    color=discord.Color.green(),
                )
                await ctx.send(embed=done_embed)
                return
        # fallback to command channel
        if everyone:
            await ctx.send(content="@everyone", embed=embed)
        else:
            await ctx.send(embed=embed)

        if month == prev_month_key:
            await self.config.guild(ctx.guild).last_announce_month.set(month)

    @chatter_group.command(name="results")
    async def chatter_results(self, ctx: commands.Context, month: str):
        """Preview monthly results without posting an announcement.

        `month` must be `YYYY-MM`.
        """
        try:
            parsed = datetime.strptime(month, "%Y-%m")
        except Exception:
            await ctx.send("Invalid month format. Use `YYYY-MM`.")
            return

        month_key = f"{parsed.year}-{parsed.month:02d}"
        is_current_month = month_key == _month_key_for_dt()
        stats = await self.config.guild(ctx.guild).stats()
        month_stats = stats.get(month_key) or {}
        if not month_stats:
            embed = discord.Embed(
                title="No Data",
                description=f"No tracked messages found for {month_key}.",
                color=discord.Color.orange(),
            )
            if is_current_month:
                embed.add_field(
                    name="Status",
                    value="This month is still in progress, so results are not final yet.",
                    inline=False,
                )
            await ctx.send(embed=embed)
            return

        embed = self._build_month_announcement_embed(guild=ctx.guild, month_key=month_key, month_stats=month_stats)
        if is_current_month:
            embed.add_field(
                name="Status",
                value="This month is still in progress, so results are not final yet.",
                inline=False,
            )
            embed.set_footer(text="Preview only. Ongoing month results may change.")
        else:
            embed.set_footer(text="Preview only. This command does not announce the winner.")
        await ctx.send(embed=embed)

    @chatter_group.command(name="leader")
    async def chatter_leader(self, ctx: commands.Context, date_or_page: Optional[str] = None, page: Optional[int] = None):
        """Show who's currently in lead for a month or a specific date.

        `date_or_page` accepts `YYYY-MM-DD`, `YYYY-MM`, or a page number.
        `page` defaults to 1 and shows
        5 users per page. If omitted and an override is set
        via `chatter backdate set`, that date will be used; otherwise today's date is used.
        The command shows the current leader and a paged leaderboard for the
        corresponding calendar month.
        """
        date: Optional[str] = None
        if page is None:
            page = 1

        if date_or_page:
            if date_or_page.isdigit():
                page = int(date_or_page)
            else:
                date = date_or_page

        if page < 1:
            await ctx.send("Page must be 1 or greater.")
            return

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
                        parsed = _utc_now()
            else:
                    parsed = _utc_now()

        month_key = f"{parsed.year}-{parsed.month:02d}"
        stats = await self.config.guild(ctx.guild).stats()
        month_stats = stats.get(month_key) or {}
        if not month_stats:
            embed = discord.Embed(
                title="No Data",
                description=f"No tracked messages found for {month_key}.",
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)
            return

        # compute top and paged leaderboard
        top_uid, top_count = max(month_stats.items(), key=lambda kv: kv[1])
        top_uid_int = int(top_uid)
        member = ctx.guild.get_member(top_uid_int)
        mention = member.mention if member else f"<@{top_uid_int}>"
        sorted_top = sorted(month_stats.items(), key=lambda kv: kv[1], reverse=True)
        total_entries = len(sorted_top)
        uncapped_total_pages = max(1, (total_entries + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE)
        total_pages = uncapped_total_pages
        if page > total_pages:
            await ctx.send(
                f"Page {page} does not exist. There {'is' if total_pages == 1 else 'are'} "
                f"{total_pages} page{'s' if total_pages != 1 else ''}."
            )
            return

        embed = self._build_leaderboard_embed(
            guild=ctx.guild,
            month_key=month_key,
            leader_mention=mention,
            top_count=top_count,
            sorted_top=sorted_top,
            page=page,
            total_pages=total_pages,
            uncapped_total_pages=uncapped_total_pages,
        )
        if total_pages <= 1:
            await ctx.send(embed=embed)
            return

        view = ChatterLeaderPaginationView(
            cog=self,
            ctx=ctx,
            month_key=month_key,
            leader_mention=mention,
            top_count=top_count,
            sorted_top=sorted_top,
            total_pages=total_pages,
            uncapped_total_pages=uncapped_total_pages,
            current_page=page,
        )
        sent = await ctx.send(embed=embed, view=view)
        view.message = sent

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
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        # compute first day of next month
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1, day=1)
        else:
            next_month = start.replace(month=start.month + 1, day=1)
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
                description="No configured channels to scan. Add channels with `.chatter channels add <channel>` or provide channels to the command."
            )
            await ctx.send(embed=embed)
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
