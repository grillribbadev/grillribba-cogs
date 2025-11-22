from __future__ import annotations
import asyncio
import json
import random
import re
import time
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from PIL import Image, ImageFilter, ImageOps
from redbot.core import Config
from redbot.core.bot import Red

from .constants import DEFAULT_GUILD, DEFAULT_USER

ONEPIECE_API = "https://onepiece.fandom.com/api.php"


def _now() -> int:
    return int(time.time())


class GuessEngine:
    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2025111801, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self.config.register_user(**DEFAULT_USER)

    # ---------------- active round helpers ----------------

    async def set_active(self, guild: discord.Guild, *, title: str, message: discord.Message) -> None:
        await self.config.guild(guild).active.set({
            "title": title,
            "posted_message_id": message.id,
            "posted_channel_id": message.channel.id,
            "started_at": _now(),
            "expired": False,
            "half_hint_sent": False,  # reset for new round
        })

    async def set_expired(self, guild: discord.Guild, value: bool) -> None:
        active = await self.config.guild(guild).active()
        active["expired"] = bool(value)
        await self.config.guild(guild).active.set(active)

    async def get_active(self, guild: discord.Guild) -> Dict:
        return await self.config.guild(guild).active()

    async def mark_half_hint_sent(self, guild: discord.Guild) -> None:
        active = await self.config.guild(guild).active()
        active["half_hint_sent"] = True
        await self.config.guild(guild).active.set(active)

    # ---------------- character pool & aliases ----------------

    async def list_characters(self, guild: discord.Guild) -> List[str]:
        return await self.config.guild(guild).characters()

    async def add_character(self, guild: discord.Guild, title: str) -> bool:
        items = await self.config.guild(guild).characters()
        if title in items:
            return False
        items.append(title)
        await self.config.guild(guild).characters.set(items)
        return True

    async def remove_character(self, guild: discord.Guild, title: str) -> bool:
        items = await self.config.guild(guild).characters()
        if title not in items:
            return False
        items.remove(title)
        await self.config.guild(guild).characters.set(items)
        # cleanup optional hint & aliases
        aliases = await self.config.guild(guild).aliases()
        aliases.pop(title, None)
        await self.config.guild(guild).aliases.set(aliases)
        hints = await self.config.guild(guild).hints()
        hints.pop(title, None)
        await self.config.guild(guild).hints.set(hints)
        return True

    async def upsert_aliases(self, guild: discord.Guild, title: str, aliases: List[str]) -> None:
        m = await self.config.guild(guild).aliases()
        m[str(title)] = list(dict.fromkeys([a for a in aliases if a]))  # dedup/preserve order
        await self.config.guild(guild).aliases.set(m)

    async def get_hint(self, guild: discord.Guild, title: str) -> Optional[str]:
        hints = await self.config.guild(guild).hints()
        return hints.get(title)

    async def pick_random_title(self, guild: discord.Guild) -> Optional[str]:
        items = await self.config.guild(guild).characters()
        return random.choice(items) if items else None

    # ---------------- answer checking ----------------

    async def check_guess(self, guild: discord.Guild, user_input: str) -> Tuple[bool, Optional[str]]:
        active = await self.get_active(guild)
        title = active.get("title")
        if not title or active.get("expired"):
            return False, title

        # build candidate keywords (title + aliases)
        aliases = await self.config.guild(guild).aliases()
        keys = [title] + aliases.get(title, [])
        normalized_guess = self._normalize(user_input)

        for key in keys:
            if self._is_match(normalized_guess, key):
                return True, title
        return False, title

    @staticmethod
    def _normalize(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def _is_match(self, guess: str, key: str) -> bool:
        k = self._normalize(key)
        if not k:
            return False
        # exact normalized
        if guess == k:
            return True
        # token containment or edit distance-ish containment
        # allow users to say "blackbeard" for "Marshall D. Teach" if alias covers it;
        # otherwise loose contains on tokens length>=3
        if k in guess or guess in k:
            return True
        g_tokens = set(t for t in guess.split() if len(t) >= 3)
        k_tokens = set(t for t in k.split() if len(t) >= 3)
        return bool(g_tokens & k_tokens)

    # ---------------- fandom API helpers ----------------

    async def fetch_page_brief(self, title: str) -> Tuple[str, str, Optional[str]]:
        """
        Return (normalized_title, extract_text, main_image_url)
        """
        params = {
            "action": "query",
            "prop": "extracts|pageimages",
            "exintro": "1",
            "explaintext": "1",
            "titles": title,
            "piprop": "original",
            "format": "json",
            "redirects": "1",
        }
        async with aiohttp.ClientSession() as s:
            async with s.get(ONEPIECE_API, params=params, timeout=15) as r:
                data = await r.json()
        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values())) if pages else {}
        normalized_title = page.get("title", title)
        extract = page.get("extract", "") or ""
        image_url = page.get("original", {}).get("source")
        return normalized_title, extract, image_url

    async def get_random_quote(self, title: str, forbidden: List[str]) -> Optional[str]:
        """
        Try to grab a random quote from the page's 'Quotes' section.
        Filters out quotes that mention the character's name or aliases.
        Returns a plain-text quote (no speaker prefix), or None.
        """
        # 1) find the section index for 'Quotes'
        params = {"action": "parse", "page": title, "prop": "sections", "format": "json"}
        async with aiohttp.ClientSession() as s:
            async with s.get(ONEPIECE_API, params=params, timeout=15) as r:
                data = await r.json()

        sections = data.get("parse", {}).get("sections", []) or []
        quotes_idx = None
        for sec in sections:
            if "quotes" in (sec.get("line") or "").lower():
                quotes_idx = sec.get("index")
                break
        if quotes_idx is None:
            return None

        # 2) fetch the section wikitext and extract bullet lines
        params = {
            "action": "parse",
            "page": title,
            "section": quotes_idx,
            "prop": "wikitext",
            "format": "json",
        }
        async with aiohttp.ClientSession() as s:
            async with s.get(ONEPIECE_API, params=params, timeout=15) as r:
                data = await r.json()

        wikitext = (data.get("parse", {}).get("wikitext", {}) or {}).get("*", "") or ""
        if not wikitext:
            return None

        # split by lines starting with * (wiki bullets)
        lines = [ln.strip() for ln in wikitext.splitlines() if ln.strip().startswith("*")]
        if not lines:
            return None

        # Convert simple wiki markup to plain text and filter
        candidates: List[str] = []
        for ln in lines:
            text = ln.lstrip("*").strip()
            text = self._strip_wikicode(text)
            # remove leading speaker "Name: " if present
            text = re.sub(r"^[^:]{1,40}:\s+", "", text).strip()
            if not text:
                continue
            low = text.lower()
            # skip if it includes the name/aliases tokens
            if any(tok in low for tok in forbidden):
                continue
            # prefer mid-length quotes
            candidates.append(text)

        if not candidates:
            return None
        return random.choice(candidates)

    @staticmethod
    def _strip_wikicode(s: str) -> str:
        # strip links [[A|B]] or [[A]]
        s = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", s)
        s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
        # strip italics/bold
        s = s.replace("'''''", "").replace("'''", "").replace("''", "")
        # strip templates {{...}}
        s = re.sub(r"\{\{[^}]+\}\}", "", s)
        # strip refs <ref>...</ref> and <ref .../>
        s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"<ref[^/]*/>", "", s, flags=re.IGNORECASE)
        # strip HTML tags
        s = re.sub(r"<[^>]+>", "", s)
        return s.strip()

    # ---------------- image processing ----------------

    async def make_blurred(self, image_url: str, *, mode: str = "gaussian", strength: int = 8, bw: bool = False) -> Optional[BytesIO]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(image_url, timeout=15) as r:
                    if r.status != 200:
                        return None
                    raw = await r.read()
        except Exception:
            return None

        try:
            im = Image.open(BytesIO(raw)).convert("RGBA")
        except Exception:
            return None

        if bw:
            im = ImageOps.grayscale(im).convert("RGBA")

        if mode == "pixelate":
            # downscale then upscale
            w, h = im.size
            block = max(1, min(250, int(strength)))
            im = im.resize((max(1, w // block), max(1, h // block)), Image.NEAREST).resize((w, h), Image.NEAREST)
        else:
            radius = max(1, min(250, int(strength)))
            im = im.filter(ImageFilter.GaussianBlur(radius=radius))

        buf = BytesIO()
        im.save(buf, "PNG")
        buf.seek(0)
        return buf

    # ---------------- local reward hook ----------------

    async def reward(self, member: discord.Member, amount: int) -> None:
        # placeholder for coins/other reward systems; no-op if amount == 0
        return
