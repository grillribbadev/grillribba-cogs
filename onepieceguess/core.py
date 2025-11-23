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

    # ---------------- migrations ----------------

    async def ensure_mode_migrated(self, guild: discord.Guild) -> None:
        """Seed per-mode blur from legacy blur; ensure current_mode & half_hint_sent exist."""
        g = await self.config.guild(guild).all()
        if not g.get("blur_by_mode"):
            legacy = g.get("blur") or {"mode": "gaussian", "strength": 8, "bw": False}
            await self.config.guild(guild).blur_by_mode.set({
                "characters": legacy,
                "devilfruits": {"mode": "gaussian", "strength": 1, "bw": False},
                "ships": {"mode": "gaussian", "strength": legacy.get("strength", 8), "bw": legacy.get("bw", False)}
            })
        if not g.get("current_mode"):
            await self.config.guild(guild).current_mode.set("characters")
        act = g.get("active") or {}
        if "half_hint_sent" not in act:
            act["half_hint_sent"] = False
            await self.config.guild(guild).active.set(act)

    async def ensure_pool_migrated(self, guild: discord.Guild) -> None:
        """Move legacy `characters` into characters_by_mode['characters'] if empty."""
        g = await self.config.guild(guild).all()
        by = g.get("characters_by_mode") or {}
        if not by:
            by = {"characters": [], "devilfruits": [], "ships": []}
        legacy = g.get("characters") or []
        if legacy and not by.get("characters"):
            by["characters"] = list(legacy)
        await self.config.guild(guild).characters_by_mode.set(by)

    # ---------------- active round helpers ----------------

    async def set_active(self, guild: discord.Guild, *, title: str, message: discord.Message) -> None:
        await self.config.guild(guild).active.set({
            "title": title,
            "posted_message_id": message.id,
            "posted_channel_id": message.channel.id,
            "started_at": _now(),
            "expired": False,
            "half_hint_sent": False,
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

    # ---------------- mode & blur helpers ----------------

    async def get_current_mode(self, guild: discord.Guild) -> str:
        return await self.config.guild(guild).current_mode()

    async def set_current_mode(self, guild: discord.Guild, mode: str) -> None:
        await self.config.guild(guild).current_mode.set(mode)

    async def get_blur_for_mode(self, guild: discord.Guild) -> Dict[str, object]:
        mode = await self.get_current_mode(guild)
        bm = await self.config.guild(guild).blur_by_mode()
        prof = bm.get(mode) or bm.get("characters") or {"mode": "gaussian", "strength": 8, "bw": False}
        prof = {
            "mode": str(prof.get("mode") or "gaussian").lower(),
            "strength": max(1, min(250, int(prof.get("strength") or 8))),
            "bw": bool(prof.get("bw")),
        }
        return prof

    async def update_blur_for_mode(self, guild: discord.Guild, **entries) -> Dict[str, object]:
        mode = await self.get_current_mode(guild)
        bm = await self.config.guild(guild).blur_by_mode()
        cur = dict(bm.get(mode, {}))
        for k, v in entries.items():
            cur[k] = v
        bm[mode] = cur
        await self.config.guild(guild).blur_by_mode.set(bm)
        return cur

    # ---------------- per-mode pools ----------------

    async def _get_pool_for_current_mode(self, guild: discord.Guild) -> List[str]:
        mode = (await self.config.guild(guild).current_mode()) or "characters"
        by = await self.config.guild(guild).characters_by_mode()
        return list(by.get(mode, []))

    async def _set_pool_for_current_mode(self, guild: discord.Guild, new_list: List[str]) -> None:
        mode = (await self.config.guild(guild).current_mode()) or "characters"
        by = await self.config.guild(guild).characters_by_mode()
        by[mode] = list(new_list)
        await self.config.guild(guild).characters_by_mode.set(by)

    async def list_characters(self, guild: discord.Guild) -> List[str]:
        """List entries for the CURRENT MODE."""
        return await self._get_pool_for_current_mode(guild)

    async def add_character(self, guild: discord.Guild, title: str) -> bool:
        """Add entry to CURRENT MODE pool."""
        items = await self._get_pool_for_current_mode(guild)
        if title in items:
            return False
        items.append(title)
        await self._set_pool_for_current_mode(guild, items)
        return True

    async def remove_character(self, guild: discord.Guild, title: str) -> bool:
        """Remove entry from CURRENT MODE pool (also cleans aliases/hints for that title)."""
        items = await self._get_pool_for_current_mode(guild)
        if title not in items:
            return False
        items.remove(title)
        await self._set_pool_for_current_mode(guild, items)
        # cleanup optional hint & aliases
        aliases = await self.config.guild(guild).aliases()
        aliases.pop(title, None)
        await self.config.guild(guild).aliases.set(aliases)
        hints = await self.config.guild(guild).hints()
        hints.pop(title, None)
        await self.config.guild(guild).hints.set(hints)
        return True

    async def pick_random_title(self, guild: discord.Guild) -> Optional[str]:
        """Pick from CURRENT MODE pool."""
        items = await self._get_pool_for_current_mode(guild)
        return random.choice(items) if items else None

    # ---------------- answer checking ----------------

    async def check_guess(self, guild: discord.Guild, user_input: str) -> Tuple[bool, Optional[str]]:
        active = await self.get_active(guild)
        # No round or expired → treat as no active round
        if not active or not active.get("title") or active.get("expired"):
            return False, None

        title = active["title"]
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
        if guess == k:
            return True
        if k in guess or guess in k:
            return True
        g_tokens = set(t for t in guess.split() if len(t) >= 3)
        k_tokens = set(t for t in k.split() if len(t) >= 3)
        return bool(g_tokens & k_tokens)

    # ---------------- fandom API helpers ----------------

    async def fetch_page_brief(self, title: str):
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

        # split by lines starting with *
        lines = [ln.strip() for ln in wikitext.splitlines() if ln.strip().startswith("*")]
        if not lines:
            return None

        # strip simple wiki markup and filter
        import re as _re
        candidates: List[str] = []
        for ln in lines:
            text = ln.lstrip("*").strip()
            # strip links
            text = _re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", text)
            text = _re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
            # italics/bold
            text = text.replace("'''''", "").replace("'''", "").replace("''", "")
            # templates
            text = _re.sub(r"\{\{[^}]+\}\}", "", text)
            # refs
            text = _re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
            text = _re.sub(r"<ref[^/]*/>", "", text, flags=_re.IGNORECASE)
            # html tags
            text = _re.sub(r"<[^>]+>", "", text)
            # leading "Name: "
            text = _re.sub(r"^[^:]{1,40}:\s+", "", text).strip()
            if not text:
                continue
            low = text.lower()
            if any(tok in low for tok in forbidden):
                continue
            candidates.append(text)

        if not candidates:
            return None
        return random.choice(candidates)

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