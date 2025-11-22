from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
from io import BytesIO
import aiohttp

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
            chars = await self.engine.config.guild(guild).characters()
            if not chars and SEED_FILE.exists():
                try:
                    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
                    if isinstance(seed, list) and seed:
                        await self.engine.config.guild(guild).characters.set([str(s) for s in seed])
                except Exception:
                    pass
            # migration safety: ensure keys exist
            g = await self.engine.config.guild(guild).all()
            blur = g.get("blur") or {}
            if "bw" not in blur:
                blur["bw"] = False
                await self.engine.config.guild(guild).blur.set(blur)
            active = g.get("active") or {}
            if "expired" not in active:
                active["expired"] = False
                await self.engine.config.guild(guild).active.set(active)

        await self.tasks.start()

    def cog_unload(self) -> None:
        self.tasks.cancel()

    def _core(self):
        return self.bot.get_cog("BeriCore")

    # --------- status ---------
    @commands.hybrid_group(name="opguess", invoke_without_command=True)
    @commands.guild_only()
    async def opguess(self, ctx: commands.Context):
        """OnePieceGuess: admin & status."""
        g = await self.engine.config.guild(ctx.guild).all()
        enabled = "ON" if g.get("enabled") else "OFF"
        ch = ctx.guild.get_channel(g.get("channel_id") or 0)
        blur = g.get("blur") or {}
        await ctx.reply(
            "Status: **{enabled}**\nChannel: {channel}\nInterval: **{interval}s**\nRound timeout: **{roundtime}s**\n"
            "Reward: **{reward}**\nBlur: **{mode}** @ **{strength}** • B/W: **{bw}**\n"
            "Hint: **{hint}** (max {maxc} chars)".format(
                enabled=enabled,
                channel=(ch.mention if ch else "—"),
                interval=g.get("interval"),
                roundtime=g.get("roundtime"),
                reward=g.get("reward"),
                mode=blur.get("mode", "gaussian"),
                strength=blur.get("strength", 8),
                bw=("ON" if blur.get("bw") else "OFF"),
                hint=("ON" if g.get("hint_enabled") else "OFF"),
                maxc=g.get("hint_max_chars"),
            ),
            allowed_mentions=discord.AllowedMentions.none()
        )

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
        seconds = max(30, min(24*3600, int(seconds)))
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
        await ctx.reply(f"Reward set to **{max(0,int(amount))}**")

    # ---- admin: blur, strength, black & white, hints ----
    @opguess.group(name="blur", invoke_without_command=True)
    @commands.admin()
    async def opguess_blur(self, ctx: commands.Context):
        blur = await self.engine.config.guild(ctx.guild).blur()
        await ctx.reply(
            f"Blur mode: **{blur.get('mode','gaussian')}** • strength: **{blur.get('strength',8)}** • B/W: **{'ON' if blur.get('bw') else 'OFF'}**"
        )

    @opguess_blur.command(name="mode")
    @commands.admin()
    async def opguess_blur_mode(self, ctx: commands.Context, mode: str):
        """Set blur mode: gaussian|pixelate"""
        mode = mode.lower().strip()
        if mode not in {"gaussian", "pixelate"}:
            return await ctx.reply("Mode must be `gaussian` or `pixelate`.")
        blur = await self.engine.config.guild(ctx.guild).blur()
        blur["mode"] = mode
        await self.engine.config.guild(ctx.guild).blur.set(blur)
        await ctx.reply(f"Blur mode set to **{mode}**")

    @opguess_blur.command(name="strength")
    @commands.admin()
    async def opguess_blur_strength(self, ctx: commands.Context, value: int):
        """Set blur radius (gaussian) or block size (pixelate). Max 250."""
        value = max(1, min(250, int(value)))
        blur = await self.engine.config.guild(ctx.guild).blur()
        blur["strength"] = value
        await self.engine.config.guild(ctx.guild).blur.set(blur)
        await ctx.reply(f"Blur strength set to **{value}**")

    @opguess_blur.command(name="bw")
    @commands.admin()
    async def opguess_blur_bw(self, ctx: commands.Context, on_off: Optional[bool] = None):
        """Toggle black & white mode for the image."""
        blur = await self.engine.config.guild(ctx.guild).blur()
        new = (not bool(blur.get("bw"))) if on_off is None else bool(on_off)
        blur["bw"] = new
        await self.engine.config.guild(ctx.guild).blur.set(blur)
        await ctx.reply(f"Black & white **{'ON' if new else 'OFF'}**")

    @opguess.command(name="hint")
    @commands.admin()
    async def opguess_hint(self, ctx: commands.Context, enabled: Optional[bool] = None, max_chars: Optional[int] = None):
        """Toggle text hint and/or set max characters (default 200)."""
        if enabled is not None:
            await self.engine.config.guild(ctx.guild).hint_enabled.set(bool(enabled))
        if max_chars is not None:
            await self.engine.config.guild(ctx.guild).hint_max_chars.set(max(50, min(1000, int(max_chars))))
        g = await self.engine.config.guild(ctx.guild).all()
        await ctx.reply(f"Hint **{'ON' if g.get('hint_enabled') else 'OFF'}**, max **{g.get('hint_max_chars')}** chars.")

    # ---- admin: characters & aliases ----
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

    # ---- admin: import/export ----
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
            await self.engine.config.guild(ctx.guild).characters.set(titles)
            await self.engine.config.guild(ctx.guild).aliases.set({str(k): [str(v) for v in vs] for k,vs in aliases.items()})
        else:
            return await ctx.reply("Unsupported JSON structure.")
        await ctx.reply(f"Imported **{len(await self.engine.config.guild(ctx.guild).characters())}** characters.")

    @opguess.command(name="export")
    @commands.admin()
    async def opguess_export(self, ctx: commands.Context):
        titles = await self.engine.config.guild(ctx.guild).characters()
        aliases = await self.engine.config.guild(ctx.guild).aliases()
        payload = {"characters": titles, "aliases": aliases}
        buf = json.dumps(payload, indent=2).encode("utf-8")
        await ctx.reply(file=discord.File(fp=buf, filename="onepiece_characters.json"))

    @opguess.command(name="forcepost")
    @commands.admin()
    async def opguess_forcepost(self, ctx: commands.Context):
        g = await self.engine.config.guild(ctx.guild).all()
        ch = ctx.guild.get_channel(g.get("channel_id") or 0)
        if not ch:
            return await ctx.reply("No channel configured. Use `opguess setchannel`.")
        await self.engine.set_expired(ctx.guild, True)  # mark old round done
        # quick single post using same logic as scheduler
        await self._post_once(ctx.guild)
        await ctx.reply("Round posted (if character pool is not empty).")

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
            color=COLOR_OK
        )
        emb.set_footer(text=f"Timer: {interval}s • Round timeout: {roundtime}s")

        if gconf.get("hint_enabled") and extract:
            maxn = int(gconf.get("hint_max_chars") or 200)
            val = extract if len(extract) <= maxn else (extract[:maxn] + '…')
            emb.add_field(name="Hint", value=val, inline=False)

        file = None
        if image_url:
            blur = gconf.get("blur") or {}
            mode = str(blur.get("mode") or "gaussian").lower()
            strength = int(blur.get("strength") or 8)
            bw = bool(blur.get("bw"))
            buf = await self.engine.make_blurred(image_url, mode=mode, strength=strength, bw=bw)
            if buf:
                file = discord.File(buf, filename="opguess_blur.png")
                emb.set_image(url="attachment://opguess_blur.png")

        message = await channel.send(embed=emb, file=file) if file else await channel.send(embed=emb)
        await self.engine.set_active(guild, title=title, message=message)
        await self.engine.set_expired(guild, False)

    @commands.hybrid_command(name="guess")
    @commands.guild_only()
    async def guess(self, ctx: commands.Context, *, name: str):
        """Guess the character name; typos/aliases accepted."""
        ok, title = await self.engine.check_guess(ctx.guild, name)
        if not title:
            return await ctx.reply("No active round — wait for the next prompt.")
        if not ok:
            # ❌ Wrong answer behavior:
            # - Prefix: react to the user's message.
            # - Slash: send an ephemeral red X since we can't react to an interaction.
            if getattr(ctx, "interaction", None):
                try:
                    if not ctx.interaction.response.is_done():
                        await ctx.interaction.response.send_message("❌", ephemeral=True)
                    else:
                        await ctx.interaction.followup.send("❌", ephemeral=True)
                except Exception:
                    pass
            else:
                try:
                    await ctx.message.add_reaction("❌")
                except Exception:
                    pass
            return

        # Correct guess: mark expired (keep cadence until next interval)
        await self.engine.set_expired(ctx.guild, True)

        # Stats & reward
        u = self.engine.config.user(ctx.author)
        wins = await u.wins() or 0
        await u.wins.set(wins + 1)
        reward = await self.engine.config.guild(ctx.guild).reward()
        await self.engine.reward(ctx.author, reward)

        # Fetch original (unblurred) image and attach on the "Correct!" embed
        file = None
        try:
            ctitle, _extract, image_url = await self.engine.fetch_page_brief(title)
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
            description=f"{ctx.author.mention} got it — **{title}**.",
            color=COLOR_OK
        )
        if file:
            emb.set_image(url="attachment://opguess_reveal.png")

        await ctx.reply(
            embed=emb,
            file=file if file else discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions.none()
        )

async def setup(bot: Red):
    cog = OnePieceGuess(bot)
    await bot.add_cog(cog)
