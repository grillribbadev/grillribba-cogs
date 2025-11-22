from __future__ import annotations
import aiohttp
import asyncio
import random
import time
from typing import Any, Dict, List, Optional, Tuple
from io import BytesIO

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

from PIL import Image, ImageFilter

from .constants import DEFAULT_GUILD, DEFAULT_USER
from .matching import is_guess_match

FANDOM_API = "https://onepiece.fandom.com/api.php"

class GuessEngine:
    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x0NEP1ECE, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self.config.register_user(**DEFAULT_USER)

    # ---------- fandom ----------
    async def fetch_page_brief(self, title: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts|pageimages",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
            "piprop": "original"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(FANDOM_API, params=params, timeout=15) as resp:
                if resp.status != 200:
                    return None, None, None
                data = await resp.json()
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return None, None, None
        page = next(iter(pages.values()))
        ctitle = page.get("title")
        extract = page.get("extract")
        image_url = page.get("original", {}).get("source") if page.get("original") else None
        return ctitle, extract, image_url

    # ---------- image helpers ----------
    async def _download_image(self, url: str) -> Optional[bytes]:
        if not url:
            return None
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()

    @staticmethod
    def _blur_gaussian(im: Image.Image, radius: int) -> Image.Image:
        radius = max(1, min(64, int(radius)))
        return im.filter(ImageFilter.GaussianBlur(radius=radius))

    @staticmethod
    def _blur_pixelate(im: Image.Image, block: int) -> Image.Image:
        block = max(4, min(64, int(block)))
        w, h = im.size
        # downscale then upscale to pixelate
        im_small = im.resize((max(1, w // block), max(1, h // block)), Image.NEAREST)
        return im_small.resize((w, h), Image.NEAREST)

    async def make_blurred(self, image_url: str, *, mode: str, strength: int) -> Optional[BytesIO]:
        data = await self._download_image(image_url)
        if not data:
            return None
        try:
            im = Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            return None
        if mode == "pixelate":
            out = self._blur_pixelate(im, strength)
        else:
            out = self._blur_gaussian(im, strength)
        buf = BytesIO()
        out.save(buf, format="PNG")
        buf.seek(0)
        return buf

    # ---------- characters & aliases ----------
    async def list_characters(self, guild: discord.Guild) -> List[str]:
        return list(await self.config.guild(guild).characters())

    async def add_character(self, guild: discord.Guild, title: str) -> bool:
        chars = await self.config.guild(guild).characters()
        if title in chars:
            return False
        chars.append(title)
        await self.config.guild(guild).characters.set(chars)
        return True

    async def remove_character(self, guild: discord.Guild, title: str) -> bool:
        chars = await self.config.guild(guild).characters()
        if title not in chars:
            return False
        chars = [c for c in chars if c != title]
        await self.config.guild(guild).characters.set(chars)
        aliases = await self.config.guild(guild).aliases()
        if title in aliases:
            del aliases[title]
            await self.config.guild(guild).aliases.set(aliases)
        active = await self.config.guild(guild).active()
        if (active or {}).get("title") == title:
            await self.clear_active(guild)
        return True

    async def upsert_aliases(self, guild: discord.Guild, title: str, alias_list: List[str]) -> None:
        data = await self.config.guild(guild).aliases()
        data[str(title)] = [a for a in alias_list if a.strip()]
        await self.config.guild(guild).aliases.set(data)

    async def get_aliases(self, guild: discord.Guild, title: str) -> List[str]:
        return (await self.config.guild(guild).aliases()).get(str(title), [])

    # ---------- active ----------
    async def pick_random_title(self, guild: discord.Guild) -> Optional[str]:
        chars = await self.config.guild(guild).characters()
        if not chars:
            return None
        return random.choice(chars)

    async def set_active(self, guild: discord.Guild, *, title: str, message: discord.Message) -> None:
        await self.config.guild(guild).active.set({
            "title": title,
            "posted_message_id": message.id,
            "posted_channel_id": message.channel.id,
            "started_at": int(time.time())
        })

    async def get_active(self, guild: discord.Guild) -> Dict[str, Any]:
        return await self.config.guild(guild).active()

    async def clear_active(self, guild: discord.Guild) -> None:
        await self.config.guild(guild).active.set({"title": None, "posted_message_id": None, "posted_channel_id": None, "started_at": 0})

    # ---------- reward ----------
    def _core(self):
        return self.bot.get_cog("BeriCore")

    async def reward(self, member: discord.Member, amount: int, *, reason: str = "opguess:win") -> None:
        core = self._core()
        if not core or amount <= 0:
            return
        try:
            await core.add_beri(member, amount, reason=reason, actor=member, bypass_cap=True)
        except Exception:
            pass

    # ---------- matching ----------
    async def check_guess(self, guild: discord.Guild, user_guess: str) -> Tuple[bool, Optional[str]]:
        active = await self.get_active(guild)
        title = active.get("title")
        if not title:
            return False, None
        aliases = await self.get_aliases(guild, title)
        ok = is_guess_match(user_guess, title, aliases)
        return ok, title
