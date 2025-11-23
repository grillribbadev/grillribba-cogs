from __future__ import annotations
import time
import re
from typing import Optional
from io import BytesIO

import aiohttp
import discord
from discord.ext import tasks
from redbot.core.bot import Red

from .core import GuessEngine


class GuessTasks:
    """Background loop that posts rounds, sends mid-round hints, and handles timeouts."""

    def __init__(self, cog, engine: GuessEngine) -> None:
        self.cog = cog
        self.bot: Red = cog.bot
        self.engine = engine
        self._ticker.change_interval(seconds=5.0)  # cadence checker

    async def start(self) -> None:
        if not self._ticker.is_running():
            self._ticker.start()

    def cancel(self) -> None:
        if self._ticker.is_running():
            self._ticker.cancel()

    @staticmethod
    def _initials_from_title(title: str) -> str:
        """Build initials like 'M.D.L.' from 'Monkey D. Luffy'."""
        parts = re.split(r"[\s\-\_/()]+", title.strip())
        initials = []
        for p in parts:
            if not p:
                continue
            # single-letter token
            if len(p) == 1 and p.isalpha():
                initials.append(p.upper() + ".")
                continue
            for ch in p:
                if ch.isalpha():
                    initials.append(ch.upper() + ".")
                    break
        return "".join(initials)

    @tasks.loop(seconds=5.0)
    async def _ticker(self) -> None:
        now = int(time.time())
        for guild in list(self.bot.guilds):
            try:
                gconf = await self.engine.config.guild(guild).all()
                if not gconf.get("enabled"):
                    continue

                # Ensure channel exists
                channel_id = gconf.get("channel_id")
                channel: Optional[discord.TextChannel] = guild.get_channel(int(channel_id or 0))  # type: ignore
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue

                active = gconf.get("active") or {}
                title = active.get("title")
                expired = bool(active.get("expired"))
                started_at = int(active.get("started_at") or 0)
                interval = int(gconf.get("interval") or 1800)
                roundtime = int(gconf.get("roundtime") or 120)
                half_hint_sent = bool(active.get("half_hint_sent"))

                # 1) If nothing active or expired and the cadence says it's time, post a new round
                if (not title) or expired:
                    if not started_at or now - started_at >= interval:
                        try:
                            await self.cog._post_once(guild)
                        except Exception:
                            pass
                    continue  # skip hint/timeout checks for expired/just-posted

                # 2) A round is active — check timeout and mid-hint
                elapsed = now - started_at if started_at else 0

                # 2a) timeout
                if elapsed >= roundtime:
                    await self.engine.set_expired(guild, True)
                    # announce timeout under the original round message, with the unblurred image if possible
                    try:
                        posted_channel_id = int(active.get("posted_channel_id") or 0)
                        posted_message_id = int(active.get("posted_message_id") or 0)
                        if posted_channel_id and posted_message_id:
                            ch = guild.get_channel(posted_channel_id)
                            if isinstance(ch, discord.TextChannel):
                                try:
                                    msg = await ch.fetch_message(posted_message_id)
                                except discord.NotFound:
                                    msg = None

                                # Build embed + fetch original image
                                emb = discord.Embed(
                                    title="⏰ Time!",
                                    description=f"No one guessed **{title}**.",
                                    color=discord.Color.red(),
                                )

                                file = None
                                try:
                                    _t, _extract, image_url = await self.engine.fetch_page_brief(title)
                                    if image_url:
                                        async with aiohttp.ClientSession() as s:
                                            async with s.get(image_url, timeout=12) as r:
                                                if r.status == 200:
                                                    buf = BytesIO(await r.read())
                                                    buf.seek(0)
                                                    file = discord.File(buf, filename="opguess_timeout.png")
                                                    emb.set_image(url="attachment://opguess_timeout.png")
                                except Exception:
                                    file = None

                                if msg:
                                    await ch.send(
                                        embed=emb,
                                        file=file if file else discord.utils.MISSING,
                                        reference=msg,
                                        mention_author=False,
                                    )
                    except Exception:
                        pass
                    continue

                # 2b) mid-round hint at half time (post once)
                # NOTE: This is independent of the 'hint_enabled' toggle so it can't be silently blocked.
                if not half_hint_sent and elapsed >= roundtime / 2:
                    hint_text: Optional[str] = None

                    # Try a safe quote first (no name/aliases) — mode-aware aliases map
                    try:
                        aliases = await self.engine.get_aliases_map(guild)
                        forbidden_names = [title] + aliases.get(title, [])
                        toks = []
                        for n in forbidden_names:
                            n = (n or "").strip().lower()
                            if not n:
                                continue
                            toks.append(n)
                            toks.extend([p for p in n.replace(".", " ").split() if p])
                        quote = await self.engine.get_random_quote(title, toks)
                        if quote:
                            maxlen = int(gconf.get("hint_max_chars") or 200)
                            if len(quote) > maxlen:
                                quote = quote[:maxlen] + "…"
                            hint_text = f"💡 Hint (quote): {quote}"
                    except Exception:
                        hint_text = None

                    # Fallback: initials if no safe quote
                    if not hint_text:
                        initials = self._initials_from_title(title)
                        if initials:
                            hint_text = f"💡 Hint (initials): {initials}"

                    if hint_text:
                        posted_ok = False
                        # Prefer replying under the round message
                        try:
                            posted_channel_id = int(active.get("posted_channel_id") or 0)
                            posted_message_id = int(active.get("posted_message_id") or 0)
                            if posted_channel_id and posted_message_id:
                                ch = guild.get_channel(posted_channel_id)
                                if isinstance(ch, discord.TextChannel):
                                    try:
                                        msg = await ch.fetch_message(posted_message_id)
                                    except discord.NotFound:
                                        msg = None
                                    if msg:
                                        await ch.send(
                                            hint_text,
                                            reference=msg,
                                            mention_author=False,
                                        )
                                        posted_ok = True
                        except Exception:
                            posted_ok = False

                        # Fallback: plain send in channel
                        if not posted_ok:
                            try:
                                await channel.send(hint_text)
                            except Exception:
                                pass

                        # mark as sent (even if the send failed silently—we don't want spam)
                        try:
                            await self.engine.mark_half_hint_sent(guild)
                        except Exception:
                            pass

            except Exception:
                # never let one guild break the loop
                continue

    @_ticker.before_loop
    async def _before(self):
        await self.bot.wait_until_red_ready()