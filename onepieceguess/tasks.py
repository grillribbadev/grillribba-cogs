from __future__ import annotations
import time
from typing import Optional

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
        self._ticker.change_interval(seconds=5.0)

    async def start(self) -> None:
        if not self._ticker.is_running():
            self._ticker.start()

    def cancel(self) -> None:
        if self._ticker.is_running():
            self._ticker.cancel()

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

                # 1) If nothing active or expired and the cadence says it's time, post a new round
                if (not title) or expired:
                    # post only when cadence elapsed since last start (best-effort):
                    if not started_at or now - started_at >= interval:
                        try:
                            await self.cog._post_once(guild)
                        except Exception:
                            pass
                    continue  # don't process hint/timeout for expired or just-posted

                # 2) A round is active — check timeout and mid-hint
                elapsed = now - started_at if started_at else 0

                # 2a) timeout
                if elapsed >= roundtime:
                    # mark expired and announce timeout
                    await self.engine.set_expired(guild, True)
                    try:
                        # fetch the posted message to reply under it
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
                                        f"⏰ Time! No one guessed **{title}**.",
                                        reference=msg,
                                        mention_author=False,
                                    )
                    except Exception:
                        pass
                    continue

                # 2b) mid-round quote at half the time (once)
                if gconf.get("hint_enabled") and not active.get("half_hint_sent") and elapsed >= roundtime / 2:
                    # collect forbidden tokens (title + aliases) to avoid name reveal
                    aliases = await self.engine.config.guild(guild).aliases()
                    forbidden_names = [title] + aliases.get(title, [])
                    # normalize to lowercase tokens
                    toks = []
                    for n in forbidden_names:
                        n = (n or "").strip().lower()
                        if not n:
                            continue
                        toks.append(n)
                        toks.extend([p for p in n.replace(".", " ").split() if p])
                    try:
                        quote = await self.engine.get_random_quote(title, toks)
                    except Exception:
                        quote = None

                    if quote:
                        # clamp quote length: keep it readable
                        maxlen = int(gconf.get("hint_max_chars") or 200)
                        if len(quote) > maxlen:
                            quote = quote[:maxlen] + "…"

                        # reply under the original round message if we can
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
                                            f"💡 Hint (quote): {quote}",
                                            reference=msg,
                                            mention_author=False,
                                        )
                                        await self.engine.mark_half_hint_sent(guild)
                                        continue
                        except Exception:
                            pass

                        # fallback: post to the configured channel if we couldn't thread it
                        try:
                            await channel.send(f"💡 Hint (quote): {quote}")
                            await self.engine.mark_half_hint_sent(guild)
                        except Exception:
                            pass

            except Exception:
                # never let one guild break the loop
                continue

    @_ticker.before_loop
    async def _before(self):
        await self.bot.wait_until_red_ready()
