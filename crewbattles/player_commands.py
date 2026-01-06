import asyncio
import copy
import math
import random
import time

import discord
from redbot.core import commands

from .constants import BASE_HP, DEFAULT_USER
from .utils import format_duration


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
                "• Train: **`.cbtrain armament|observation|conqueror`**\n\n"
                "**Devil Fruits**\n"
                "• Shop: **`.cbshop`** (use the Type dropdown + pick a fruit + **Buy**)\n"
                "• (Fallback) Buy by name: **`.cbbuy <fruit name>`**\n"
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

    _SHOP_TYPES = {
        "paramecia": "paramecia",
        "zoan": "zoan",
        "ancient": "ancient zoan",
        "ancient zoan": "ancient zoan",
        "logia": "logia",
        "mythical": "mythical zoan",
        "mythic zoan": "mythical zoan",
        "mythical zoan": "mythical zoan",
    }

    def _norm_shop_type(self, t: str) -> str | None:
        key = " ".join((t or "").strip().lower().split())
        return self._SHOP_TYPES.get(key)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command(name="cbshop")
    async def cbshop(self, ctx: commands.Context, *, fruit_type: str = ""):
        """
        Usage:
          .cbshop                    -> all fruits (paged)
          .cbshop paramecia          -> filtered (paged)
          .cbshop zoan
          .cbshop ancient zoan
          .cbshop logia
          .cbshop mythical zoan
        """
        raw = (fruit_type or "").strip()
        initial_type = self._norm_shop_type(raw) if raw else "all"

        TYPE_OPTIONS: list[tuple[str, str]] = [
            ("all", "All"),
            ("paramecia", "Paramecia"),
            ("zoan", "Zoan"),
            ("ancient zoan", "Ancient Zoan"),
            ("logia", "Logia"),
            ("mythical zoan", "Mythical Zoan"),
        ]

        def norm_item_type(x: dict) -> str:
            return self._norm_shop_type(x.get("type", "")) or "paramecia"

        def get_items(type_key: str) -> list[dict]:
            items = self.fruits.all() or []
            if type_key and type_key != "all":
                items = [f for f in items if norm_item_type(f) == type_key]
            # ascending order by price, then name
            items.sort(key=lambda f: (int(f.get("price", 0) or 0), (f.get("name") or "").lower()))
            return items

        # If the user passed an invalid type string, fall back to all.
        valid_type_keys = {k for k, _ in TYPE_OPTIONS}
        if initial_type not in valid_type_keys:
            initial_type = "all"

        # Empty shop guard
        if not (self.fruits.all() or []):
            return await ctx.send("Shop is empty.")

        per = 10

        def build_embed(*, type_key: str, page: int) -> discord.Embed:
            items = get_items(type_key)
            pages = max(1, math.ceil(len(items) / per))
            page = max(1, min(int(page), pages))
            start = (page - 1) * per
            chunk = items[start : start + per]

            title = "🛒 Devil Fruit Shop"
            if type_key != "all":
                title += f" • {type_key.title()}"
            e = discord.Embed(title=title, color=discord.Color.gold())

            lines: list[str] = []
            for f in chunk:
                name = str(f.get("name", "Unknown") or "Unknown")
                bonus = int(f.get("bonus", 0) or 0)
                price = int(f.get("price", 0) or 0)
                ability = f.get("ability") or "None"
                stock = f.get("stock", None)
                stock_txt = "∞" if stock is None else str(stock)

                # compact + phone-friendly
                lines.append(f"**{name}** `+{bonus}` • `{price:,}` • Stock `{stock_txt}`\n*{ability}*")

            if not lines:
                e.description = "—"
            else:
                e.description = "\n\n".join(lines)

            e.set_footer(text=f"Page {page}/{pages} • Select a type, pick a fruit, then press Buy")
            return e

        async def do_buy(*, buyer: discord.Member, fruit_name: str) -> tuple[bool, str]:
            p = await self.players.get(buyer)
            if not p.get("started"):
                return False, "You must `.startcb` first."
            if p.get("fruit"):
                return False, "You already have a fruit. Remove it first with `.cbremovefruit`."

            fruit = self.fruits.get(fruit_name)
            if not fruit:
                return False, "That fruit is not stocked in the shop."

            stock = fruit.get("stock", None)
            if stock is not None and int(stock) <= 0:
                return False, "That fruit is out of stock."

            price = int(fruit.get("price", 0) or 0)
            ok = await self._spend_money(buyer, price, reason="crew_battles:buy_fruit")
            if not ok:
                bal = await self._get_money(buyer)
                return False, f"Not enough Beri. Cost `{price:,}`, you have `{bal:,}`."

            p["fruit"] = fruit.get("name")
            await self.players.save(buyer, p)

            if stock is not None:
                fruit["stock"] = max(0, int(stock) - 1)
                self.fruits.update(fruit)

            return True, f"✅ Bought **{fruit['name']}** for `{price:,}` Beri."

        class _ShopView(discord.ui.View):
            def __init__(self, *, author_id: int, type_key: str, page: int):
                super().__init__(timeout=90)
                self.author_id = author_id
                self.type_key = type_key
                self.page = page
                self.selected_name: str | None = None
                self._msg: discord.Message | None = None
                self._sync_components()

            def _pages(self) -> int:
                items = get_items(self.type_key)
                return max(1, math.ceil(len(items) / per))

            def _page_items(self) -> list[dict]:
                items = get_items(self.type_key)
                pages = max(1, math.ceil(len(items) / per))
                self.page = max(1, min(int(self.page), pages))
                start = (self.page - 1) * per
                return items[start : start + per]

            def _sync_components(self):
                pages = self._pages()
                self.prev_btn.disabled = self.page <= 1
                self.next_btn.disabled = self.page >= pages

                # Update type select defaults
                for opt in self.type_select.options:
                    opt.default = opt.value == self.type_key

                # Update fruit select options for current page
                chunk = self._page_items()
                opts: list[discord.SelectOption] = []
                for f in chunk[:25]:
                    name = str(f.get("name", "Unknown") or "Unknown")
                    price = int(f.get("price", 0) or 0)
                    stock = f.get("stock", None)
                    stock_txt = "∞" if stock is None else str(stock)
                    label = name if len(name) <= 100 else (name[:99] + "…")
                    desc = f"{price:,} • Stock {stock_txt}"
                    opts.append(discord.SelectOption(label=label, value=name, description=desc[:100]))

                self.fruit_select.options = opts
                if self.selected_name and all(o.value != self.selected_name for o in opts):
                    self.selected_name = None

                self.buy_btn.disabled = not bool(self.selected_name)

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user is not None and interaction.user.id == self.author_id

            async def on_timeout(self) -> None:
                for child in self.children:
                    if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                        child.disabled = True
                if self._msg:
                    try:
                        await self._msg.edit(view=self)
                    except Exception:
                        pass

            @discord.ui.select(
                placeholder="Type…",
                options=[discord.SelectOption(label=label, value=value) for value, label in TYPE_OPTIONS],
            )
            async def type_select(self, interaction: discord.Interaction, select: discord.ui.Select):
                self.type_key = select.values[0]
                self.page = 1
                self.selected_name = None
                self._sync_components()
                await interaction.response.edit_message(
                    embed=build_embed(type_key=self.type_key, page=self.page),
                    view=self,
                )

            @discord.ui.select(placeholder="Pick a fruit…", options=[])
            async def fruit_select(self, interaction: discord.Interaction, select: discord.ui.Select):
                self.selected_name = select.values[0] if select.values else None
                self._sync_components()
                await interaction.response.edit_message(
                    embed=build_embed(type_key=self.type_key, page=self.page),
                    view=self,
                )

            @discord.ui.button(label="Buy", style=discord.ButtonStyle.success)
            async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not self.selected_name:
                    return await interaction.response.send_message("Pick a fruit first.", ephemeral=True)
                ok, msg_text = await do_buy(buyer=interaction.user, fruit_name=self.selected_name)
                # Refresh list (stock may change)
                self._sync_components()
                try:
                    await interaction.response.edit_message(
                        embed=build_embed(type_key=self.type_key, page=self.page),
                        view=self,
                    )
                except Exception:
                    pass
                # Confirmation
                try:
                    await interaction.followup.send(msg_text, ephemeral=True)
                except Exception:
                    # fallback if followup fails
                    if not ok:
                        pass

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page = max(1, self.page - 1)
                self.selected_name = None
                self._sync_components()
                await interaction.response.edit_message(
                    embed=build_embed(type_key=self.type_key, page=self.page),
                    view=self,
                )

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page = min(self._pages(), self.page + 1)
                self.selected_name = None
                self._sync_components()
                await interaction.response.edit_message(
                    embed=build_embed(type_key=self.type_key, page=self.page),
                    view=self,
                )

        view = _ShopView(author_id=ctx.author.id, type_key=initial_type, page=1)
        # initial sync after components exist
        view._sync_components()
        msg = await ctx.send(embed=build_embed(type_key=view.type_key, page=view.page), view=view)
        view._msg = msg

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

        embed.set_footer(text="Train: .cbtrain armament|observation|conqueror")
        return await ctx.reply(embed=embed)

    @commands.command(name="cbunlockconqueror", aliases=["unlockconqueror", "cbunlockconq", "cbunlockconquerors"])
    async def cbunlockconqueror(self, ctx: commands.Context):
        """Unlock Conqueror's Haki (requires level 10)."""
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must `.startcb` first.")

        level = int(p.get("level", 1) or 1)
        if level < 10:
            return await ctx.reply("👑 Conqueror is unlocked at **Level 10**.")

        haki = p.get("haki", {}) or {}
        if bool(haki.get("conquerors")):
            return await ctx.reply("👑 You already unlocked Conqueror's Haki.")

        g = await self.config.guild(ctx.guild).all()
        cost = int(g.get("conqueror_unlock_cost", 5000) or 5000)
        if cost < 0:
            cost = 0

        if cost > 0:
            ok = await self._spend_money(ctx.author, cost, reason="crew_battles:unlock_conqueror")
            if not ok:
                bal = await self._get_money(ctx.author)
                return await ctx.reply(f"Not enough Beri. Cost `{cost:,}`, you have `{bal:,}`.")

        haki["conquerors"] = True
        haki["conqueror"] = int(haki.get("conqueror", 0) or 0)
        p["haki"] = haki
        await self.players.save(ctx.author, p)

        e = discord.Embed(
            title="⚡👑 Conqueror's Haki Awakened! 👑⚡",
            description=f"{ctx.author.mention} has unlocked **Conqueror's Haki**!\n\n⚡ The air crackles with lightning…",
            color=discord.Color.purple(),
        )
        try:
            e.set_thumbnail(url=ctx.author.display_avatar.url)
        except Exception:
            pass
        if cost > 0:
            e.add_field(name="Cost", value=f"`{cost:,}` Beri", inline=True)
        e.add_field(name="Next", value="Train it with **`.cbtrain conqueror`**", inline=False)
        return await ctx.send(embed=e)

    @commands.command(name="cbtrainhaki")
    async def cbtrainhaki(self, ctx: commands.Context, haki_type: str, *rest: str):
        haki_type = (haki_type or "").lower().strip()
        if haki_type in {"menu", "ui", "select"}:
            return await self._cbtrain_menu(ctx)

        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must `.startcb` first.")

        if haki_type in ("conq", "conquerors"):
            haki_type = "conqueror"
        if haki_type not in ("armament", "observation", "conqueror"):
            return await ctx.reply("Type must be: `armament`, `observation`, or `conqueror`.")

        # 1 point per train (extra args ignored for back-compat)
        points = 1

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
            return await ctx.reply(f"⏳ Wait `{format_duration(rem)}` before training **{haki_type}** again.")

        # per-type cost (falls back to haki_cost)
        base_cost = int(g.get("haki_cost", 500) or 500)
        type_cost_key = {
            "armament": "haki_cost_armament",
            "observation": "haki_cost_observation",
            "conqueror": "haki_cost_conqueror",
        }.get(haki_type, "haki_cost")
        raw_cost = g.get(type_cost_key, None)
        cost_per = base_cost if raw_cost is None else int(raw_cost or 0)
        total_cost = max(0, cost_per)
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
    async def cbtrain(self, ctx: commands.Context, haki_type: str = None, *rest: str):
        """Train haki.

        Usage:
          .cbtrain                 -> interactive menu (pick 1/2/3 trainings)
          .cbtrain menu            -> interactive menu
          .cbtrain armament        -> legacy single-train
          .cbtrain observation
          .cbtrain conqueror
        """
        if not haki_type or not str(haki_type).strip():
            return await self._cbtrain_menu(ctx)
        return await self.cbtrainhaki(ctx, str(haki_type), *rest)

    async def _cbtrain_menu(self, ctx: commands.Context):
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("You must `.startcb` first.")

        haki = p.get("haki", {}) or {}
        g = await self.config.guild(ctx.guild).all()

        base_cost = int(g.get("haki_cost", 500) or 500)
        cooldown = int(g.get("haki_cooldown", 60 * 60) or 60 * 60)
        if cooldown < 0:
            cooldown = 0

        def cost_for(haki_type: str) -> int:
            type_cost_key = {
                "armament": "haki_cost_armament",
                "observation": "haki_cost_observation",
                "conqueror": "haki_cost_conqueror",
            }.get(haki_type, "haki_cost")
            raw_cost = g.get(type_cost_key, None)
            cost_per = base_cost if raw_cost is None else int(raw_cost or 0)
            return max(0, int(cost_per))

        def get_ts_map(player: dict) -> dict:
            ts = player.get("haki_train_ts") or {}
            return ts if isinstance(ts, dict) else {}

        def can_train(player: dict, haki_type: str, now: int) -> tuple[bool, str]:
            hk = player.get("haki", {}) or {}
            if haki_type == "conqueror" and not bool(hk.get("conquerors")):
                return False, "Locked"
            key = "conqueror" if haki_type == "conqueror" else haki_type
            cur = int(hk.get(key, 0) or 0)
            if cur >= 100:
                return False, "Maxed"

            ts_map = get_ts_map(player)
            last = int(ts_map.get(haki_type, 0) or 0)
            rem = (last + cooldown) - now
            if rem > 0:
                return False, f"CD {format_duration(rem)}"
            return True, "OK"

        def build_embed(*, selected: set[str]) -> discord.Embed:
            now = self._now()
            hk = p.get("haki", {}) or {}
            arm = int(hk.get("armament", 0) or 0)
            obs = int(hk.get("observation", 0) or 0)
            conq_unlocked = bool(hk.get("conquerors"))
            conq_lvl = int(hk.get("conqueror", 0) or 0)

            e = discord.Embed(title="🌊 Train Haki", color=discord.Color.purple())
            e.description = (
                "Pick 1–3 types to train (+1 each).\n"
                "This keeps the same cooldown/cost rules as `.cbtrain armament|...`."
            )

            lines: list[str] = []
            for t, label in (
                ("armament", "🛡️ Armament"),
                ("observation", "👁️ Observation"),
                ("conqueror", "👑 Conqueror"),
            ):
                ok, status = can_train(p, t, now)
                cost = cost_for(t)
                is_sel = t in selected
                cur = arm if t == "armament" else obs if t == "observation" else conq_lvl
                lock_note = "" if (t != "conqueror" or conq_unlocked) else " (locked)"
                mark = "✅" if is_sel else "▫️"
                state = status if ok else status
                lines.append(f"{mark} {label}{lock_note}: `{cur}/100` • `{cost:,}` • {state}")

            total = sum(cost_for(t) for t in selected)
            if total < 0:
                total = 0
            e.add_field(name="Options", value="\n".join(lines), inline=False)
            e.add_field(name="Total cost", value=f"`{total:,}` Beri", inline=False)
            e.set_footer(text="Use the buttons to select, then Train")
            return e

        class _HakiTrainView(discord.ui.View):
            def __init__(self, *, author_id: int):
                super().__init__(timeout=90)
                self.author_id = author_id
                self.selected: set[str] = set()
                self._msg: discord.Message | None = None
                self._sync_buttons()

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user is not None and interaction.user.id == self.author_id

            def _sync_buttons(self):
                now = self._outer._now()  # type: ignore
                # disable options that cannot be trained
                for t, btn in (
                    ("armament", self.arm_btn),
                    ("observation", self.obs_btn),
                    ("conqueror", self.conq_btn),
                ):
                    ok, _ = can_train(p, t, now)
                    btn.disabled = not ok
                    btn.style = discord.ButtonStyle.success if t in self.selected else discord.ButtonStyle.secondary
                self.train_btn.disabled = len(self.selected) == 0

            async def on_timeout(self) -> None:
                for child in self.children:
                    if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                        child.disabled = True
                if self._msg:
                    try:
                        await self._msg.edit(view=self)
                    except Exception:
                        pass

            def _toggle(self, t: str):
                if t in self.selected:
                    self.selected.remove(t)
                else:
                    self.selected.add(t)

            @discord.ui.button(label="Armament", style=discord.ButtonStyle.secondary)
            async def arm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self._toggle("armament")
                self._sync_buttons()
                await interaction.response.edit_message(embed=build_embed(selected=self.selected), view=self)

            @discord.ui.button(label="Observation", style=discord.ButtonStyle.secondary)
            async def obs_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self._toggle("observation")
                self._sync_buttons()
                await interaction.response.edit_message(embed=build_embed(selected=self.selected), view=self)

            @discord.ui.button(label="Conqueror", style=discord.ButtonStyle.secondary)
            async def conq_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self._toggle("conqueror")
                self._sync_buttons()
                await interaction.response.edit_message(embed=build_embed(selected=self.selected), view=self)

            @discord.ui.button(label="Train", style=discord.ButtonStyle.primary)
            async def train_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                if not self.selected:
                    return await interaction.response.send_message("Pick at least one type.", ephemeral=True)

                now = self._outer._now()  # type: ignore

                # Re-validate eligibility at click time.
                blocked = []
                for t in sorted(self.selected):
                    ok, status = can_train(p, t, now)
                    if not ok:
                        blocked.append(f"{t}: {status}")
                if blocked:
                    self.selected = {t for t in self.selected if can_train(p, t, now)[0]}
                    self._sync_buttons()
                    await interaction.response.edit_message(embed=build_embed(selected=self.selected), view=self)
                    return await interaction.followup.send(
                        "Some selections can't be trained right now:\n" + "\n".join(blocked),
                        ephemeral=True,
                    )

                total_cost = sum(cost_for(t) for t in self.selected)
                ok = await self._outer._spend_money(interaction.user, total_cost, reason="crew_battles:train_haki")  # type: ignore
                if not ok:
                    bal = await self._outer._get_money(interaction.user)  # type: ignore
                    return await interaction.response.send_message(
                        f"Not enough Beri. Cost `{total_cost:,}`, you have `{bal:,}`.",
                        ephemeral=True,
                    )

                # Apply training
                hk = p.get("haki", {}) or {}
                ts_map = get_ts_map(p)

                results = []
                for t in sorted(self.selected):
                    key = "conqueror" if t == "conqueror" else t
                    cur = int(hk.get(key, 0) or 0)
                    new = min(100, cur + 1)
                    hk[key] = new
                    if t == "conqueror":
                        hk["conquerors"] = True
                    ts_map[t] = now
                    results.append(f"**{t}** `{cur}` → `{new}`")

                p["haki"] = hk
                p["haki_train_ts"] = ts_map
                await self._outer.players.save(interaction.user, p)  # type: ignore

                # Disable view after training (cooldowns start now)
                for child in self.children:
                    if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                        child.disabled = True
                await interaction.response.edit_message(embed=build_embed(selected=set()), view=self)
                try:
                    await interaction.followup.send(
                        "✅ Trained:\n" + "\n".join(results) + f"\nSpent `{total_cost:,}` Beri.",
                        ephemeral=True,
                    )
                except Exception:
                    pass

        # Bind outer self so inner view can call mixin helpers.
        _HakiTrainView._outer = self  # type: ignore

        view = _HakiTrainView(author_id=ctx.author.id)
        msg = await ctx.send(embed=build_embed(selected=set()), view=view)
        view._msg = msg

    @commands.command(name="cbleaderboard", aliases=["cblb", "cbtop"])
    async def cbleaderboard(self, ctx: commands.Context, *args: str):
        """
        Usage:
          .cbleaderboard
          .cbleaderboard wins|level|winrate
          (optional legacy) .cbleaderboard <page> [wins|level|winrate]

        Paging is primarily done with buttons.
        """
        # Parse args safely (no int converter errors)
        sort_by = "wins"
        start_page = 1

        parts = [a.strip().lower() for a in (args or []) if a and a.strip()]
        if parts:
            if parts[0].isdigit():
                start_page = max(1, int(parts[0]))
                if len(parts) > 1:
                    sort_by = parts[1]
            else:
                sort_by = parts[0]

        if sort_by not in ("wins", "level", "winrate"):
            sort_by = "wins"

        all_users = await self.players.all(ctx.guild)
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

            entries.append(
                {
                    "uid": uid_int,
                    "wins": wins,
                    "losses": losses,
                    "level": lvl,
                    "exp": exp,
                    "winrate": winrate,
                    "total": total,
                }
            )

        if not entries:
            return await ctx.reply("No players found yet. Use `.startcb` to begin.")

        def sort_entries(mode: str):
            if mode == "level":
                entries.sort(key=lambda x: (x["level"], x["exp"], x["wins"]), reverse=True)
            elif mode == "winrate":
                entries.sort(key=lambda x: (x["total"] > 0, x["winrate"], x["wins"], x["level"]), reverse=True)
            else:
                entries.sort(key=lambda x: (x["wins"], x["winrate"], x["level"]), reverse=True)

        sort_entries(sort_by)

        per = 10
        pages = max(1, math.ceil(len(entries) / per))
        start_page = max(1, min(start_page, pages))

        def disp_name(uid: int) -> str:
            m = ctx.guild.get_member(uid) if ctx.guild else None
            name = m.display_name if m else f"User {uid}"

            # Keep names safe + compact for embeds (mobile-friendly wrapping)
            name = name.replace("`", "'").replace("\n", " ").strip()
            try:
                name = discord.utils.escape_markdown(name)
            except Exception:
                pass
            return (name[:28] + "…") if len(name) > 29 else name

        def build_embed(page: int, mode: str) -> discord.Embed:
            page = max(1, min(int(page), pages))
            start = (page - 1) * per
            chunk = entries[start : start + per]

            def _rank_icon(rank: int) -> str:
                if rank == 1:
                    return "🥇"
                if rank == 2:
                    return "🥈"
                if rank == 3:
                    return "🥉"
                return f"#{rank}"

            lines: list[str] = []
            for idx, row in enumerate(chunk, start=start + 1):
                name = disp_name(row["uid"])
                wr = float(row.get("winrate", 0.0) or 0.0)
                lines.append(
                    f"{_rank_icon(idx)} **{name}** — Lv **{row['level']}** • W **{row['wins']}** / L **{row['losses']}** • WR **{wr:.0f}%**"
                )

            desc = "\n".join(lines) if lines else "—"
            e = discord.Embed(
                title="🏆 Crew Battles Leaderboard",
                description=desc,
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow(),
            )

            sort_label = {"wins": "Wins", "level": "Level", "winrate": "Winrate"}.get(mode, str(mode))
            e.set_footer(text=f"Sorted by {sort_label} • Page {page}/{pages} • Players: {len(entries)}")
            return e

        if pages == 1:
            return await ctx.send(embed=build_embed(1, sort_by))

        class _LeaderboardPager(discord.ui.View):
            def __init__(self, *, author_id: int, mode: str, page: int):
                super().__init__(timeout=60)
                self.author_id = author_id
                self.mode = mode
                self.current = page
                self._msg: discord.Message | None = None
                self._sync()

            def _sync(self):
                self.prev_btn.disabled = self.current <= 1
                self.next_btn.disabled = self.current >= pages

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user is not None and interaction.user.id == self.author_id

            async def on_timeout(self) -> None:
                for child in self.children:
                    if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                        child.disabled = True
                if self._msg:
                    try:
                        await self._msg.edit(view=self)
                    except Exception:
                        pass

            @discord.ui.select(
                placeholder="Sort by…",
                options=[
                    discord.SelectOption(label="Wins", value="wins"),
                    discord.SelectOption(label="Level", value="level"),
                    discord.SelectOption(label="Winrate", value="winrate"),
                ],
            )
            async def sort_select(self, interaction: discord.Interaction, select: discord.ui.Select):
                self.mode = select.values[0]
                sort_entries(self.mode)
                self.current = 1
                self._sync()
                await interaction.response.edit_message(embed=build_embed(self.current, self.mode), view=self)

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.current = max(1, self.current - 1)
                self._sync()
                await interaction.response.edit_message(embed=build_embed(self.current, self.mode), view=self)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.current = min(pages, self.current + 1)
                self._sync()
                await interaction.response.edit_message(embed=build_embed(self.current, self.mode), view=self)

        view = _LeaderboardPager(author_id=ctx.author.id, mode=sort_by, page=start_page)
        msg = await ctx.send(embed=build_embed(start_page, sort_by), view=view)
        view._msg = msg

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