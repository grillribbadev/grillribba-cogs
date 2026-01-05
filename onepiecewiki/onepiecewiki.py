# onepiecewiki.py
from __future__ import annotations

import asyncio
import re
import html as ihtml
import difflib
from typing import Any, Dict, Optional, Tuple, List

import aiohttp
import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import humanize_list

API_BASE = "https://onepiece.fandom.com/api.php"
WIKI_BASE = "https://onepiece.fandom.com/wiki/"

# ---------- cleaning helpers ----------
WIKILINK = re.compile(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]")
STRIP_TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
REFNUM = re.compile(r"(?:\[\d+\])+")          # [1] / [1][2]...
DANGLING_SEMI = re.compile(r"\s*;\s*")
DANGLING_COMMA = re.compile(r"\s*,\s*,+")
MULTI_SEP = re.compile(r"\s*[;•|\u2022]\s*")  # ; or •

def _clean_wikival(val: str) -> str:
  """Strip wiki markup, citations [1], html, and tidy punctuation."""
  val = ihtml.unescape(val or "")
  val = WIKILINK.sub(r"\1", val)
  val = REFNUM.sub("", val)
  val = STRIP_TAGS.sub("", val)
  val = DANGLING_SEMI.sub(", ", val)
  val = DANGLING_COMMA.sub(", ", val)
  val = WS.sub(" ", val).strip(" ,;")
  return val

def _norm_key(key: str) -> str:
  return re.sub(r"[^a-z0-9]+", "", (key or "").lower())

def _limit(text: str, n: int = 1024) -> str:
  return text if len(text) <= n else (text[: n - 1] + "…")

def _bullets(val: str, *, max_items: int = 12) -> str:
  if not val:
    return ""
  s = _clean_wikival(val)
  parts = [p.strip() for p in MULTI_SEP.split(s) if p.strip()]
  if len(parts) == 1 and "," in s:
    parts = [p.strip() for p in s.split(",") if p.strip()]
  if not parts:
    parts = [s]
  parts = parts[:max_items]
  return "\n".join(f"• {p}" for p in parts)

# ---------- bounty parsing (HTML-aware & robust) ----------
GROUPED_ANY = re.compile(r"\d(?:[\d,\.\u00A0\u202F ]{2,}\d)")  # numbers with grouping (commas/spaces/thin-spaces/dots)
NUM_SEP = re.compile(r"[^\d]")  # strip non-digits
NOTE_RE = re.compile(r"\(([^)]+)\)")

MIN_BOUNTY = 1_000_000
MAX_BOUNTY = 10_000_000_000_000  # 10T cap
ORDINALS = ["1st known","2nd known","3rd known","4th known","5th known",
      "6th known","7th known","8th known","9th known","10th known"]

SECTION_H2 = re.compile(r"<h2[^>]*>.*?</h2>", re.I | re.S)
STRIP_ANCHORS = re.compile(r'<a[^>]*>(.*?)</a>', re.I | re.S)
H2_BLOCK_RE = re.compile(r"(<h2[^>]*>.*?</h2>)(.*?)(?=<h2|$)", re.I | re.S)

def _fmt_amount(n: int) -> str:
  return f"{n:,}"

def _extract_bounty_amounts(text_or_html: str) -> List[int]:
  """
  Find all plausible bounty numbers inside a string/HTML:
  - accept commas, spaces, thin spaces, dots
  - strip all non-digits and parse
  - filter to >= 1,000,000 and sane upper bound
  """
  amounts: List[int] = []
  seen = set()

  clean = (text_or_html or "").replace("\u2009", " ").replace("\u202F", " ").replace("\u00A0", " ")

  for m in re.finditer(r"\d[\d,\. ]*\d", clean):
    digits = re.sub(r"[^\d]", "", m.group(0))
    if not digits:
      continue
    try:
      amt = int(digits)
    except ValueError:
      continue
    if MIN_BOUNTY <= amt <= MAX_BOUNTY and amt not in seen:
      seen.add(amt)
      amounts.append(amt)
  amounts.sort()
  return amounts

