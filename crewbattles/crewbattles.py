import asyncio
import random
import time
import discord
import math
import copy
import json
from redbot.core import commands, Config

from .constants import DEFAULT_GUILD, DEFAULT_USER, BASE_HP
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .embeds import battle_embed


# Haki training configuration (cost in Beri per point, cooldown seconds)
HAKI_TRAIN_COST = 500
HAKI_TRAIN_COOLDOWN = 60 * 60  # 1 hour


class CrewBattles(commands.Cog):
    """Crew Battles – PvP battles with Devil Fruits, Teams & BeriCore integration"""

    def __init__(self, bot):
        self.bot = bot
        self.players = PlayerManager(self)
        self.fruits = FruitManager()
        self.teams = TeamsBridge(bot)

        self.config = Config.get_conf(
            self,
            identifier=444888221,
            force_registration=True
        )
        self.config.register_guild(**DEFAULT_GUILD)

        # track active battles by channel id to prevent more than one per channel
        self._active_battles = set()

    # =========================================================
    # INTERNAL HELPERS
    # =========================================================

    def _beri(self):
        """Safely fetch BeriCore."""
        return self.bot.get_cog("BeriCore")

    @commands.command()
    async def cbshop(self, ctx, page: int = 1):
        """Show the devil fruit shop (paginated). Use .cbshop <page> to navigate."""
        fruits = self.fruits.all()
        if not fruits:
            return await ctx.reply("🛒 The shop is currently empty.")

        per_page = 10  # number of fruits per embed page (keeps fields well under 25)
        total = len(fruits)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(total_pages, int(page or 1)))

        start = (page - 1) * per_page
        end = start + per_page
        slice_ = fruits[start:end]

        embed = discord.Embed(
            title=f"🍎 Devil Fruit Shop — Page {page}/{total_pages}",
            color=discord.Color.gold(),
            description=f"Showing {start + 1}-{min(end, total)} of {total} fruits. Use `.cbshop <page>` to view other pages."
        )

        for f in slice_:
            name = f.get("name", "Unknown Fruit")
            ftype = f.get("type", "Unknown")
            bonus = f.get("bonus", 0)
            price = f.get("price", 0)
            stock = f.get("stock", None)
            ability = f.get("ability", "") or "—"

            stock_text = "Unlimited" if stock is None else str(stock)
            value = (
                f"Type: {ftype}\n"
                f"Ability: {ability}\n"
                f"Bonus: {bonus}\n"
                f"Price: {price:,} Beri\n"
                f"Stock: {stock_text}"
            )
            embed.add_field(name=name, value=value, inline=False)

        await ctx.reply(embed=embed)

    @commands.command()
    async def cbbuy(self, ctx, *, fruit_name: str):
        """Buy a Devil Fruit"""
        p = await self.players.get(ctx.author)

        if p.get("fruit"):
            return await ctx.reply("❌ You already have a Devil Fruit. Remove it first.")

        fruit = self.fruits.get(fruit_name)
        if not fruit:
            return await ctx.reply("❌ That Devil Fruit does not exist.")

        stock = fruit.get("stock")
        if stock is not None and stock <= 0:
            return await ctx.reply("❌ That Devil Fruit is out of stock.")

        price = fruit.get("price", 25000)
        core = self._beri()
        if not core:
            return await ctx.reply("❌ Economy system unavailable.")

        # Decrement stock if applicable
        if fruit.get("stock") is not None:
            fruit["stock"] -= 1
            self.fruits.update(fruit)

        balance = await core.get_beri(ctx.author)
        if balance < price:
            return await ctx.reply(f"❌ You need **{price:,} Beri** to buy this fruit.")

        # Charge user
        await core.add_beri(
            ctx.author,
            -price,
            reason="shop:devil_fruit:purchase",
            bypass_cap=True,
        )

        p["fruit"] = fruit.get("name")
        await self.players.save(ctx.author, p)

        await ctx.reply(
            f"🍈 **{fruit['name']}** purchased successfully!\n"
            f"💰 Spent **{price:,} Beri**"
        )

    @commands.command()
    async def cbremovefruit(self, ctx):
        """Remove your Devil Fruit (costs 5,000 Beri)"""
        p = await self.players.get(ctx.author)

        if not p.get("fruit"):
            return await ctx.reply("❌ You do not have a Devil Fruit.")

        cost = 5000
        core = self._beri()
        if not core:
            return await ctx.reply("❌ Economy system unavailable.")

        balance = await core.get_beri(ctx.author)
        if balance < cost:
            return await ctx.reply(f"❌ You need **{cost:,} Beri** to remove your fruit.")

        await core.add_beri(
            ctx.author,
            -cost,
            reason="shop:devil_fruit:remove",
            bypass_cap=True,
        )

        old = p["fruit"]
        p["fruit"] = None
        await self.players.save(ctx.author, p)

        await ctx.reply(
            f"🗑️ Removed **{old}**\n"
            f"💰 Cost: **{cost:,} Beri**"
        )

    # =========================================================
    # ADMIN COMMANDS
    # =========================================================

    @commands.group()
    @commands.admin()
    async def cbadmin(self, ctx):
        """Crew Battles admin commands"""
        pass

    @cbadmin.command()
    async def setberi(self, ctx, win: int, loss: int = 0):
        """Set Beri rewards for wins & losses"""
        await self.config.guild(ctx.guild).beri_win.set(int(win))
        await self.config.guild(ctx.guild).beri_loss.set(int(loss))
        await ctx.reply(
            f"💰 **Beri rewards updated**\n"
            f"• Win: **{win}**\n"
            f"• Loss: **{loss}**"
        )

    @cbadmin.command()
    async def addfruit(self, ctx, name: str, ftype: str, bonus: int, price: int, stock: int = None):
        """
        Add a Devil Fruit to the shop
        stock = number, omit for unlimited
        """
        self.fruits.add(
            name=name,
            ftype=ftype,
            bonus=int(bonus),
            price=int(price),
            stock=stock,
        )

        await ctx.reply(
            f"🍈 Devil Fruit **{name}** added\n"
            f"• Type: {ftype}\n"
            f"• Bonus: +{bonus}\n"
            f"• Price: {price:,} Beri\n"
            f"• Stock: {'∞' if stock is None else stock}"
        )

    @cbadmin.command()
    async def setfruitstock(self, ctx, *, name: str, stock: int):
        fruit = self.fruits.get(name)
        if not fruit:
            return await ctx.reply("❌ Fruit not found.")

        fruit["stock"] = stock
        self.fruits.update(fruit)
        await ctx.reply(f"📦 Stock for **{name}** set to **{stock}**")

    @cbadmin.command()
    async def resetuser(self, ctx, member: discord.Member):
        """Reset a user's Crew Battles data to defaults."""
        await self.players.save(member, copy.deepcopy(DEFAULT_USER))
        await ctx.reply(f"✅ Reset Crew Battles data for {member.display_name}")

    @cbadmin.command()
    async def viewuser(self, ctx, member: discord.Member):
        """View a user's raw Crew Battles data."""
        p = await self.players.get(member)
        await ctx.reply(f"Data for {member.display_name}: ```py\n{p}\n```")

    @cbadmin.command()
    async def setlevel(self, ctx, member: discord.Member, level: int):
        """Set a user's level."""
        p = await self.players.get(member)
        p["level"] = max(1, int(level))
        await self.players.save(member, p)
        await ctx.reply(f"✅ Set {member.display_name}'s level to {p['level']}")

    @cbadmin.command()
    async def setexp(self, ctx, member: discord.Member, exp: int):
        """Set a user's EXP."""
        p = await self.players.get(member)
        p["exp"] = max(0, int(exp))
        await self.players.save(member, p)
        await ctx.reply(f"✅ Set {member.display_name}'s EXP to {p['exp']}")

    @cbadmin.command()
    async def setwins(self, ctx, member: discord.Member, wins: int):
        """Set a user's wins."""
        p = await self.players.get(member)
        p["wins"] = max(0, int(wins))
        await self.players.save(member, p)
        await ctx.reply(f"✅ Set {member.display_name}'s wins to {p['wins']}")

    @cbadmin.command()
    async def setlosses(self, ctx, member: discord.Member, losses: int):
        """Set a user's losses."""
        p = await self.players.get(member)
        p["losses"] = max(0, int(losses))
        await self.players.save(member, p)
        await ctx.reply(f"✅ Set {member.display_name}'s losses to {p['losses']}")

    @cbadmin.command()
    async def setstarted(self, ctx, member: discord.Member, started: bool):
        """Set whether a user has started Crew Battles."""
        p = await self.players.get(member)
        p["started"] = bool(started)
        await self.players.save(member, p)
        await ctx.reply(f"✅ Set {member.display_name} started={p['started']}")

    @cbadmin.command()
    async def givefruit(self, ctx, member: discord.Member, *, fruit_name: str):
        """Give a user a devil fruit (must exist in fruits)."""
        fruit = self.fruits.get(fruit_name)
        if not fruit:
            return await ctx.reply("❌ Fruit not found.")
        p = await self.players.get(member)
        p["fruit"] = fruit["name"]
        await self.players.save(member, p)
        await ctx.reply(f"✅ Gave **{fruit['name']}** to {member.display_name}")

    @cbadmin.command()
    async def removefruituser(self, ctx, member: discord.Member):
        """Remove a user's devil fruit."""
        p = await self.players.get(member)
        old = p.get("fruit")
        p["fruit"] = None
        await self.players.save(member, p)
        await ctx.reply(f"✅ Removed fruit ({old}) from {member.display_name}")

    @cbadmin.command()
    async def addberi(self, ctx, member: discord.Member, amount: int):
        """Give or remove Beri from a user (requires BeriCore)."""
        core = self._beri()
        if not core:
            return await ctx.reply("❌ Economy system unavailable.")
        try:
            await core.add_beri(member, int(amount), reason="admin:beri_adjust", bypass_cap=True)
        except Exception as e:
            return await ctx.reply(f"❌ Error adjusting Beri: {e}")
        await ctx.reply(f"✅ Adjusted Beri for {member.display_name} by {amount:,}")

    @cbadmin.command()
    async def sethaki(self, ctx, member: discord.Member, haki_type: str, value: str):
        """
        Set a user's haki.
        haki_type: armament | observation | conquerors
        value: number for armament/observation (0-100), true/false for conquerors
        """
        p = await self.players.get(member)
        haki = p.get("haki", {}) or {}
        haki_type = haki_type.lower()
        if haki_type in ("armament", "observation"):
            try:
                v = max(0, min(100, int(value)))
            except Exception:
                return await ctx.reply("❌ Value must be an integer 0-100.")
            haki[haki_type] = v
        elif haki_type in ("conquerors", "conqueror", "conqueror's"):
            haki["conquerors"] = str(value).lower() in ("1", "true", "yes", "on")
        else:
            return await ctx.reply("❌ Unknown haki type.")
        p["haki"] = haki
        await self.players.save(member, p)
        await ctx.reply(f"✅ Set {member.display_name}'s {haki_type} to {value}")

    @cbadmin.command()
    async def resethaki(self, ctx, member: discord.Member):
        """Reset a user's haki to defaults."""
        p = await self.players.get(member)
        p["haki"] = copy.deepcopy(DEFAULT_USER["haki"])
        await self.players.save(member, p)
        await ctx.reply(f"✅ Reset Haki for {member.display_name}")

    @cbadmin.command()
    async def unlockconqueror(self, ctx, member: discord.Member):
        """Force-unlock Conqueror's Haki for a user."""
        p = await self.players.get(member)
        haki = p.get("haki", {}) or {}
        haki["conquerors"] = True
        p["haki"] = haki
        await self.players.save(member, p)
        await ctx.reply(f"✅ Unlocked Conqueror's Haki for {member.display_name}")

    @cbadmin.command()
    async def setlast_haki_train(self, ctx, member: discord.Member, ts: int = 0):
        """Set a user's last_haki_train timestamp (0 to clear)."""
        p = await self.players.get(member)
        p["last_haki_train"] = int(ts)
        await self.players.save(member, p)
        await ctx.reply(f"✅ Set last_haki_train for {member.display_name} to {p['last_haki_train']}")

    @cbadmin.command()
    async def setturn_delay(self, ctx, delay: float):
        """Set guild turn delay (seconds)."""
        await self.config.guild(ctx.guild).turn_delay.set(float(delay))
        await ctx.reply(f"✅ Set turn delay to {delay}s")

    @cbadmin.command()
    async def sethakicost(self, ctx, cost: int):
        """Set Beri cost per Haki point for this guild."""
        await self.config.guild(ctx.guild).haki_cost.set(int(cost))
        await ctx.reply(f"✅ Set Haki training cost to {cost:,} Beri per point")

    @cbadmin.command()
    async def sethakicooldown(self, ctx, seconds: int):
        """Set Haki training cooldown (seconds) for this guild."""
        await self.config.guild(ctx.guild).haki_cooldown.set(int(seconds))
        await ctx.reply(f"✅ Set Haki training cooldown to {seconds} seconds")

    @cbadmin.command()
    async def importfruits(self, ctx, *, json_text: str = None):
        """
        Import shop stock from a JSON file or raw JSON text.
        Usage: attach a .json file to the command message, or pass raw JSON as an argument.
        The imported JSON must be a list of fruit objects with keys:
          name, type, bonus, price, ability, stock (optional)
        Import replaces the current shop entirely.
        """
        # prefer attachment if provided
        text = None
        if ctx.message.attachments:
            try:
                data = await ctx.message.attachments[0].read()
                text = data.decode("utf-8")
            except Exception as e:
                return await ctx.reply(f"❌ Failed to read attachment: {e}")
        elif json_text:
            text = json_text
        else:
            return await ctx.reply("❌ Provide a JSON attachment or raw JSON text.")

        try:
            parsed = json.loads(text)
        except Exception as e:
            return await ctx.reply(f"❌ Invalid JSON: {e}")

        try:
            count = self.fruits.import_json(parsed)
        except Exception as e:
            return await ctx.reply(f"❌ Import failed: {e}")

        await ctx.reply(f"✅ Imported {count} devil fruits; shop replaced.")

    # =========================================================
    # PLAYER COMMANDS
    # =========================================================

    @commands.command()
    async def startcb(self, ctx):
        p = await self.players.get(ctx.author)
        if p["started"]:
            return await ctx.reply("❌ You already started Crew Battles.")

        fruit = self.fruits.random()
        p["started"] = True
        p["fruit"] = fruit["name"] if fruit else None

        await self.players.save(ctx.author, p)

        embed = discord.Embed(
            title="🏴‍☠️ Journey Begun!",
            color=discord.Color.gold(),
            description=(
                f"**Level:** 1\n"
                f"**Devil Fruit:** {p['fruit'] or 'None'}\n\n"
                "You can now battle using `.battle @user`"
            ),
        )
        await ctx.reply(embed=embed)

    @commands.command(name="cbprofile")
    async def cbprofile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        p = await self.players.get(member)

        if not p["started"]:
            return await ctx.reply("❌ This player has not started Crew Battles yet.")

        wins = p.get("wins", 0)
        losses = p.get("losses", 0)
        total = wins + losses
        winrate = (wins / total * 100) if total else 0.0
        haki = p.get("haki", {})

        # small visual bar for haki values (0-100)
        def _bar(value, max_value=100, length=12):
            try:
                v = int(value)
            except Exception:
                v = 0
            v = max(0, min(v, max_value))
            filled = int(v / max_value * length) if max_value else 0
            return "█" * filled + "░" * (length - filled)

        # fruit display (include basic details if available)
        fruit_name = p.get("fruit") or "None"
        fruit_detail = None
        if p.get("fruit"):
            try:
                fruit_detail = self.fruits.get(fruit_name)
            except Exception:
                fruit_detail = None

        if fruit_detail:
            fruit_txt = f"{fruit_name} • {fruit_detail.get('type','').title()} • +{fruit_detail.get('bonus',0)}"
        else:
            fruit_txt = fruit_name

        # build embed
        embed = discord.Embed(
            title=f"🏴‍☠️ {member.display_name}'s Crew Profile",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )

        # thumbnail (compatible with different discord.py versions)
        try:
            avatar_url = member.display_avatar.url
        except Exception:
            avatar_url = getattr(member, "avatar_url", None)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.add_field(
            name="📊 Stats",
            value=(
                f"**Level:** {p.get('level', 1)}  •  **EXP:** {p.get('exp', 0)}\n"
                f"**Wins:** {wins}  •  **Losses:** {losses}  •  **Win Rate:** {winrate:.1f}%"
            ),
            inline=False,
        )

        embed.add_field(
            name="🍈 Devil Fruit",
            value=fruit_txt,
            inline=False,
        )

        arm = haki.get("armament", 0)
        obs = haki.get("observation", 0)
        conquer = "Unlocked ✅" if haki.get("conquerors") else "Locked ❌"

        embed.add_field(
            name="✨ Haki",
            value=(
                f"🛡 Armament: {arm} {_bar(arm)}\n"
                f"👁 Observation: {obs} {_bar(obs)}\n"
                f"👑 Conqueror’s: {conquer}"
            ),
            inline=False,
        )

        embed.set_footer(text="Crew Battles • Progress is saved")
        await ctx.reply(embed=embed)

    @commands.command()
    async def cbhaki(self, ctx, member: discord.Member = None):
        """View a member's Haki stats"""
        member = member or ctx.author
        p = await self.players.get(member)
        if not p.get("started"):
            return await ctx.reply("❌ This player has not started Crew Battles yet.")
        haki = p.get("haki", {}) or {}
        arm = int(haki.get("armament", 0))
        obs = int(haki.get("observation", 0))
        conq_unlocked = bool(haki.get("conquerors"))
        conq_lvl = int(haki.get("conqueror", 0)) if haki.get("conqueror") is not None else None

        def _bar(value, max_value=100, length=12):
            v = max(0, min(int(value or 0), max_value))
            filled = int(v / max_value * length) if max_value else 0
            return "█" * filled + "░" * (length - filled)

        embed = discord.Embed(
            title=f"✨ {member.display_name}'s Haki",
            color=discord.Color.purple()
        )

        embed.add_field(
            name="🛡 Armament",
            value=f"{arm} / 100  { _bar(arm) }",
            inline=False
        )
        embed.add_field(
            name="👁 Observation",
            value=f"{obs} / 100  { _bar(obs) }",
            inline=False
        )

        conq_text = "Unlocked ✅" if conq_unlocked else "Locked ❌"
        if conq_lvl is not None and conq_unlocked:
            conq_text += f"  •  Level: {conq_lvl}/100"
        embed.add_field(
            name="👑 Conqueror",
            value=conq_text,
            inline=False
        )

        embed.set_footer(text="Train Haki with .cbtrainhaki — unlock Conqueror at level 10 (.cbunlockconqueror)")
        await ctx.reply(embed=embed)


    @commands.command()
    async def cbtrainhaki(self, ctx, haki_type: str, points: int = 1):
        """
        Train Haki:
        Usage: .cbtrainhaki <armament|observation|conqueror> [points]
        """
        haki_type = (haki_type or "").lower().strip()
        if haki_type in ("conquerors", "conqueror's"):
            haki_type = "conqueror"

        if haki_type not in ("armament", "observation", "conqueror"):
            return await ctx.reply("❌ Haki type must be one of: armament, observation, conqueror")

        try:
            points = max(1, int(points))
        except Exception:
            return await ctx.reply("❌ Points must be a positive integer.")

        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("❌ You must start Crew Battles first (.startcb).")

        g = await self.config.guild(ctx.guild).all()
        cost_per_point = int(g.get("haki_cost", HAKI_TRAIN_COST))
        cooldown = int(g.get("haki_cooldown", HAKI_TRAIN_COOLDOWN))

        last = int(p.get("last_haki_train", 0) or 0)
        now = int(time.time())
        if now - last < cooldown:
            remaining = cooldown - (now - last)
            return await ctx.reply(f"⏳ You must wait {remaining//60}m {remaining%60}s before training again.")

        core = self._beri()
        if not core:
            return await ctx.reply("❌ Economy system unavailable.")

        haki = p.get("haki", {}) or {}

        if haki_type == "conqueror":
            if not bool(haki.get("conquerors")):
                return await ctx.reply("❌ You must unlock Conqueror's Haki first (.cbunlockconqueror).")
            cur = int(haki.get("conqueror", 0))
        else:
            cur = int(haki.get(haki_type, 0))

        new = min(100, cur + points)
        actual = new - cur
        if actual <= 0:
            return await ctx.reply(f"⚠️ {haki_type.capitalize()} Haki is already at max (100).")

        total_cost = cost_per_point * actual
        balance = await core.get_beri(ctx.author)
        if balance < total_cost:
            return await ctx.reply(f"❌ You need **{total_cost:,} Beri** to train {actual} point(s).")

        await core.add_beri(ctx.author, -total_cost, reason="haki:train", bypass_cap=True)
        if haki_type == "conqueror":
            haki["conqueror"] = new
        else:
            haki[haki_type] = new
        p["haki"] = haki
        p["last_haki_train"] = now
        await self.players.save(ctx.author, p)

        # nice embed reply
        embed = discord.Embed(
            title="✅ Haki Trained",
            color=discord.Color.blue(),
            description=f"Trained **{actual}** point(s) into **{haki_type.capitalize()}** Haki."
        )
        embed.add_field(name="New Value", value=f"**{new}** / 100", inline=True)
        embed.add_field(name="Cost", value=f"**{total_cost:,} Beri**", inline=True)
        embed.set_footer(text=f"Next training available in {cooldown//60} minutes")
        await ctx.reply(embed=embed)


    @commands.command()
    async def cbtutorial(self, ctx):
        """Show basic commands and how to play (non-staff)"""
        embed = discord.Embed(
            title="📘 Crew Battles — Quick Tutorial",
            color=discord.Color.teal(),
            description="Commands listed are available to non-staff players."
        )

        embed.add_field(
            name="Getting started",
            value="• `.startcb` — begin your journey and receive a random fruit\n"
                  "• `.cbprofile` — view your crew profile and fruit",
            inline=False
        )

        embed.add_field(
            name="Battling",
            value="• `.battle @user` — challenge another player to a duel\n"
                  "• `.cbleaderboard` — view top players\n"
                  "• During battles: Haki and Devil Fruit abilities may trigger for extra effects",
            inline=False
        )

        embed.add_field(
            name="Devil Fruit Shop",
            value="• `.cbshop [page]` — view shop\n"
                  "• `.cbbuy <fruit name>` — buy a fruit from the shop\n"
                  "• `.cbremovefruit` — remove your fruit (costs Beri)",
            inline=False
        )

        embed.add_field(
            name="Haki & Training",
            value="• `.cbhaki [member]` — view Haki stats\n"
                  "• `.cbtrainhaki <armament|observation|conqueror> [points]` — train Haki (cost & cooldown apply)\n"
                  "• `.cbunlockconqueror` — unlock Conqueror's Haki at level 10",
            inline=False
        )

        embed.set_footer(text="Tip: use .cbprofile and .cbshop to inspect fruits and plan builds")
        await ctx.reply(embed=embed)
