import asyncio
import random
import discord
from redbot.core import commands, Config

from .constants import DEFAULT_GUILD
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .embeds import battle_embed


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

        winner, turns, hp1, hp2 = simulate(p1, p2)

        hp1_start = 100 + p1["level"] * 6
        hp2_start = 100 + p2["level"] * 6
        max_hp = max(hp1_start, hp2_start)

        battle_log: list[str] = []

        msg = await ctx.send(
            embed=battle_embed(
                ctx.author,
                opponent,
                hp1_start,
                hp2_start,
                max_hp,
                "⚔️ Battle started!"
            )
        )

        delay = await self.config.guild(ctx.guild).turn_delay()

        for turn in turns:
            side, dmg, hp, attack, crit = turn

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
                    max_hp,
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