def _extract_bounties_from_html(html: str) -> List[int]:
  # Prefer the PortableInfobox data-source form
  blocks = re.findall(
    r'<div[^>]*data-source="bounty"[^>]*>.*?<div[^>]*class="[^"]*pi-data-value[^"]*"[^>]*>(.*?)</div>',
    html, re.I | re.S
  )
  if not blocks:
    # Fallback to label (Bounty / Bounties)
    blocks = re.findall(
      r'<h3[^>]*class="[^"]*pi-data-label[^"]*"[^>]*>\s*Bount?ies?\s*</h3>\s*'
      r'<div[^>]*class="[^"]*pi-data-value[^"]*"[^>]*>(.*?)</div>',
      html, re.I | re.S
    )
  if not blocks:
    return []
  return _extract_bounty_amounts(" ".join(blocks))

def _parse_bounty_text(raw: str) -> List[int]:
  return _extract_bounty_amounts(_clean_wikival(raw))

# ---------- debut extraction (HTML-aware) ----------
def _extract_debut_from_html(html: str) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[int]]:
  """
  Returns (manga_text, anime_text, chapter_no, episode_no) best-effort.
  Most pages use a single "Debut" row with Manga/Anime inline items.
  """
  # Try the unified "Debut" row first
  m = re.search(
    r'<h3[^>]*class="[^"]*pi-data-label[^"]*"[^>]*>\s*Debut\s*</h3>\s*'
    r'<div[^>]*class="[^"]*pi-data-value[^"]*"[^>]*>(.*?)</div>',
    html, re.I | re.S
  )
  if not m:
    # Rare legacy: separate Manga Debut/Anime Debut rows
    def grab(label: str) -> Optional[str]:
      mm = re.search(
        rf'<h3[^>]*class="[^"]*pi-data-label[^"]*"[^>]*>\s*{label}\s*</h3>\s*'
        r'<div[^>]*class="[^"]*pi-data-value[^"]*"[^>]*>(.*?)</div>',
        html, re.I | re.S
      )
      return mm.group(1) if mm else None
    man_html = grab("Manga Debut")
    ani_html = grab("Anime Debut")
  else:
    debut_val_html = m.group(1)
    im_m = re.search(r'(?:Manga)\s*[:：]\s*(.*?)(?=$|<br|</div)', debut_val_html, re.I | re.S)
    im_a = re.search(r'(?:Anime)\s*[:：]\s*(.*?)(?=$|<br|</div)', debut_val_html, re.I | re.S)
    man_html = im_m.group(1) if im_m else None
    ani_html = im_a.group(1) if im_a else None

  man_text = _clean_wikival(man_html) if man_html else None
  ani_text = _clean_wikival(ani_html) if ani_html else None

  chap_no: Optional[int] = None
  ep_no: Optional[int] = None

  # Chapter number from HTML or cleaned text
  if man_html and chap_no is None:
    m = re.search(r"(?:Chapter[_\s]|title=\"Chapter\s*|Chapter\s+)(\d{1,4})", man_html, re.I)
    if m: chap_no = int(m.group(1))
  if chap_no is None and man_text:
    m = re.search(r"chapter\s+(\d{1,4})", man_text, re.I)
    if m: chap_no = int(m.group(1))

  # Episode number from HTML or cleaned text
  if ani_html and ep_no is None:
    m = re.search(r"(?:Episode[_\s]|title=\"Episode\s*|Episode\s+)(\d{1,4})", ani_html, re.I)
    if m: ep_no = int(m.group(1))
  if ep_no is None and ani_text:
    m = re.search(r"episode\s+(\d{1,4})", ani_text, re.I)
    if m: ep_no = int(m.group(1))

  return man_text, ani_text, chap_no, ep_no

