from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
from io import BytesIO
import aiohttp
import io
import time
import re

import discord
from redbot.core import commands
from redbot.core.bot import Red

from .constants import COLOR_OK
from .core import GuessEngine
from .tasks import GuessTasks

DATA_DIR = Path(__file__).parent / "data"
SEED_FILE = DATA_DIR / "character_pool.json"


class OnePieceGuess(commands.Cog):
    """Timed One Piece guessing game using Fandom API. Blurred images. Fully configurable."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.engine = GuessEngine(bot)
        self.tasks = GuessTasks(self, self.engine)

    async def cog_load(self) -> None:
        # seed characters into config on first load if empty
        for guild in self.bot.guilds:
            await self.engine.ensure_profiles_migrated(guild)
            chars = await self.engine.config.guild(guild).characters()
            if not chars and SEED_FILE.exists():
                try:
                    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
                    if isinstance(seed, list) and seed:
                        await self.engine.config.guild(guild).characters.set([str(s) for s in seed])
                except Exception:
                    pass
        await self.tasks.start()

    def cog_unload(self) -> None:
        self.tasks.cancel()

    # --------- status / root group ---------
    @commands.hybrid_group(name="opguess", invoke_without_command=True)
    @commands.guild_only()
    async def opguess(self, ctx: commands.Context):
        """OnePieceGuess: admin & status."""
        g = await self.engine.config.guild(ctx.guild).all()
        enabled = "ON" if g.get("enabled") else "OFF"
        ch = ctx.guild.get_channel(g.get("channel_id") or 0)
        blur_profiles = g.get("blur_profiles") or {}
        tap = g.get("team_api") or {}

        default_prof = blur_profiles.get("_default") or {}
        status = (
            f"Status: **{enabled}**\n"
            f"Channel: {ch.mention if ch else '—'}\n"
            f"Interval: **{g.get('interval')}s**\n"
            f"Round timeout: **{g.get('roundtime')}s**\n"
            f"Reward: **{g.get('reward')}**\n"
            f"Blur (default): **{default_prof.get('mode','gaussian')}** @ **{default_prof.get('strength',8)}** • B/W: **{'ON' if default_prof.get('bw') else 'OFF'}**\n"
            f"Hint: **{'ON' if g.get('hint_enabled') else 'OFF'}** (max {g.get('hint_max_chars')} chars)\n"
            f"Teams: **{'ON' if tap.get('enabled') else 'OFF'}** (mode: {tap.get('mode') or 'teamscog'}, "
            f"win_pts: {int(tap.get('win_points') or 0)}, timeout_pts: {int(tap.get('timeout_points') or 0)})"
        )
        # also show category overrides summary
        cats = g.get("categories") or {}
        prof_names = [k for k in (blur_profiles.keys()) if k != "_default"]
        if prof_names:
            status += f"\nBlur overrides: {', '.join(sorted(prof_names))}"
        if cats:
            status += f"\nMapped titles: **{len(cats)}** (use `opguess category list`)"
        await ctx.reply(status, allowed_mentions=discord.AllowedMentions.none())

    # ---- status command already present in your version ----
    @opguess.command(name="status")
    @commands.admin()
    @commands.guild_only()
    async def opguess_status(self, ctx: commands.Context):
        """Show the current round status: answer, jump link, and remaining time."""
        active = await self.engine.get_active(ctx.guild)
        if not active or not active.get("title"):
            return await ctx.reply("No active round right now.")
        title: str = active.get("title")
        msg_id = active.get("posted_message_id")
        chan_id = active.get("posted_channel_id")
        started_at = active.get("started_at") or 0

        g = await self.engine.config.guild(ctx.guild).all()
        roundtime = int(g.get("roundtime") or 120)
        elapsed = int(max(0, time.time() - started_at)) if started_at else 0
        remaining = max(0, roundtime - elapsed)

        jump = None
        if chan_id and msg_id:
            ch = ctx.guild.get_channel(int(chan_id))
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(int(msg_id))
                    jump = msg.jump_url
                except Exception:
                    pass

        emb = discord.Embed(
            title="🧭 Current Round Status",
            color=discord.Color.blurple(),
            description=f"**Answer:** {title}\n**Time remaining:** {remaining}s (of {roundtime}s)",
        )
        if jump:
            emb.add_field(name="Round Message", value=f"[Jump to message]({jump})", inline=False)
        await ctx.reply(embed=emb, allowed_mentions=discord.AllowedMentions.none())

    # ---- admin: toggle/channel/interval/reward/roundtime ----
    @opguess.command(name="toggle")
    @commands.admin()
    async def opguess_toggle(self, ctx: commands.Context, on_off: Optional[bool] = None):
        new = (not (await self.engine.config.guild(ctx.guild).enabled())) if on_off is None else bool(on_off)
        await self.engine.config.guild(ctx.guild).enabled.set(new)
        await ctx.reply(f"Game **{'enabled' if new else 'disabled'}**.")

    @opguess.command(name="setchannel")
    @commands.admin()
    async def opguess_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.engine.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.reply(f"Channel set to {channel.mention}")

    @opguess.command(name="setinterval")
    @commands.admin()
    async def opguess_setinterval(self, ctx: commands.Context, seconds: int):
        seconds = max(30, min(24 * 3600, int(seconds)))
        await self.engine.config.guild(ctx.guild).interval.set(seconds)
        await ctx.reply(f"Interval set to **{seconds}s**")

    @opguess.command(name="setroundtime")
    @commands.admin()
    async def opguess_setroundtime(self, ctx: commands.Context, seconds: int):
        """Set how long a round stays open before timing out (seconds)."""
        seconds = max(15, min(3600, int(seconds)))
        await self.engine.config.guild(ctx.guild).roundtime.set(seconds)
        await ctx.reply(f"Round timeout set to **{seconds}s**")

    @opguess.command(name="setreward")
    @commands.admin()
    async def opguess_setreward(self, ctx: commands.Context, amount: int):
        await self.engine.config.guild(ctx.guild).reward.set(max(0, int(amount)))
        await ctx.reply(f"Reward set to **{max(0, int(amount))}**")

    # ---- default blur (legacy commands now edit the _default profile) ----
    @opguess.group(name="blur", invoke_without_command=True)
    @commands.admin()
    async def opguess_blur(self, ctx: commands.Context):
        profiles = await self.engine.config.guild(ctx.guild).blur_profiles()
        d = profiles.get("_default") or {}
        await ctx.reply(
            f"Default Blur: **{d.get('mode','gaussian')}** • strength: **{d.get('strength',8)}** • B/W: **{'ON' if d.get('bw') else 'OFF'}**\n"
            f"Use `opguess blurprofile` to override per category."
        )

    @opguess_blur.command(name="mode")
    @commands.admin()
    async def opguess_blur_mode(self, ctx: commands.Context, mode: str):
        """Set default blur mode: gaussian|pixelate"""
        mode = mode.lower().strip()
        if mode not in {"gaussian", "pixelate"}:
            return await ctx.reply("Mode must be `gaussian` or `pixelate`.")
        await self.engine.update_blur_profile(ctx.guild, "_default", mode=mode)
        await ctx.reply(f"Default blur mode set to **{mode}**")

    @opguess_blur.command(name="strength")
    @commands.admin()
    async def opguess_blur_strength(self, ctx: commands.Context, value: int):
        """Set default blur radius/block size. Max 250."""
        value = max(1, min(250, int(value)))
        await self.engine.update_blur_profile(ctx.guild, "_default", strength=value)
        await ctx.reply(f"Default blur strength set to **{value}**")

    @opguess_blur.command(name="bw")
    @commands.admin()
    async def opguess_blur_bw(self, ctx: commands.Context, on_off: Optional[bool] = None):
        """Toggle default black & white mode for the image."""
        profiles = await self.engine.config.guild(ctx.guild).blur_profiles()
        cur = bool((profiles.get("_default") or {}).get("bw"))
        new = (not cur) if on_off is None else bool(on_off)
        await self.engine.update_blur_profile(ctx.guild, "_default", bw=new)
        await ctx.reply(f"Default black & white **{'ON' if new else 'OFF'}**")

    # ---- per-category blur overrides ----
    @opguess.group(name="blurprofile", invoke_without_command=True)
    @commands.admin()
    async def opguess_blurprofile(self, ctx: commands.Context, category: Optional[str] = None):
        """
        Show category blur (merged with default).
        Example: .opguess blurprofile devilfruits
        """
        profiles = await self.engine.config.guild(ctx.guild).blur_profiles()
        if not category:
            cats = [k for k in profiles.keys() if k != "_default"]
            return await ctx.reply("Usage: `.opguess blurprofile <category>`\n"
                                   f"Existing overrides: {', '.join(cats) if cats else '—'}")
        cat = category.strip().lower()
        merged = await self.engine.get_blur_for_title(ctx.guild, cat)  # hack: uses name to fetch category if mapped; for show, we’ll compute manually
        d = profiles.get("_default") or {}
        o = profiles.get(cat) or {}
        eff = dict(d); eff.update(o)
        await ctx.reply(
            f"Blur for **{cat}** → **{eff.get('mode','gaussian')}** @ **{eff.get('strength',8)}** • B/W: **{'ON' if eff.get('bw') else 'OFF'}**"
        )

    @opguess_blurprofile.command(name="mode")
    @commands.admin()
    async def opguess_blurprofile_mode(self, ctx: commands.Context, category: str, mode: str):
        """Set blur mode for a category: gaussian|pixelate"""
        mode = mode.lower().strip()
        if mode not in {"gaussian", "pixelate"}:
            return await ctx.reply("Mode must be `gaussian` or `pixelate`.")
        await self.engine.update_blur_profile(ctx.guild, category.strip().lower(), mode=mode)
        await ctx.reply(f"Blur mode for **{category}** set to **{mode}**")

    @opguess_blurprofile.command(name="strength")
    @commands.admin()
    async def opguess_blurprofile_strength(self, ctx: commands.Context, category: str, value: int):
        """Set blur strength for a category (max 250)."""
        value = max(1, min(250, int(value)))
        await self.engine.update_blur_profile(ctx.guild, category.strip().lower(), strength=value)
        await ctx.reply(f"Blur strength for **{category}** set to **{value}**")

    @opguess_blurprofile.command(name="bw")
    @commands.admin()
    async def opguess_blurprofile_bw(self, ctx: commands.Context, category: str, on_off: Optional[bool] = None):
        """Toggle black & white for a category."""
        profiles = await self.engine.config.guild(ctx.guild).blur_profiles()
        cat = category.strip().lower()
        cur = bool((profiles.get(cat) or {}).get("bw"))
        new = (not cur) if on_off is None else bool(on_off)
        await self.engine.update_blur_profile(ctx.guild, cat, bw=new)
        await ctx.reply(f"B/W for **{category}** **{'ON' if new else 'OFF'}**")

    @opguess_blurprofile.command(name="clear")
    @commands.admin()
    async def opguess_blurprofile_clear(self, ctx: commands.Context, category: str):
        """Clear the override so the category inherits default."""
        await self.engine.clear_blur_profile(ctx.guild, category.strip().lower())
        await ctx.reply(f"Cleared blur override for **{category}** (now inherits default).")

    # ---- categories mapping ----
    @opguess.group(name="category", invoke_without_command=True)
    @commands.admin()
    async def opguess_category(self, ctx: commands.Context):
        """Map titles to categories and set defaults."""
        g = await self.engine.config.guild(ctx.guild).all()
        cats = g.get("categories") or {}
        dc = g.get("default_category") or "characters"
        await ctx.reply(f"Default category: **{dc}**\nMapped titles: **{len(cats)}**\n"
                        f"Use `.opguess category set <title> <category>` or `.opguess category list [category]`.")

    @opguess_category.command(name="default")
    @commands.admin()
    async def opguess_category_default(self, ctx: commands.Context, category: str):
        await self.engine.set_default_category(ctx.guild, category.strip().lower())
        await ctx.reply(f"Default category set to **{category}**")

    @opguess_category.command(name="set")
    @commands.admin()
    async def opguess_category_set(self, ctx: commands.Context, title: str, category: str):
        await self.engine.set_category(ctx.guild, title.strip(), category.strip().lower())
        await ctx.reply(f"Mapped **{title}** → **{category}**")

    @opguess_category.command(name="show")
    @commands.admin()
    async def opguess_category_show(self, ctx: commands.Context, *, title: str):
        cat = await self.engine.get_category(ctx.guild, title.strip())
        await ctx.reply(f"**{title}** is in category **{cat}**")

    @opguess_category.command(name="list")
    @commands.admin()
    async def opguess_category_list(self, ctx: commands.Context, category: Optional[str] = None):
        cats = await self.engine.config.guild(ctx.guild).categories()
        if category:
            want = category.strip().lower()
            items = [t for t, c in cats.items() if c == want]
            sample = ", ".join(items[:10]) + (" …" if len(items) > 10 else "")
            return await ctx.reply(f"{want}: **{len(items)}** mapped\nSample: {sample or '—'}")
        # summary
        by = {}
        for t, c in cats.items():
            by.setdefault(c, 0)
            by[c] += 1
        if not by:
            return await ctx.reply("No explicit mappings yet.")
        lines = [f"- {k}: **{v}**" for k, v in sorted(by.items(), key=lambda x: (-x[1], x[0]))]
        await ctx.reply("Category mapping summary:\n" + "\n".join(lines))

    # ---- import/export, chars & aliases, hints, teams, reveal, forcepost, guess ----
    # (unchanged from your latest working build – omitted here for brevity except where we use blur for title)

    # ------------ IMPORT / EXPORT ------------
    @opguess.command(name="import")
    @commands.admin()
    async def opguess_import(self, ctx: commands.Context):
        if not ctx.message.attachments:
            return await ctx.reply("Attach a JSON file with a list or object.")
        att = ctx.message.attachments[0]
        raw = await att.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return await ctx.reply("Invalid JSON.")
        if isinstance(payload, list):
            titles = [str(x) for x in payload]
            await self.engine.config.guild(ctx.guild).characters.set(titles)
        elif isinstance(payload, dict):
            titles = [str(x) for x in payload.get("characters", [])]
            aliases = payload.get("aliases", {})
            hints = payload.get("hints", {})
            await self.engine.config.guild(ctx.guild).characters.set(titles)
            await self.engine.config.guild(ctx.guild).aliases.set(
                {str(k): [str(v) for v in vs] for k, vs in aliases.items()}
            )
            await self.engine.config.guild(ctx.guild).hints.set(
                {str(k): str(v) for k, v in hints.items()}
            )
        else:
            return await ctx.reply("Unsupported JSON structure.")
        await ctx.reply(f"Imported **{len(await self.engine.config.guild(ctx.guild).characters())}** entries.")

    @opguess.command(name="export")
    @commands.admin()
    async def opguess_export(self, ctx: commands.Context):
        titles = await self.engine.config.guild(ctx.guild).characters()
        aliases = await self.engine.config.guild(ctx.guild).aliases()
        hints = await self.engine.config.guild(ctx.guild).hints()
        payload = {"characters": titles, "aliases": aliases, "hints": hints}
        buf = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
        buf.seek(0)
        await ctx.reply(file=discord.File(buf, filename="onepiece_characters.json"))

    # ---- characters + aliases (keep your added alias mgmt) ----
    @opguess.group(name="char", invoke_without_command=True)
    @commands.admin()
    async def opguess_char(self, ctx: commands.Context):
        chars = await self.engine.list_characters(ctx.guild)
        if not chars:
            return await ctx.reply("No characters configured. Use `[p]opguess char add <title>` or `[p]opguess import`.")
        sample = ", ".join(chars[:10]) + (" …" if len(chars) > 10 else "")
        await ctx.reply(f"Characters: **{len(chars)}**\nSample: {sample}")

    @opguess_char.command(name="add")
    @commands.admin()
    async def opguess_char_add(self, ctx: commands.Context, *, title: str):
        ok = await self.engine.add_character(ctx.guild, title.strip())
        await ctx.reply("Added." if ok else "Already exists.")

    @opguess_char.command(name="remove")
    @commands.admin()
    async def opguess_char_remove(self, ctx: commands.Context, *, title: str):
        ok = await self.engine.remove_character(ctx.guild, title.strip())
        await ctx.reply("Removed." if ok else "Not found.")

    @opguess_char.command(name="aliases")
    @commands.admin()
    async def opguess_char_aliases(self, ctx: commands.Context, title: str, *, comma_separated: str):
        aliases = [a.strip() for a in comma_separated.split(",") if a.strip()]
        await self.engine.upsert_aliases(ctx.guild, title, aliases)
        await ctx.reply(f"Aliases set for **{title}**: {', '.join(aliases) if aliases else '—'}")

    # --- aliases management (view/add/remove/clear) ---
    async def _resolve_title(self, guild: discord.Guild, title: str) -> str:
        chars = await self.engine.config.guild(guild).characters()
        return next((t for t in chars if t.lower() == title.lower()), title)

    @opguess_char.command(name="aliasesview", aliases=["aliasshow", "aliasesget"])
    @commands.admin()
    async def opguess_char_aliases_view(self, ctx: commands.Context, *, title: str):
        title_key = await self._resolve_title(ctx.guild, title.strip())
        aliases_map = await self.engine.config.guild(ctx.guild).aliases()
        current = aliases_map.get(title_key, [])
        if not current:
            return await ctx.reply(f"**{title_key}** has no aliases saved.")
        await ctx.reply(f"**{title_key}** aliases ({len(current)}): {', '.join(current)}")

    @opguess_char.command(name="aliasadd", aliases=["addalias"])
    @commands.admin()
    async def opguess_char_alias_add(self, ctx: commands.Context, title: str, *, aliases: str):
        title_key = await self._resolve_title(ctx.guild, title.strip())
        aliases_map = await self.engine.config.guild(ctx.guild).aliases()
        base = set(aliases_map.get(title_key, []))
        new_items = {a.strip() for a in aliases.split(",") if a.strip()}
        if not new_items:
            return await ctx.reply("Provide at least one alias (comma-separated).")
        updated = sorted(base | new_items, key=str.lower)
        aliases_map[title_key] = updated
        await self.engine.config.guild(ctx.guild).aliases.set(aliases_map)
        added = len(updated) - len(base)
        await ctx.reply(f"Added {added} alias(es) to **{title_key}**.\nNow: {', '.join(updated) if updated else '—'}")

    @opguess_char.command(name="aliasremove", aliases=["remalias", "delalias"])
    @commands.admin()
    async def opguess_char_alias_remove(self, ctx: commands.Context, title: str, *, aliases: str):
        title_key = await self._resolve_title(ctx.guild, title.strip())
        aliases_map = await self.engine.config.guild(ctx.guild).aliases()
        current = set(aliases_map.get(title_key, []))
        if not current:
            return await ctx.reply(f"**{title_key}** has no aliases to remove.")
        to_remove = {a.strip() for a in aliases.split(",") if a.strip()}
        new_set = current - to_remove
        aliases_map[title_key] = sorted(new_set, key=str.lower)
        await self.engine.config.guild(ctx.guild).aliases.set(aliases_map)
        removed = len(current) - len(new_set)
        await ctx.reply(f"Removed {removed} alias(es) from **{title_key}**.\nNow: {', '.join(aliases_map[title_key]) if aliases_map[title_key] else '—'}")

    @opguess_char.command(name="aliasclear", aliases=["clearaliases"])
    @commands.admin()
    async def opguess_char_alias_clear(self, ctx: commands.Context, *, title: str):
        title_key = await self._resolve_title(ctx.guild, title.strip())
        aliases_map = await self.engine.config.guild(ctx.guild).aliases()
        had = len(aliases_map.get(title_key, []))
        if title_key in aliases_map:
            aliases_map[title_key] = []
            await self.engine.config.guild(ctx.guild).aliases.set(aliases_map)
        await ctx.reply(f"Cleared {had} alias(es) for **{title_key}**.")

    # ---- Teams (unchanged commands you already had) ----
    @opguess.group(name="teamapi", invoke_without_command=True)
    @commands.admin()
    async def opguess_teamapi(self, ctx: commands.Context):
        t = await self.engine.config.guild(ctx.guild).team_api()
        await ctx.reply(
            f"Teams is **{'ON' if t.get('enabled') else 'OFF'}** | mode: `{t.get('mode') or 'teamscog'}` | "
            f"win_pts: **{int(t.get('win_points') or 0)}** | timeout_pts: **{int(t.get('timeout_points') or 0)}**"
        )

    @opguess_teamapi.command(name="toggle")
    @commands.admin()
    async def opguess_teamapi_toggle(self, ctx: commands.Context, on_off: Optional[bool] = None):
        t = await self.engine.config.guild(ctx.guild).team_api()
        new = (not bool(t.get("enabled"))) if on_off is None else bool(on_off)
        t["enabled"] = new
        t["mode"] = t.get("mode") or "teamscog"
        await self.engine.config.guild(ctx.guild).team_api.set(t)
        await ctx.reply(f"Teams integration **{'enabled' if new else 'disabled'}** (mode: {t['mode']}).")

    @opguess_teamapi.command(name="mode")
    @commands.admin()
    async def opguess_teamapi_mode(self, ctx: commands.Context, mode: str):
        mode = mode.lower().strip()
        if mode not in {"teamscog", "http"}:
            return await ctx.reply("Mode must be `teamscog` or `http`.")
        t = await self.engine.config.guild(ctx.guild).team_api()
        t["mode"] = mode
        await self.engine.config.guild(ctx.guild).team_api.set(t)
        await ctx.reply(f"Teams mode set to **{mode}**")

    @opguess_teamapi.command(name="setpoints")
    @commands.admin()
    async def opguess_teamapi_setpoints(self, ctx: commands.Context, win_points: int, timeout_points: int = 0):
        t = await self.engine.config.guild(ctx.guild).team_api()
        t["win_points"] = int(win_points)
        t["timeout_points"] = int(timeout_points)
        await self.engine.config.guild(ctx.guild).team_api.set(t)
        await ctx.reply(f"Set points — win: {int(win_points)}, timeout: {int(timeout_points)}")

    # ---- reveal ----
    @opguess.command(name="reveal")
    @commands.admin()
    async def opguess_reveal(self, ctx: commands.Context, visibility: Optional[str] = "dm", end: Optional[bool] = False):
        active = await self.engine.get_active(ctx.guild)
        title = (active or {}).get("title")
        if not title:
            return await ctx.reply("No active round to reveal.")
        file = None
        try:
            _t, _extract, image_url = await self.engine.fetch_page_brief(title)
            if image_url:
                async with aiohttp.ClientSession() as s:
                    async with s.get(image_url, timeout=12) as r:
                        if r.status == 200:
                            buf = BytesIO(await r.read()); buf.seek(0)
                            file = discord.File(buf, filename="opguess_answer.png")
        except Exception:
            file = None
        emb = discord.Embed(title="🔎 Current Answer", description=f"**{title}**", color=discord.Color.blurple())
        if file:
            emb.set_image(url="attachment://opguess_answer.png")
        vis = (visibility or "dm").lower()
        if vis in {"here", "channel", "public"}:
            await ctx.reply(embed=emb, file=file if file else discord.utils.MISSING)
        else:
            try:
                await ctx.author.send(embed=emb, file=file if file else discord.utils.MISSING)
                await ctx.reply("📬 Sent the current answer to your DMs.", allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden:
                return await ctx.reply("I couldn't DM you. Use `[p]opguess reveal here` to post it in this channel.")
        if end:
            await self.engine.set_expired(ctx.guild, True)

    # ---- posting: now uses per-category blur profile ----
    async def _post_once(self, guild):
        gconf = await self.engine.config.guild(guild).all()
        channel = guild.get_channel(int(gconf.get("channel_id") or 0))
        if not channel:
            return
        title = await self.engine.pick_random_title(guild)
        if not title:
            return
        ctitle, extract, image_url = await self.engine.fetch_page_brief(title)
        interval = int(gconf.get("interval") or 1800)
        roundtime = int(gconf.get("roundtime") or 120)

        emb = discord.Embed(
            title="🗺️ Guess the One Piece Character!",
            description="Reply with `.guess <name>` (prefix) or `/guess` if enabled.",
            color=COLOR_OK,
        )
        emb.set_footer(text=f"Timer: {interval}s • Round timeout: {roundtime}s")

        if gconf.get("hint_enabled"):
            try:
                custom = await self.engine.get_hint(guild, title)
            except Exception:
                custom = None
            text = custom if custom else extract
            if text:
                maxn = int(gconf.get("hint_max_chars") or 200)
                val = text if len(text) <= maxn else (text[:maxn] + "…")
                emb.add_field(name="Hint", value=val, inline=False)

        file = None
        if image_url:
            profile = await self.engine.get_blur_for_title(guild, title)
            mode = str(profile.get("mode") or "gaussian").lower()
            strength = int(profile.get("strength") or 8)
            bw = bool(profile.get("bw"))
            buf = await self.engine.make_blurred(image_url, mode=mode, strength=strength, bw=bw)
            if buf:
                file = discord.File(buf, filename="opguess_blur.png")
                emb.set_image(url="attachment://opguess_blur.png")

        message = await channel.send(embed=emb, file=file) if file else await channel.send(embed=emb)
        await self.engine.set_active(guild, title=title, message=message)
        await self.engine.set_expired(guild, False)

    # ---- player ----
    @commands.hybrid_command(name="guess")
    @commands.guild_only()
    async def guess(self, ctx: commands.Context, *, name: str):
        """Guess the character; wrong answers get a ❌ reaction."""
        ok, title = await self.engine.check_guess(ctx.guild, name)

        if not title:
            if getattr(ctx, "interaction", None):
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(
                        "No active round — wait for the next prompt.", ephemeral=True
                    )
            else:
                await ctx.reply("No active round — wait for the next prompt.", delete_after=5)
            return

        if not ok:
            if getattr(ctx, "message", None):
                try:
                    await ctx.message.add_reaction("❌")
                    return
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if getattr(ctx, "interaction", None) and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message("❌", ephemeral=True)
            else:
                await ctx.reply("❌", delete_after=2)
            return

        # Correct!
        await self.engine.set_expired(ctx.guild, True)

        # Stats & optional local reward
        u = self.engine.config.user(ctx.author)
        wins = await u.wins() or 0
        await u.wins.set(wins + 1)
        reward = await self.engine.config.guild(ctx.guild).reward()
        await self.engine.reward(ctx.author, reward)

        # Teams note (unchanged)
        award_note = ""
        try:
            tconf = await self.engine.config.guild(ctx.guild).team_api()
            if tconf.get("enabled"):
                win_pts = int(tconf.get("win_points") or 0)
                mode = (tconf.get("mode") or "teamscog").lower()
                if win_pts > 0:
                    if mode == "teamscog":
                        teams_cog = self.bot.get_cog("Teams")
                        if teams_cog:
                            team = next(
                                (t for t in teams_cog.teams.get(ctx.guild.id, {}).values() if ctx.author in t.members),
                                None,
                            )
                            if team:
                                manager = ctx.guild.me
                                try:
                                    await team.add_points(win_pts, ctx.author, manager)
                                    award_note = f" (**+{win_pts}** to **{team.display_name}**)"
                                except Exception:
                                    award_note = " (**team points failed to apply**)"
                            else:
                                award_note = " (**no team — no points awarded**)"
                        else:
                            award_note = " (**Teams cog not loaded**)"
                    else:
                        try:
                            await self.engine.team_api.send_win(ctx.guild, ctx.author, title)  # optional external
                            award_note = f" (**+{win_pts} team points**)"
                        except Exception:
                            award_note = " (**team API error**)"
        except Exception:
            pass

        # Unblurred reveal on correct guess
        file = None
        try:
            _ctitle, _extract, image_url = await self.engine.fetch_page_brief(title)
            if image_url:
                async with aiohttp.ClientSession() as s:
                    async with s.get(image_url, timeout=12) as r:
                        if r.status == 200:
                            buf = BytesIO(await r.read())
                            buf.seek(0)
                            file = discord.File(buf, filename="opguess_reveal.png")
        except Exception:
            file = None

        emb = discord.Embed(
            title="✅ Correct!",
            description=f"{ctx.author.mention} got it — **{title}**.{award_note}",
            color=COLOR_OK,
        )
        if file:
            emb.set_image(url="attachment://opguess_reveal.png")

        if getattr(ctx, "interaction", None) and not ctx.interaction.response.is_done():
            await ctx.interaction.response.send_message(
                embed=emb,
                file=file if file else discord.utils.MISSING,
                allowed_mentions=discord.AllowedMentions.none()
            )
        else:
            await ctx.reply(
                embed=emb,
                file=file if file else discord.utils.MISSING,
                allowed_mentions=discord.AllowedMentions.none()
            )


async def setup(bot: Red):
    cog = OnePieceGuess(bot)
    await bot.add_cog(cog)
