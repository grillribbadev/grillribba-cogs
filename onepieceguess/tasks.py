from __future__ import annotations
from discord.ext import tasks
import discord
import time

from redbot.core import commands

from .constants import COLOR_EMBED
from .core import GuessEngine

class GuessTasks:
    def __init__(self, cog: commands.Cog, engine: GuessEngine):
        self.cog = cog
        self.engine = engine
        self.loop = tasks.Loop(self._tick, seconds=60.0)

    async def start(self):
        if not self.loop.is_running():
            self.loop.start()

    def cancel(self):
        if self.loop.is_running():
            self.loop.cancel()

    async def _tick(self):
        bot = self.cog.bot
        for guild in list(bot.guilds):
            gconf = await self.engine.config.guild(guild).all()
            if not gconf.get("enabled"):
                continue
            channel_id = gconf.get("channel_id")
            interval = int(gconf.get("interval") or 0)
            if not channel_id or interval <= 0:
                continue

            active = gconf.get("active") or {}
            last_start = int(active.get("started_at") or 0)
            now = int(time.time())
            if active.get("title") and (now - last_start) < interval:
                continue

            channel = guild.get_channel(int(channel_id))
            if not channel:
                continue

            title = await self.engine.pick_random_title(guild)
            if not title:
                continue

            ctitle, extract, image_url = await self.engine.fetch_page_brief(title)
            display_title = ctitle or title

            # Build embed
            emb = discord.Embed(
                title="🗺️ Guess the One Piece Character!",
                description="Reply with `.guess <name>` (prefix) or `/guess` if enabled.\n"
                            "Typos and common aliases are accepted.",
                color=COLOR_EMBED
            )
            emb.set_footer(text=f"Timer: {interval}s • Title seeded from: {display_title}")

            # Text hint?
            if gconf.get("hint_enabled"):
                if extract:
                    maxn = int(gconf.get("hint_max_chars") or 200)
                    val = extract if len(extract) <= maxn else (extract[:maxn] + "…")
                    emb.add_field(name="Hint", value=val, inline=False)

            # Blurred image
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

            await self.engine.set_active(guild, title=title, message=message)