# ---------- aliases ----------
COMMON_ALIASES: Dict[str, str] = {
  "luffy": "Monkey D. Luffy", "zoro": "Roronoa Zoro", "roronoa": "Roronoa Zoro",
  "sanji": "Vinsmoke Sanji", "nami": "Nami", "usopp": "Usopp",
  "chopper": "Tony Tony Chopper", "robin": "Nico Robin", "franky": "Franky",
  "brook": "Brook", "jinbe": "Jinbe", "jimbei": "Jinbe", "ace": "Portgas D. Ace",
  "sabo": "Sabo", "shanks": "Shanks", "buggy": "Buggy", "garp": "Monkey D. Garp",
  "kuzan": "Kuzan", "aokiji": "Kuzan", "kizaru": "Borsalino", "borsalino": "Borsalino",
  "akainu": "Sakazuki", "sakazuki": "Sakazuki", "law": "Trafalgar D. Water Law",
  "trafalgar": "Trafalgar D. Water Law", "mihawk": "Dracule Mihawk",
  "doflamingo": "Donquixote Doflamingo", "dofy": "Donquixote Doflamingo",
  "katakuri": "Charlotte Katakuri", "big mom": "Charlotte Linlin", "linlin": "Charlotte Linlin",
  "kaido": "Kaido", "kaidou": "Kaido", "yamato": "Yamato", "kid": "Eustass Kid",
  "killer": "Killer", "hancock": "Boa Hancock", "boa": "Boa Hancock",
  "smoker": "Smoker", "crocodile": "Crocodile", "eneru": "Enel", "enel": "Enel",
}

def _fuzzy_query(q: str) -> str:
  tokens = [t for t in re.split(r"\s+", q.strip()) if t]
  return " ".join((t if len(t) < 4 else f"{t}~") for t in tokens) or q


def _similarity(a: str, b: str) -> float:
  """Return a 0..1 similarity score between two strings."""
  a_n = re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()
  b_n = re.sub(r"[^a-z0-9]+", " ", (b or "").lower()).strip()
  if not a_n or not b_n:
    return 0.0

  ratio = difflib.SequenceMatcher(None, a_n, b_n).ratio()
  a_tokens = [t for t in a_n.split() if t]
  b_tokens = set(t for t in b_n.split() if t)
  token_hit = (sum(1 for t in a_tokens if t in b_tokens) / max(1, len(a_tokens)))

  # Ratio catches misspellings; token_hit catches partial names.
  return (0.65 * ratio) + (0.35 * token_hit)


def _min_similarity_threshold(query: str) -> float:
  """Short queries are ambiguous; require a bit more confidence."""
  q = (query or "").strip()
  if not q:
    return 1.0
  # For very short queries (e.g., "ace") a lot of pages can match;
  # keep threshold slightly higher to avoid random picks.
  if len(q) <= 4:
    return 0.62
  return 0.55

