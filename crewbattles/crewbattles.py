import asyncio
import random
import time
import discord
import copy
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

    # =========================================================
    # INTERNAL HELPERS
    # =========================================================

    def _beri(self):
        """Safely fetch BeriCore."""
        return self.bot.get_cog("BeriCore")

    @commands.command()
    async def cbshop(self, ctx):
        """View Devil Fruit Shop"""
        fruits = self.fruits.all()
        if not fruits:
            return await ctx.reply("❌ No Devil Fruits available right now.")

        embed = discord.Embed(
            title="🍈 Devil Fruit Shop",
            description="Buy **one** Devil Fruit. You can only own **one at a time**.",
            color=discord.Color.purple(),
        )

        for f in fruits:
            stock = f.get("stock")
            stock_txt = "∞ Unlimited" if stock is None else f"{stock} left"

            embed.add_field(
                name=f["name"],
                value=(
                    f"**Type:** {f['type'].title()}\n"
                    f"💰 **Price:** {f.get('price', 25000):,} Beri\n"
                    f"📦 **Stock:** {stock_txt}"
                ),
                inline=False,
            )

        embed.set_footer(text="Use .cbbuy <fruit name> to purchase")
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

        p["fruit"] = fruit["name"]
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
        arm = haki.get("armament", 0)
        obs = haki.get("observation", 0)
        conquer = "Unlocked ✅" if haki.get("conquerors") else "Locked ❌"
        await ctx.reply(
            f"✨ Haki for {member.display_name}\n"
            f"🛡 Armament: {arm}\n"
            f"👁 Observation: {obs}\n"
            f"👑 Conqueror’s: {conquer}"
        )

    @commands.command()
    async def cbtrainhaki(self, ctx, haki_type: str, points: int = 1):
        """
        Train Haki: cbtrainhaki <armament|observation> [points]
        Costs Beri per-point and cooldown are guild-configurable.
        """
        haki_type = (haki_type or "").lower()
        if haki_type not in ("armament", "observation"):
            return await ctx.reply("❌ Haki type must be 'armament' or 'observation'.")

        try:
            points = max(1, int(points))
        except Exception:
            return await ctx.reply("❌ Invalid points value.")

        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("❌ You must start Crew Battles first.")

        # fetch guild-configured cost & cooldown (fallback to module defaults)
        g = await self.config.guild(ctx.guild).all()
        cost_per_point = int(g.get("haki_cost", HAKI_TRAIN_COST))
        cooldown = int(g.get("haki_cooldown", HAKI_TRAIN_COOLDOWN))

        # cooldown check (per-user timestamp)
        last = p.get("last_haki_train", 0) or 0
        now = int(time.time())
        elapsed = now - int(last)
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            minutes = remaining // 60
            seconds = remaining % 60
            return await ctx.reply(f"⏳ You must wait {minutes}m {seconds}s before training Haki again.")

        core = self._beri()
        if not core:
            return await ctx.reply("❌ Economy system unavailable.")

        # calculate actual trainable points (cap at 100)
        haki = p.get("haki", {}) or {}
        cur = int(haki.get(haki_type, 0))
        new = min(100, cur + points)
        actual = new - cur
        if actual <= 0:
            return await ctx.reply(f"⚠️ Your {haki_type} Haki is already at the maximum (100).")

        total_cost = cost_per_point * actual
        balance = await core.get_beri(ctx.author)
        if balance < total_cost:
            return await ctx.reply(f"❌ You need **{total_cost:,} Beri** to train {actual} point(s) of {haki_type} Haki.")

        # charge user and apply
        await core.add_beri(ctx.author, -total_cost, reason="haki:train", bypass_cap=True)
        haki[haki_type] = new
        p["haki"] = haki
        p["last_haki_train"] = now
        await self.players.save(ctx.author, p)

        await ctx.reply(
            f"✅ Trained **{actual}** point(s) of **{haki_type}** Haki.\n"
            f"• New {haki_type.capitalize()}: **{new}**\n"
            f"• Spent: **{total_cost:,} Beri**\n"
            f"• Next training available in: {cooldown // 60} minutes"
        )

    @commands.command()
    async def cbunlockconqueror(self, ctx):
        """
        Unlock Conqueror's Haki.
        Requires level >= 10 to unlock.
        """
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("❌ You must start Crew Battles first.")

        haki = p.get("haki", {}) or {}
        if haki.get("conquerors"):
            return await ctx.reply("✅ You already unlocked Conqueror's Haki.")

        level = p.get("level", 1)
        if level < 10:
            return await ctx.reply("❌ You must be at least level 10 to unlock Conqueror's Haki.")

        haki["conquerors"] = True
        p["haki"] = haki
        await self.players.save(ctx.author, p)

        await ctx.reply("👑 Conqueror's Haki unlocked! Congratulations.")

    # =========================================================
    # BATTLE SYSTEM
    # =========================================================

    @commands.command()
    async def battle(self, ctx, opponent: discord.Member):
        if opponent == ctx.author:
            return await ctx.reply("❌ You can't battle yourself.")

        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(opponent)

        if not p1["started"] or not p2["started"]:
            return await ctx.reply("❌ Both players must `.startcb` first.")

        # before calling simulate or before rendering the per-turn embed, compute both maxima:
        # assuming p1_data and p2_data are the player dicts used by simulate
        max_hp1 = BASE_HP + int(p1.get("level", 1)) * 6
        max_hp2 = BASE_HP + int(p2.get("level", 1)) * 6

        # simulate stays the same (returns hp1/hp2 current values in your turns list)
        winner, turns, final_hp1, final_hp2 = simulate(p1, p2)

        # use the computed max_hp values for each player
        hp1_start = max_hp1
        hp2_start = max_hp2

        battle_log: list[str] = []

        msg = await ctx.send(
            embed=battle_embed(
                ctx.author,
                opponent,
                hp1_start,
                hp2_start,
                max_hp1,
                max_hp2,
                "⚔️ Battle started!"
            )
        )

        delay = await self.config.guild(ctx.guild).turn_delay()

        # Named attacks to use when the battle engine doesn't provide one
        ATTACKS = [
            "Gomu Gomu no Pistol",
            "Gomu Gomu no Gatling",
            "Gomu Gomu no Bazooka",
            "Red Hawk",
            "Diable Jambe",
            "Oni Giri",
            "King Cobra",
            "Hiken",
            "Shishi Sonson",
            "Rengoku",
            "Conqueror's Crush",
            "Armament Strike",
            "Observation Stab",
            "Sky Walk Kick",
            "Elephant Gun",
        ]

        for turn in turns:
            # Defensive unpacking: support both legacy (side,dmg,hp) and newer (side,dmg,hp,attack,crit)
            if isinstance(turn, (list, tuple)):
                if len(turn) >= 5:
                    side, dmg, hp, attack, crit = turn[:5]
                elif len(turn) == 3:
                    side, dmg, hp = turn
                    attack = random.choice(ATTACKS)
                    crit = False
                else:
                    # Best-effort fallback
                    side = turn[0] if len(turn) > 0 else "p1"
                    dmg = turn[1] if len(turn) > 1 else 0
                    hp = turn[2] if len(turn) > 2 else 0
                    attack = turn[3] if len(turn) > 3 else random.choice(ATTACKS)
                    crit = bool(turn[4]) if len(turn) > 4 else False
            else:
                # Unexpected shape: coerce to defaults
                side, dmg, hp = "p1", 0, 0
                attack, crit = random.choice(ATTACKS), False

            # Ensure we don't display a bland default
            if not attack:
                attack = random.choice(ATTACKS)

            await asyncio.sleep(delay)

            if side == "p1":
                hp2 = hp
                actor = ctx.author.display_name
            else:
                hp1 = hp
                actor = opponent.display_name

            crit_txt = " 💥 **CRITICAL HIT!**" if crit else ""
            battle_log.append(
                f"⚔️ **{actor}** used **{attack}** and dealt **{dmg}** damage!{crit_txt}"
            )

            log_text = "\n".join(battle_log[-6:])  # KEEP AS STRING

            await msg.edit(
                embed=battle_embed(
                    ctx.author,
                    opponent,
                    hp1,
                    hp2,
                    max_hp1,    # p1 max HP
                    max_hp2,    # p2 max HP
                    log_text
                )
            )

        # =====================================================
        # RESULTS
        # =====================================================

        g = await self.config.guild(ctx.guild).all()

        winner_user = ctx.author if winner == "p1" else opponent
        loser_user = opponent if winner == "p1" else ctx.author

        winner_p = p1 if winner == "p1" else p2
        loser_p = p2 if winner == "p1" else p1

        winner_p["wins"] = winner_p.get("wins", 0) + 1
        loser_p["losses"] = loser_p.get("losses", 0) + 1

        winner_p["exp"] = winner_p.get("exp", 0) + g.get("exp_win", 0)
        loser_p["exp"] = loser_p.get("exp", 0) + g.get("exp_loss", 0)

        await self.players.save(ctx.author, p1)
        await self.players.save(opponent, p2)

        # -------------------------------
        # TEAM POINTS
        # -------------------------------
        await self.teams.award_win(
            ctx.guild,
            winner_user,
            g.get("crew_points_win", 0)
        )

        # -------------------------------
        # BERI CORE (CORRECT API)
        # -------------------------------
        core = self._beri()
        if core:
            try:
                await core.add_beri(
                    winner_user,
                    g.get("beri_win", 0),
                    reason="pvp:crew_battle:win"
                )
            except Exception as e:
                print(f"[CrewBattles] Beri win error: {e}")

            if g.get("beri_loss", 0) != 0:
                try:
                    await core.add_beri(
                        loser_user,
                        g.get("beri_loss", 0),
                        reason="pvp:crew_battle:loss"
                    )
                except Exception as e:
                    print(f"[CrewBattles] Beri loss error: {e}")

        await ctx.reply(
            f"🏆 **{winner_user.display_name}** won the Crew Battle against "
            f"**{loser_user.display_name}**!\n"
            f"• +{g.get('exp_win', 0)} EXP\n"
            f"• +{g.get('beri_win', 0)} Beri"
        )


async def setup(bot):
    await bot.add_cog(CrewBattles(bot))
