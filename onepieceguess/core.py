from __future__ import annotations
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

    # ---------------- mode helpers ----------------

    async def get_mode(self, guild: discord.Guild) -> str:
        mode = await self.config.guild(guild).mode()
        mode = (mode or "character").lower()
        return "character" if mode not in {"character", "fruit", "ship"} else mode

    def _pool_keys(self, mode: str) -> Tuple[str, str, str]:
        """Return (titles_key, aliases_key, hints_key) for config based on mode."""
        if mode == "fruit":
            return "fruits", "fruit_aliases", "fruit_hints"
        if mode == "ship":
            return "ships", "ship_aliases", "ship_hints"
        # default: character
        return "characters", "aliases", "hints"

    async def get_aliases_map(self, guild: discord.Guild) -> Dict[str, List[str]]:
        mode = await self.get_mode(guild)
        _, aliases_key, _ = self._pool_keys(mode)
        return await getattr(self.config.guild(guild), aliases_key)()

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

    # ---------------- pool & aliases (mode-aware) ----------------

    async def list_entries(self, guild: discord.Guild) -> List[str]:
        mode = await self.get_mode(guild)
        titles_key, _, _ = self._pool_keys(mode)
        return await getattr(self.config.guild(guild), titles_key)()

    # Back-compat names used by the cog:
    async def list_characters(self, guild: discord.Guild) -> List[str]:
        return await self.list_entries(guild)

    async def add_entry(self, guild: discord.Guild, title: str) -> bool:
        mode = await self.get_mode(guild)
        titles_key, _, _ = self._pool_keys(mode)
        items = await getattr(self.config.guild(guild), titles_key)()
        if title in items:
            return False
        items.append(title)
        await getattr(self.config.guild(guild), titles_key).set(items)
        return True

    async def add_character(self, guild: discord.Guild, title: str) -> bool:
        return await self.add_entry(guild, title)

    async def remove_entry(self, guild: discord.Guild, title: str) -> bool:
        mode = await self.get_mode(guild)
        titles_key, aliases_key, hints_key = self._pool_keys(mode)
        items = await getattr(self.config.guild(guild), titles_key)()
        if title not in items:
            return False
        items.remove(title)
        await getattr(self.config.guild(guild), titles_key).set(items)
        # cleanup optional hint & aliases
        aliases = await getattr(self.config.guild(guild), aliases_key)()
        aliases.pop(title, None)
        await getattr(self.config.guild(guild), aliases_key).set(aliases)
        hints = await getattr(self.config.guild(guild), hints_key)()
        hints.pop(title, None)
        await getattr(self.config.guild(guild), hints_key).set(hints)
        return True

    async def remove_character(self, guild: discord.Guild, title: str) -> bool:
        return await self.remove_entry(guild, title)

    async def upsert_aliases(self, guild: discord.Guild, title: str, aliases: List[str]) -> None:
        mode = await self.get_mode(guild)
        _, aliases_key, _ = self._pool_keys(mode)
        m = await getattr(self.config.guild(guild), aliases_key)()
        m[str(title)] = list(dict.fromkeys([a for a in aliases if a]))  # dedup/preserve order
        await getattr(self.config.guild(guild), aliases_key).set(m)

    async def get_hint(self, guild: discord.Guild, title: str) -> Optional[str]:
        mode = await self.get_mode(guild)
        _, _, hints_key = self._pool_keys(mode)
        hints = await getattr(self.config.guild(guild), hints_key)()
        return hints.get(title)

    async def pick_random_title(self, guild: discord.Guild) -> Optional[str]:
        mode = await self.get_mode(guild)
        titles_key, _, _ = self._pool_keys(mode)
        items = await getattr(self.config.guild(guild), titles_key)()
        return random.choice(items) if items else None

    # ---------------- answer checking ----------------

    async def check_guess(self, guild: discord.Guild, user_input: str) -> Tuple[bool, Optional[str]]:
        active = await self.get_active(guild)
        # Treat no round OR expired round as "no active round"
        if not active or not active.get("title") or active.get("expired"):
            return False, None

        title = active["title"]

        # build candidate keywords (title + aliases) from current mode
        aliases_map = await self.get_aliases_map(guild)
        keys = [title] + aliases_map.get(title, [])
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
        # containment
        if k in guess or guess in k:
            return True
        # token overlap for >=3 chars (helps typos/aliases)
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
        Filters out quotes that mention the entry's name or aliases.
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