import asyncio
import copy
import math
import random
import time

import discord
from redbot.core import commands

from .constants import BASE_HP, DEFAULT_USER


class PlayerCommandsMixin:
    """
    Mixin expects the main cog to provide:
      - self.config
      - self.players (PlayerManager)
      - self.fruits (FruitManager)
      - self.teams (TeamsBridge)
      - simulate(...) + battle_embed(...) are imported/available in crewbattles.py and used there OR
        the main cog imports them and exposes them.
      - helpers: self._now(), self._apply_exp(), self._spend_money(), self._get_money(), self._add_beri(), self._team_of()
      - self._active_battles (set)
    """

    def _now(self) -> int:
        return int(time.time())

    # -----------------------------
    # Player commands
    # -----------------------------
    @commands.command(name="cbtutorial", aliases=["cbguide", "cbhelp"])
    async def cbtutorial(self, ctx: commands.Context):
        e = discord.Embed(
            title="📘 Crew Battles Tutorial",
            description=(
                "**Start**\n"
                "• Create your pirate record: **`.startcb`**\n"
                "• View your profile: **`.cbprofile`**\n\n"
                "**Battles**\n"
                "• Duel someone: **`.battle @user`**\n"
                "• Leaderboard: **`.cbleaderboard`**\n\n"
                "**Haki**\n"
                "• View Haki: **`.cbhaki`**\n"
                "• Train: **`.cbtrain armament|observation|conqueror [points]`**\n\n"
                "**Devil Fruits**\n"
                "• Shop: **`.cbshop`**\n"
                "• Buy: **`.cbbuy <fruit name>`**\n"
                "• Remove: **`.cbremovefruit`**\n"
                "• Abilities only trigger if you **own/equip** the fruit."
            ),
            color=discord.Color.blurple(),
        )
        try:
            e.set_thumbnail(url=ctx.author.display_avatar.url)
        except Exception:
            pass
        return await ctx.reply(embed=e)

    @commands.command(name="startcb")
    async def startcb(self, ctx: commands.Context):
        p = await self.players.get(ctx.author)
        if p.get("started"):
            return await ctx.reply("You already started. Use `.cbprofile`.")

        p = copy.deepcopy(DEFAULT_USER)
        p["started"] = True

        # starter fruit (5% chance, does not consume shop stock)
        fruit_name = None
        try:
            pool = self.fruits.pool_all() or []
            if pool and random.random() < 0.05:
                fruit_name = random.choice(pool).get("name")
        except Exception:
            fruit_name = None

        p["fruit"] = fruit_name
        await self.players.save(ctx.author, p)

        fruit_disp = fruit_name or "None"
        e = discord.Embed(
            title="🏴‍☠️ Crew Battles Activated!",
            description=(
                "Welcome aboard. Your pirate record has been created.\n\n"
                "**Next steps:**\n"
                "• 📘 **`.cbtutorial`**\n"
                "• 👤 **`.cbprofile`**\n"
                "• 🛒 **`.cbshop`**\n"
                "• ⚔️ **`.battle @user`**"
            ),
            color=discord.Color.blurple(),
        )
        e.add_field(
            name="🎒 Starting Loadout",
            value=f"🍈 **Fruit:** `{fruit_disp}`\n❤️ **Battle HP:** `{int(BASE_HP)}`",
            inline=False,
        )
        e.set_footer(text="Tip: Train Haki to improve crit/dodge/counter chances.")
        return await ctx.reply(embed=e)

    @commands.cooldown(1, 10, commands.BucketType.user)  # 1 use per 10s per user
    @commands.command(name="cbshop")
    async def cbshop(self, ctx: commands.Context, page: int = 1):
        items = self.fruits.all() or []
        if not items:
            return await ctx.send("Shop is empty. Admins can stock it with `.cbadmin fruits shopadd ...`")

        per = 10  # max 10 fruits per page
        pages = max(1, math.ceil(len(items) / per))
        page = max(1, min(int(page or 1), pages))

        def build_embed(p: int) -> discord.Embed:
            start = (p - 1) * per
            chunk = items[start : start + per]

            e = discord.Embed(title="🛒 Devil Fruit Shop", color=discord.Color.gold())
            lines = []
            for f in chunk:
                name = f.get("name", "Unknown")
                ftype = str(f.get("type", "unknown")).title()
                bonus = int(f.get("bonus", 0) or 0)
                price = int(f.get("price", 0) or 0)
                ability = f.get("ability") or "None"
                stock = f.get("stock", None)
                stock_txt = "∞" if stock is None else str(stock)
                lines.append(f"- **{name}** ({ftype}) `+{bonus}` | `{price:,}` | Stock: `{stock_txt}` | *{ability}*")

            e.description = "\n".join(lines) if lines else "—"
            e.set_footer(text=f"Page {p}/{pages} • Buy: .cbbuy <fruit name>")
            return e

        if pages == 1:
            return await ctx.send(embed=build_embed(page))

        class _ShopPager(discord.ui.View):
            def __init__(self, *, author_id: int, current: int):
                super().__init__(timeout=60)
                self.author_id = author_id
                self.current = current
                self._msg: discord.Message | None = None
                self._sync_buttons()

            def _sync_buttons(self):
                self.prev_btn.disabled = self.current <= 1
                self.next_btn.disabled = self.current >= pages

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                # only the command invoker can flip pages
                return interaction.user and interaction.user.id == self.author_id

            async def on_timeout(self) -> None:
                # disable buttons when timed out
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                try:
                    if self._msg:
                        await self._msg.edit(view=self)
                except Exception:
                    pass

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.current = max(1, self.current - 1)
                self._sync_buttons()
                await interaction.response.edit_message(embed=build_embed(self.current), view=self)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.current = min(pages, self.current + 1)
                self._sync_buttons()
                await interaction.response.edit_message(embed=build_embed(self.current), view=self)

        view = _ShopPager(author_id=ctx.author.id, current=page)
        msg = await ctx.send(embed=build_embed(page), view=view)  # was ctx.reply(...)
        view._msg = msg
        return

    @commands.command(name="cbbuy")
    async def cbbuy(self, ctx: commands.Context, *, fruit_name: str):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must `.startcb` first.")
        if p.get("fruit"):
            return await ctx.reply("You already have a fruit. Remove it first with `.cbremovefruit`.")

        fruit = self.fruits.get(fruit_name)
        if not fruit:
            return await ctx.reply("That fruit is not stocked in the shop.")

        stock = fruit.get("stock", None)
        if stock is not None and int(stock) <= 0:
            return await ctx.reply("That fruit is out of stock.")

        price = int(fruit.get("price", 0) or 0)
        ok = await self._spend_money(ctx.author, price, reason="crew_battles:buy_fruit")
        if not ok:
            bal = await self._get_money(ctx.author)
            return await ctx.reply(f"Not enough Beri. Cost `{price:,}`, you have `{bal:,}`.")

        p["fruit"] = fruit.get("name")
        await self.players.save(ctx.author, p)

        if stock is not None:
            fruit["stock"] = max(0, int(stock) - 1)
            self.fruits.update(fruit)

        return await ctx.reply(f"✅ Bought **{fruit['name']}** for `{price:,}` Beri.")

    @commands.command(name="cbremovefruit")
    async def cbremovefruit(self, ctx: commands.Context):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must `.startcb` first.")
        if not p.get("fruit"):
            return await ctx.reply("You don't have a fruit equipped.")

        g = await self.config.guild(ctx.guild).all()
        cost = int(g.get("remove_fruit_cost", 0) or 0)
        if cost > 0:
            ok = await self._spend_money(ctx.author, cost, reason="crew_battles:remove_fruit")
            if not ok:
                bal = await self._get_money(ctx.author)
                return await ctx.reply(f"Not enough Beri. Cost `{cost:,}`, you have `{bal:,}`.")

        old = p.get("fruit")
        p["fruit"] = None
        await self.players.save(ctx.author, p)
        return await ctx.reply(f"✅ Removed fruit: **{old}**")

    @commands.command(name="cbprofile")
    async def cbprofile(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        p = await self.players.get(member)
        if not p.get("started"):
            return await ctx.reply("That user has not started. Use `.startcb`.")

        wins = int(p.get("wins", 0) or 0)
        losses = int(p.get("losses", 0) or 0)
        total = wins + losses
        winrate = (wins / total * 100.0) if total else 0.0

        haki = p.get("haki", {}) or {}
        arm = int(haki.get("armament", 0) or 0)
        obs = int(haki.get("observation", 0) or 0)
        conq_unlocked = bool(haki.get("conquerors"))
        conq_lvl = int(haki.get("conqueror", 0) or 0)

        fruit_name = p.get("fruit") or "None"
        fruit_txt = fruit_name
        if p.get("fruit"):
            fd = self.fruits.get(p["fruit"]) or self.fruits.pool_get(p["fruit"])
            if isinstance(fd, dict):
                fruit_txt = f"{fd.get('name', fruit_name)} (`+{int(fd.get('bonus',0) or 0)}`) — *{fd.get('ability') or 'None'}*"

        conq_line = f"{conq_lvl}/100" if conq_unlocked else "Locked"

        embed = discord.Embed(
            title=f"🏴‍☠️ {member.display_name}'s Crew Profile",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass

        embed.add_field(
            name="📈 Progress",
            value=(
                f"Level: `{int(p.get('level', 1) or 1)}` | EXP: `{int(p.get('exp', 0) or 0)}`\n"
                f"Wins: `{wins}` | Losses: `{losses}` | Win Rate: `{winrate:.1f}%`"
            ),
            inline=False,
        )
        embed.add_field(name="🍈 Devil Fruit", value=fruit_txt, inline=False)
        embed.add_field(
            name="🌊 Haki",
            value=(
                f"Armament: `{arm}/100` (crit)\n"
                f"Observation: `{obs}/100` (dodge)\n"
                f"Conqueror: `{conq_line}`"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Battle HP is flat: {int(BASE_HP)}")
        return await ctx.reply(embed=embed)

    @commands.command(name="cbhaki")
    async def cbhaki(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        p = await self.players.get(member)
        if not p.get("started"):
            return await ctx.reply("That user has not started. Use `.startcb`.")

        haki = p.get("haki", {}) or {}
        arm = int(haki.get("armament", 0) or 0)
        obs = int(haki.get("observation", 0) or 0)
        conq = bool(haki.get("conquerors"))
        conq_lvl = int(haki.get("conqueror", 0) or 0)

        def bar(val: int, maxv: int = 100, width: int = 12) -> str:
            val = max(0, min(maxv, int(val)))
            filled = int(round((val / maxv) * width))
            return "🟦" * filled + "⬛" * (width - filled)

        embed = discord.Embed(title=f"🌊 Haki — {member.display_name}", color=discord.Color.purple())
        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass

        embed.add_field(name="🛡️ Armament (CRIT)", value=f"`{arm}/100`\n{bar(arm)}", inline=False)
        embed.add_field(name="👁️ Observation (DODGE)", value=f"`{obs}/100`\n{bar(obs)}", inline=False)
        if conq:
            embed.add_field(name="👑 Conqueror", value=f"`{conq_lvl}/100`", inline=False)
        else:
            embed.add_field(name="👑 Conqueror", value="Locked", inline=False)

        embed.set_footer(text="Train: .cbtrain armament|observation|conqueror [points]")
        return await ctx.reply(embed=embed)

    @commands.command(name="cbtrainhaki")
    async def cbtrainhaki(self, ctx: commands.Context, haki_type: str, points: int = 1):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must `.startcb` first.")

        haki_type = (haki_type or "").lower().strip()
        if haki_type in ("conq", "conquerors"):
            haki_type = "conqueror"
        if haki_type not in ("armament", "observation", "conqueror"):
            return await ctx.reply("Type must be: `armament`, `observation`, or `conqueror`.")

        points = int(points or 1)
        if points <= 0:
            return await ctx.reply("Points must be a positive number.")

        haki = p.get("haki", {}) or {}
        if haki_type == "conqueror" and not bool(haki.get("conquerors")):
            return await ctx.reply("👑 Conqueror is locked. Unlock it first.")

        now = self._now()
        ts_map = p.get("haki_train_ts") or {}
        if not isinstance(ts_map, dict):
            ts_map = {}

        g = await self.config.guild(ctx.guild).all()
        cooldown = int(g.get("haki_cooldown", 60 * 60) or 60 * 60)
        last = int(ts_map.get(haki_type, 0) or 0)
        rem = (last + cooldown) - now
        if rem > 0:
            return await ctx.reply(f"⏳ Wait `{rem}s` before training **{haki_type}** again.")

        cost_per = int(g.get("haki_cost", 500) or 500)
        total_cost = max(0, cost_per * points)
        ok = await self._spend_money(ctx.author, total_cost, reason="crew_battles:train_haki")
        if not ok:
            bal = await self._get_money(ctx.author)
            return await ctx.reply(f"Not enough Beri. Cost `{total_cost:,}`, you have `{bal:,}`.")

        key = "conqueror" if haki_type == "conqueror" else haki_type
        cur = int(haki.get(key, 0) or 0)
        new = min(100, cur + points)
        haki[key] = new
        if haki_type == "conqueror":
            haki["conquerors"] = True

        p["haki"] = haki
        ts_map[haki_type] = now
        p["haki_train_ts"] = ts_map
        await self.players.save(ctx.author, p)
        return await ctx.reply(f"✅ Trained **{haki_type}**: `{cur}` → `{new}` (spent `{total_cost:,}` Beri).")

    @commands.command(name="cbtrain")
    async def cbtrain(self, ctx: commands.Context, haki_type: str, points: int = 1):
        return await self.cbtrainhaki(ctx, haki_type, points)

    @commands.command(name="cbleaderboard", aliases=["cblb", "cbtop"])
    async def cbleaderboard(self, ctx: commands.Context, page: int = 1, sort_by: str = "wins"):
        sort_by = (sort_by or "wins").lower().strip()
        if sort_by not in ("wins", "level", "winrate"):
            sort_by = "wins"

        all_users = await self.players.all()
        entries = []
        for uid, pdata in (all_users or {}).items():
            if not isinstance(pdata, dict) or not pdata.get("started"):
                continue
            try:
                uid_int = int(uid)
            except Exception:
                continue
            wins = int(pdata.get("wins", 0) or 0)
            losses = int(pdata.get("losses", 0) or 0)
            lvl = int(pdata.get("level", 1) or 1)
            exp = int(pdata.get("exp", 0) or 0)
            total = wins + losses
            winrate = (wins / total * 100.0) if total else 0.0
            entries.append({"uid": uid_int, "wins": wins, "losses": losses, "level": lvl, "exp": exp, "winrate": winrate})

        if not entries:
            return await ctx.reply("No players found yet. Use `.startcb` to begin.")

        if sort_by == "level":
            entries.sort(key=lambda x: (x["level"], x["exp"], x["wins"]), reverse=True)
        elif sort_by == "winrate":
            entries.sort(key=lambda x: (x["winrate"], x["wins"], x["level"]), reverse=True)
        else:
            entries.sort(key=lambda x: (x["wins"], x["winrate"], x["level"]), reverse=True)

        per = 10
        page = max(1, int(page or 1))
        start = (page - 1) * per
        chunk = entries[start : start + per]
        if not chunk:
            return await ctx.reply("That page is empty.")

        e = discord.Embed(
            title="🏆 Crew Battles Leaderboard",
            description=f"Sorted by **{sort_by}** • Page **{page}**",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )

        lines = []
        for i, row in enumerate(chunk, start=start + 1):
            m = ctx.guild.get_member(row["uid"]) if ctx.guild else None
            name = m.display_name if m else f"User {row['uid']}"
            medal = "🥇 " if i == 1 else "🥈 " if i == 2 else "🥉 " if i == 3 else ""
            lines.append(
                f"{medal}`#{i:>2}` **{name}** — 🏅 Wins: `{row['wins']}` | ☠️ Losses: `{row['losses']}` | "
                f"📈 Lvl: `{row['level']}` | 🎯 WR: `{row['winrate']:.1f}%`"
            )

        e.add_field(name="Top Pirates", value="\n".join(lines), inline=False)
        e.set_footer(text="Use: .cbleaderboard <page> <wins|level|winrate>")
        return await ctx.reply(embed=e)

    @commands.is_owner()
    @commands.command(name="cbdebugbattle")
    async def cbdebugbattle(self, ctx: commands.Context, other: discord.Member):
        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(other)

        def fruit_info(p):
            fname = p.get("fruit")
            if not fname:
                return ("None", 0, "")
            f = self.fruits.get(fname) or self.fruits.pool_get(fname)
            if not isinstance(f, dict):
                return (str(fname), 0, "")
            return (f.get("name", fname), int(f.get("bonus", 0) or 0), str(f.get("ability", "") or ""))

        def haki_info(p):
            h = p.get("haki", {}) or {}
            return (
                int(h.get("armament", 0) or 0),
                int(h.get("observation", 0) or 0),
                bool(h.get("conquerors")),
                int(h.get("conqueror", 0) or 0),
            )

        f1 = fruit_info(p1)
        f2 = fruit_info(p2)
        h1 = haki_info(p1)
        h2 = haki_info(p2)

        return await ctx.reply(
            "**Battle inputs:**\n"
            f"**You:** fruit=`{f1[0]}` bonus=`{f1[1]}` ability=`{f1[2] or 'None'}` haki(A/O/Cunlock/Clvl)=`{h1}`\n"
            f"**Other:** fruit=`{f2[0]}` bonus=`{f2[1]}` ability=`{f2[2] or 'None'}` haki(A/O/Cunlock/Clvl)=`{h2}`"
        )