# ---------- Cog ----------
class OnePieceWiki(commands.Cog):
  """One Piece Wiki — clean card with big image, accurate bounty list, crews, and debut chapter/episode."""

  __author__ = "your_name"
  __version__ = "2.9.0"

  def __init__(self, bot):
    self.bot = bot
    self.session: Optional[aiohttp.ClientSession] = None
    self.config = Config.get_conf(self, identifier=2025091001, force_registration=True)
    self.config.register_guild(aliases={})

  async def cog_load(self):
    self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))

  async def cog_unload(self):
    if self.session and not self.session.closed:
      await self.session.close()

  @commands.hybrid_command(name="wiki", aliases=["opwiki"], usage="<character>")
  @commands.guild_only()
  @commands.bot_in_a_guild()
  @commands.bot_has_permissions(embed_links=True)
  @commands.cooldown(3, 10, commands.BucketType.user)
  @commands.max_concurrency(2, commands.BucketType.channel, wait=True)
  async def wiki(self, ctx: commands.Context, *, query: str):
    """Search OP Wiki and display a lean character card."""
    await ctx.typing()
    # support for the feats feature
    parts = query.split()
    mode = None
    if len(parts) >= 2:
      last = parts[-1].lower()
      if last in {"feats"}:
        mode = last
        query = " ".join(parts[:-1]).strip()

    try:
      title, suggestions = await self._search(ctx.guild, query)
      if not title:
        em = discord.Embed(
          title="No close match found",
          description="I couldn't find a wiki result that closely matches your query.",
          color=discord.Color.red(),
        )
        if suggestions:
          sug = humanize_list([f"[{t}]({WIKI_BASE}{t.replace(' ', '_')})" for t in suggestions[:5]])
          em.add_field(name="Did you mean", value=sug, inline=False)
        em.set_footer(text="Tip: try a full name or different spelling")
        return await ctx.reply(embed=em)
      info, page_url, raw_html = await self._fetch_infobox(title)
      summary, big_img = await self._fetch_summary_and_image(title)
    except asyncio.TimeoutError:
      em = discord.Embed(
        title="Wiki timed out",
        description="The One Piece Wiki didn't respond in time. Try again in a moment.",
        color=discord.Color.orange(),
      )
      return await ctx.reply(embed=em)
    except aiohttp.ClientError:
      em = discord.Embed(
        title="Wiki unavailable",
        description="I couldn't reach the One Piece Wiki right now. Please try later.",
        color=discord.Color.orange(),
      )
      return await ctx.reply(embed=em)

    if summary and not info.get("summary"):
      info["summary"] = summary
    if big_img and not info.get("_image"):
      info["_image"] = big_img

    # ===== mode: feats =====
    if mode == "feats":
      sections = self._extract_section_blocks(raw_html)
      sections_norm = {k.strip().lower(): v for k, v in sections.items()}
      body_html = None
      # exact title match first
      for key in self.PREFERRED_FEATS:
        body_html = sections_norm.get(key)
        if body_html:
          break
      # fuzzy title contains as fallback
      if not body_html:
        for t, b in sections_norm.items():
          if any(k in t for k in self.PREFERRED_FEATS):
            body_html = b
            break

      if not body_html:
        return await ctx.reply("I couldn't find a Feats/Powers section on this page.")

      bullets = self._html_to_bullets(body_html, limit=12)
      if not bullets:
        return await ctx.reply("The Feats/Powers section has no listable items.")

      display_title = info.get("name") or title
      em = discord.Embed(
        title=f"{display_title} — Feats",
        url=page_url,
        color=discord.Color.gold()
      )
      em.description = _limit("\n".join(bullets), 4000)
      if big_img:
        em.set_thumbnail(url=big_img)  # thumbnail so the list stays readable
      em.set_footer(text="From the Abilities/Powers section (best match)")
      # show suggestions (if any) under feats too
      await ctx.reply(embed=em)
      if suggestions:
        sug = humanize_list([f"[{t}]({WIKI_BASE}{t.replace(' ', '_')})" for t in suggestions[:4]])
        tiny = discord.Embed(description=f"**Did you mean:** {sug}", color=discord.Color.dark_grey())
        await ctx.send(embed=tiny)
      return

    # Bounties straight from HTML (fallback to text value)
    bounty_amounts = _extract_bounties_from_html(raw_html)
    if not bounty_amounts and info.get("bounty"):
      bounty_amounts = _parse_bounty_text(info["bounty"])

    # Debut extraction from HTML (fallback to text)
    man_text_h, ani_text_h, chap_no_h, ep_no_h = _extract_debut_from_html(raw_html)
    man_text = man_text_h or _clean_wikival(self._first(info, ["mangadebut", "debutmanga"]) or "")
    ani_text = ani_text_h or _clean_wikival(self._first(info, ["animedebut", "debutanime", "firstappearance"]) or "")
    chap_no = chap_no_h if chap_no_h is not None else self._extract_first_number(man_text, "chapter")
    ep_no   = ep_no_h if ep_no_h is not None   else self._extract_first_number(ani_text, "episode")

    display_title = info.get("name") or title
    embed = discord.Embed(title=display_title, url=page_url, color=discord.Color.blurple())
    embed.set_author(name="One Piece Wiki", url=WIKI_BASE)

    # Crews (current / former)
    crews_current, crews_former = self._extract_crews(info)
    if crews_current:
      embed.add_field(name="Crews — Current", value=_limit(_bullets(crews_current)), inline=False)
    if crews_former:
      embed.add_field(name="Crews — Former", value=_limit(_bullets(crews_former)), inline=False)

    # Information (lean)
    lines: List[str] = []

    pirate_flag, marine_flag = self._infer_roles(
      info.get("affiliations"), info.get("occupation") or info.get("occupations")
    )
    if pirate_flag is not None:
      lines.append(f"**Pirate:** {'Yes 🏴‍☠️' if pirate_flag else 'No'}")

    fruit = self._first(info, ["devilfruit", "devilfruittype", "fruit"])
    haki  = self._first(info, ["haki", "hakitype", "hakitypes"])
    if fruit: lines.append(f"**Devil Fruit:** {_clean_wikival(fruit)}") # noqa
    if haki:  lines.append(f"**Haki:** {_clean_wikival(haki)}")

    if bounty_amounts and not marine_flag:
      lines.append("**Bounty:**")
      for i, amt in enumerate(bounty_amounts):
        label = ORDINALS[i] if i < len(ORDINALS) else f"{i+1}th known"
        if i == len(bounty_amounts) - 1:
          label = f"{label} • Current"
        lines.append(f"• {_fmt_amount(amt)} — {label}")

    # Debut lines
    if man_text:
      lines.append(f"**Debut (Manga):** {man_text}")
    if ani_text:
      lines.append(f"**Debut (Anime):** {ani_text}")
    if chap_no is not None:
      lines.append(f"**First Chapter:** {chap_no}")
    if ep_no is not None:
      lines.append(f"**First Episode:** {ep_no}")

    # Stats
    for label, keys in [
      ("Status",   ["status", "alive", "deceased"]),
      ("Epithet",  ["epithet", "nicknames", "alias"]),
      ("Age",      ["age"]),
      ("Birthday", ["birthday"]),
      ("Origin",   ["origin", "birthplace", "residence"]),
      ("Height",   ["height"]),
    ]:
      v = self._first(info, keys)
      if v:
        lines.append(f"**{label}:** {_clean_wikival(v)}")

    canon = info.get("_canon")
    if canon is not None:
      lines.append(f"**Canon:** {'Canon' if canon else 'Non-Canon'}")

    summ = (info.get("summary") or "").strip()
    if summ:
      lines.append("")
      lines.append(_limit(_clean_wikival(summ), 700))

    if lines:
      embed.add_field(name="Information", value=_limit("\n".join(lines).strip()), inline=False)

    if info.get("_image"):
      embed.set_image(url=info["_image"])

    embed.set_footer(text=f"Result powered by Fandom • {self.__version__}")
    await ctx.reply(embed=embed)

    if suggestions:
      sug = humanize_list([f"[{t}]({WIKI_BASE}{t.replace(' ', '_')})" for t in suggestions[:4]])
      tiny = discord.Embed(description=f"**Did you mean:** {sug}", color=discord.Color.dark_grey())
      await ctx.send(embed=tiny)

  # ---------- alias admin ----------
  @commands.hybrid_group(name="wikialias", aliases=["wikalias", "opalias"], invoke_without_command=True)
  @commands.guild_only()
  async def wikialias(self, ctx: commands.Context):
    await ctx.send_help()

  @wikialias.command(name="add")
  @commands.guild_only()
  @commands.mod_or_permissions(manage_guild=True)
  async def wikialias_add(self, ctx: commands.Context, key: str, *, target: str):
    key_l = key.strip().lower()
    async with self.config.guild(ctx.guild).aliases() as al:
      al[key_l] = target.strip()
    await ctx.reply(f"Alias **{key_l}** → **{target.strip()}** added.")

  @wikialias.command(name="remove", aliases=["del", "rm"])
  @commands.guild_only()
  @commands.mod_or_permissions(manage_guild=True)
  async def wikialias_remove(self, ctx: commands.Context, key: str):
    key_l = key.strip().lower()
    async with self.config.guild(ctx.guild).aliases() as al:
      if key_l in al:
        del al[key_l]
        return await ctx.reply(f"Alias **{key_l}** removed.")
    await ctx.reply("That alias doesn't exist here.")

  @wikialias.command(name="list")
  @commands.guild_only()
  async def wikialias_list(self, ctx: commands.Context):
    al = await self.config.guild(ctx.guild).aliases()
    if not al:
      return await ctx.reply("No aliases set for this server.")
    lines = [f"• **{k}** → {v}" for k, v in sorted(al.items())]
    await ctx.reply("**Aliases:**\n" + "\n".join(lines))

  # ---------- internals ----------
  async def _title_exists(self, title: str) -> Optional[str]:
    assert self.session is not None
    params = {"action": "query", "titles": title, "prop": "categories", "cllimit": 10,
          "format": "json", "redirects": 1, "utf8": 1}
    async with self.session.get(API_BASE, params=params) as resp:
      data = await resp.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
      return None
    page = next(iter(pages.values()))
    if page.get("missing"):
      return None
    return page.get("title")

  async def _search(self, guild: Optional[discord.Guild], query: str) -> Tuple[Optional[str], List[str]]:
    assert self.session is not None
    q = (query or "").strip()
    if not q:
      return None, []

    guild_aliases: Dict[str, str] = await self.config.guild(guild).aliases() if guild else {}
    alias_target = guild_aliases.get(q.lower()) or COMMON_ALIASES.get(q.lower())
    if alias_target:
      exact = await self._title_exists(alias_target)
      if exact:
        return exact, []

    async def run_search(srsearch: str, limit: int = 8):
      params = {"action": "query", "list": "search", "srsearch": srsearch,
            "srlimit": limit, "format": "json", "utf8": 1}
      async with self.session.get(API_BASE, params=params) as resp:
        return await resp.json()

    data = await run_search(q, 8)
    hits = data.get("query", {}).get("search", [])
    if not hits:
      data = await run_search(_fuzzy_query(q), 8)
      hits = data.get("query", {}).get("search", [])

    if not hits:
      params_os = {"action": "opensearch", "search": q, "limit": 5,
             "namespace": 0, "redirects": "resolve", "format": "json"}
      async with self.session.get(API_BASE, params=params_os) as resp:
        osj = await resp.json()
      if len(osj) >= 2 and osj[1]:
        suggestions = list(map(str, osj[1]))
        # Don't auto-pick a weak match from opensearch; enforce closeness.
        best = suggestions[0]
        if _similarity(q, best) < _min_similarity_threshold(q):
          return None, suggestions[:4]
        first = await self._title_exists(best)
        return (first or best), suggestions[1:4]
      return None, []

    tokens = [t for t in re.split(r"\s+", q.lower()) if t]
    def score(hit):
      title = hit.get("title", "")
      tlow = title.lower()
      base = hit.get("score", 0) if "score" in hit else 0
      token_bonus = sum(1 for t in tokens if t in tlow) * 10
      paren_bonus = 5 if any(k in tlow for k in ("(", ")")) else 0
      return base + token_bonus + paren_bonus

    hits.sort(key=score, reverse=True)
    top = hits[0]["title"]
    suggestions = [h["title"] for h in hits[1:6]]

    # If the best match isn't close to the user's query, refuse to guess.
    if _similarity(q, top) < _min_similarity_threshold(q):
      return None, [top] + suggestions

    canonical = await self._title_exists(top)
    return (canonical or top), suggestions

  async def _fetch_summary_and_image(self, title: str) -> Tuple[str, Optional[str]]:
    assert self.session is not None
    params = {"action": "query", "prop": "extracts|pageimages", "exintro": 1, "explaintext": 1,
          "redirects": 1, "titles": title, "pithumbsize": 1200, "format": "json", "utf8": 1}
    async with self.session.get(API_BASE, params=params) as resp:
      data = await resp.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
      return "", None
    page = next(iter(pages.values()))
    summary = _clean_wikival(page.get("extract") or "")
    img = (page.get("thumbnail") or {}).get("source")
    return summary, img

  async def _fetch_infobox(self, title: str) -> Tuple[Dict[str, str], str, str]:
    """
    Parse the PortableInfobox for key/value pairs and return:
    (info_dict, page_url, raw_html_of_the_parse)
    """
    assert self.session is not None
    page_url = f"{WIKI_BASE}{title.replace(' ', '_')}"
    params = {"action": "parse", "page": title, "prop": "text|properties|categories",
          "format": "json", "redirects": 1, "utf8": 1}
    info: Dict[str, str] = {"_canon": None}

    async with self.session.get(API_BASE, params=params) as resp:
      data = await resp.json()

    parse = data.get("parse", {})
    html = parse.get("text", {}).get("*", "") or ""
    cats = [c.get("*", "") for c in parse.get("categories", []) if isinstance(c, dict)]
    if any("Non-Canon" in c or "Non Canon" in c for c in cats):
      info["_canon"] = False

    # image
    m_img = re.search(r'<figure[^>]*class="[^"]*pi-image[^"]*"[^>]*>.*?<img[^>]+src="([^"]+)"', html, re.S)
    if m_img:
      info["_image"] = ihtml.unescape(m_img.group(1))

    # name
    m_name = re.search(r'<h2[^>]*class="[^"]*pi-title[^"]*"[^>]*>(.*?)</h2>', html, re.S)
    if m_name:
      info["name"] = _clean_wikival(m_name.group(1))

    # key/value rows
    for lab, val in re.findall(
      r'<h3[^>]*class="[^"]*pi-data-label[^"]*"[^>]*>(.*?)</h3>\s*'
      r'<div[^>]*class="[^"]*pi-data-value[^"]*"[^>]*>(.*?)</div>',
      html, re.S,
    ):
      key = _norm_key(_clean_wikival(lab))
      value = _clean_wikival(val)
      if not key or not value:
        continue
      if key in info:
        info[key] = humanize_list([info[key], value])
      else:
        info[key] = value

    if "affiliations" not in info and "affiliation" in info:
      info["affiliations"] = info["affiliation"]

    # canon explicit field overrides category inference
    canon_field = info.get("canon") or info.get("iscanon")
    if canon_field:
      fl = canon_field.strip().lower()
      if fl in {"yes", "y", "true", "canon"}:
        info["_canon"] = True
      elif fl in {"no", "n", "false", "non-canon", "non canon"}:
        info["_canon"] = False
    elif info["_canon"] is None:
      info["_canon"] = True

    return info, page_url, html

  # ---- utilities ----
  def _first(self, info: Dict[str, str], keys: List[str]) -> Optional[str]:
    for k in keys:
      nk = _norm_key(k)
      if nk in info and info[nk]:
        return info[nk]
    return None

  def _infer_roles(self, affiliations: Optional[str], occupations: Optional[str]) -> Tuple[Optional[bool], bool]:
    """Return (pirate_flag, marine_flag)."""
    text = " ".join([affiliations or "", occupations or ""]).lower()
    if not text:
      return None, False
    marine_words = ["marine", "vice admiral", "admiral", "rear admiral", "ensign", "seaman",
            "cipher pol", "world government", "cp0", "cp-0", "cp9", "navy"]
    marine_flag = any(w in text for w in marine_words)
    pirate_flag = ("pirate" in text or "pirates" in text)
    return (True if pirate_flag else (False if marine_flag else None), marine_flag)

  def _extract_crews(self, info: Dict[str, str]) -> Tuple[str, str]:
    current_sources = [
      info.get("crew"), info.get("crews"),
      info.get("piratecrew"), info.get("piratecrews"),
      info.get("affiliations"), info.get("organization"), info.get("organizations"),
    ]
    former_sources = [
      info.get("formercrew"), info.get("formercrews"),
      info.get("formeraffiliations"), info.get("previousaffiliations"), info.get("pastaffiliations"),
    ]

    cur_items: List[str] = []
    former_items: List[str] = []

    def push_items(raw: Optional[str], dest: List[str]):
      if not raw:
        return
      for p in [p.strip() for p in MULTI_SEP.split(raw) if p.strip()]:
        if p and p not in dest:
          dest.append(p)

    for src in current_sources:
      if not src:
        continue
      parts = [p.strip() for p in MULTI_SEP.split(src) if p.strip()]
      for p in parts:
        if "(former" in p.lower() or "former)" in p.lower():
          p2 = re.sub(r"\s*\(.*?former.*?\)\s*", "", p, flags=re.I).strip()
          if p2 and p2 not in former_items:
            former_items.append(p2)
        else:
          if p.lower() not in {"world government", "marines", "navy"}:
            if p not in cur_items:
              cur_items.append(p)

    for src in former_sources:
      push_items(src, former_items)

    cur_set: List[str] = []
    for x in cur_items:
      if x not in former_items and x not in cur_set:
        cur_set.append(x)

    return (", ".join(cur_set), ", ".join(former_items))

  def _extract_first_number(self, text: Optional[str], hint: str) -> Optional[int]:
    if not text:
      return None
    t = _clean_wikival(text).lower()
    if hint == "chapter":
      m = re.search(r"chapter\s+(\d{1,4})", t, re.I)
      if m:
        return int(m.group(1))
    if hint == "episode":
      m = re.search(r"episode\s+(\d{1,4})", t, re.I)
      if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,4})\b", t)
    return int(m.group(1)) if m else None

  def _clean_html_text(self, html: str) -> str:
    return _clean_wikival(html)

  def _best_section_title(self, title_html: str) -> str:
    # Remove the anchor spans and tags to get plain text
    title_html = STRIP_ANCHORS.sub("", title_html)
    return self._clean_html_text(title_html)

  def _extract_section_blocks(self, raw_html: str) -> Dict[str, str]:
    """Return {section_title: section_html_block} for all h2 sections"""
    out = {}
    for m in H2_BLOCK_RE.finditer(raw_html):
      title_html, body_html = m.group(1), m.group(2)
      # get human title
      t_m = SECTION_H2.search(title_html)
      if not t_m:
        continue
      title = self._best_section_title(t_m.group(0))
      if not title:
        continue
      out[title] = body_html
    return out

  def _html_to_bullets(self, section_html: str, limit: int = 12) -> List[str]:
    """Pull <li> items first;  if none, split paragraphs"""
    items = re.findall(r"<li[^>]*>(.*?)</li>", section_html, re.I | re.S)
    if not items:
      paras = [p.strip() for p in re.findall(r"<p[^>]*>(.*?)</p>", section_html, re.I | re.S) if p.strip()]
      items = [p for p in paras if len(self._clean_html_text(p)) > 15]  # keep meaningful text
    bullets = []
    for it in items:
      text = self._clean_html_text(it)
      if text:
        bullets.append(f"• {text}")
      if len(bullets) >= limit:
        break
    return bullets

  PREFERRED_FEATS = [
    "abilities and powers",
    "abilities & powers",
    "powers and abilities",
    "techniques",
    "abilities",
    "powers",
  ]


# ----- Red setup (single-file cog) -----
async def setup(bot):
  await bot.add_cog(OnePieceWiki(bot))
