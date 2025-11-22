from __future__ import annotations
from discord.ext import tasks
import discord
import time
import aiohttp
from io import BytesIO

from redbot.core import commands

from .constants import COLOR_EMBED, INTERVAL_DEFAULT, ROUND_DEFAULT
from .core import GuessEngine


class GuessTasks:
    """Background loop that posts/ends rounds on schedule."""
    def __init__(self, cog: commands.Cog, engine: GuessEngine):
        self.cog = cog
        self.engine = engine

    async def start(self):
        if not self._tick.is_running():
            self._tick.start()

    def cancel(self):
        if self._tick.is_running():
            self._tick.cancel()

    @tasks.loop(seconds=60.0)
    async def _tick(self):
        bot = self.cog.bot
        for guild in list(bot.guilds):
            gconf = await self.engine.config.guild(guild).all()
            if not gconf.get("enabled"):
                continue

            channel_id = gconf.get("channel_id")
            interval   = int(gconf.get("interval") or INTERVAL_DEFAULT)   # posting cadence
            roundtime  = int(gconf.get("roundtime") or ROUND_DEFAULT)     # per-round timeout
            if not channel_id or interval <= 0 or roundtime <= 0:
                continue

            active = gconf.get("active") or {}
            title = active.get("title")
            started_at = int(active.get("started_at") or 0)
            expired = bool(active.get("expired"))
            now = int(time.time())

            # 1) If a round is active and not yet expired, enforce per-round timeout
            if title and not expired:
                if now - started_at >= roundtime:
                    # Time's up: announce & lock until the cadence (interval) elapses
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        ctitle, _extract, img = await self.engine.fetch_page_brief(title)
                        reveal = discord.Embed(
                            title="⏰ Time's up!",
                            description=f"No one guessed the correct character.\n**Answer:** {ctitle or title}",
                            color=discord.Color.orange()
                        )
                        # Optional: attach unblurred image on reveal
                        if img:
                            try:
                                async with aiohttp.ClientSession() as s:
                                    async with s.get(img, timeout=12) as r:
                                        if r.status == 200:
                                            buf = BytesIO(await r.read()); buf.seek(0)
                                            file = discord.File(buf, filename="opguess_reveal.png")
                                            reveal.set_image(url="attachment://opguess_reveal.png")
                                            await channel.send(embed=reveal, file=file)
                                        else:
                                            await channel.send(embed=reveal)
                            except Exception:
                                try:
                                    await channel.send(embed=reveal)
                                except Exception:
                                    pass
                        else:
                            try:
                                await channel.send(embed=reveal)
                            except Exception:
                                pass
                    await self.engine.set_expired(guild, True)

                # Still within cadence? then do nothing else this tick
                if now - started_at < interval:
                    continue

            # 2) Cadence gate ALWAYS (even if expired=True).
            #    If a round existed, wait until the interval has fully elapsed since it started.
            if title and (now - started_at) < interval:
                continue

            # 3) Post a new round
            channel = guild.get_channel(int(channel_id))
            if not channel:
                continue

            new_title = await self.engine.pick_random_title(guild)
            if not new_title:
                continue

            ctitle, extract, image_url = await self.engine.fetch_page_brief(new_title)
            display_title = ctitle or new_title

            emb = discord.Embed(
                title="🗺️ Guess the One Piece Character!",
                description="Reply with `.guess <name>` (prefix) or `/guess` if enabled.\n"
                            "Typos and common aliases are accepted.",
                color=COLOR_EMBED
            )
            emb.set_footer(text=f"Timer: {interval}s • Round timeout: {roundtime}s • Title seeded from: {display_title}")

            # hint text
            if gconf.get("hint_enabled") and extract:
                maxn = int(gconf.get("hint_max_chars") or 200)
                val = extract if len(extract) <= maxn else (extract[:maxn] + "…")
                emb.add_field(name="Hint", value=val, inline=False)

            # blurred image
            file = None
            if image_url:
                blur = gconf.get("blur") or {}
                mode = str(blur.get("mode") or "gaussian").lower()
                strength = int(blur.get("strength") or 8)
                buf = await self.engine.make_blurred(image_url, mode=mode, strength=strength)
                if buf:
                    file = discord.File(buf, filename="opguess_blur.png")
                    emb.set_image(url="attachment://opguess_blur.png")

            try:
                message = await channel.send(embed=emb, file=file) if file else await channel.send(embed=emb)
            except Exception:
                continue

            await self.engine.set_active(guild, title=new_title, message=message)
            await self.engine.set_expired(guild, False)

    @_tick.before_loop
    async def _before_tick(self):
        await self.cog.bot.wait_until_red_ready()
