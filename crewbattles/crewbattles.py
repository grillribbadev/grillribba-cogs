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

        self.config = Config.get_conf(self, identifier=444888221, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

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
            f"💰 Beri rewards updated\n"
            f"• Win: **{win}**\n"
            f"• Loss: **{loss}**"
        )

    @cbadmin.command()
    async def addfruit(self, ctx, name: str, ftype: str, bonus: int):
        """Add a Devil Fruit manually"""
        self.fruits.add(name, ftype, bonus)
        await ctx.reply(f"🍈 Devil Fruit **{name}** added.")

    @cbadmin.command()
    async def givefruit(self, ctx, member: discord.Member, *, name: str):
        """Assign a Devil Fruit to a player (admin/testing)"""
        p = await self.players.get(member)
        p["fruit"] = name
        await self.players.save(member, p)
        await ctx.reply(f"🍈 {member.mention} assigned **{name}**")

    # =========================================================
    # PLAYER COMMANDS
    # =========================================================

    @commands.command()
    async def startcb(self, ctx):
        """Start your Crew Battles journey"""
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
                "You can now battle other crews using `.battle @user`"
            ),
        )
        await ctx.reply(embed=embed)

    @commands.command(name="cbprofile")
    async def cbprofile(self, ctx, member: discord.Member = None):
        """View a Crew Battles profile"""
        member = member or ctx.author
        p = await self.players.get(member)

        if not p["started"]:
            return await ctx.reply("❌ This player has not started Crew Battles yet.")

        wins = p.get("wins", 0)
        losses = p.get("losses", 0)
        total = wins + losses
        winrate = (wins / total * 100) if total else 0.0

        haki = p.get("haki", {})

        embed = discord.Embed(
            title=f"🏴‍☠️ {member.display_name}'s Crew Profile",
            color=discord.Color.gold(),
        )

        embed.add_field(
            name="📊 Stats",
            value=(
                f"**Level:** {p.get('level', 1)}\n"
                f"**Wins:** {wins}\n"
                f"**Losses:** {losses}\n"
                f"**Win Rate:** {winrate:.1f}%"
            ),
            inline=False,
        )

        embed.add_field(
            name="🍈 Devil Fruit",
            value=p.get("fruit") or "None",
            inline=False,
        )

        embed.add_field(
            name="✨ Haki",
            value=(
                f"🛡 Armament: {haki.get('armament', 0)}\n"
                f"👁 Observation: {haki.get('observation', 0)}\n"
                f"👑 Conqueror’s: {'Unlocked' if haki.get('conquerors') else 'Locked'}"
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
        """Battle another crew member"""
        if opponent == ctx.author:
            return await ctx.reply("❌ You can't battle yourself.")

        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(opponent)

        if not p1["started"] or not p2["started"]:
            return await ctx.reply("❌ Both players must use `.startcb` first.")

        winner, turns, hp1, hp2 = simulate(p1, p2)

        hp1_start = 100 + p1["level"] * 6
        hp2_start = 100 + p2["level"] * 6
        max_hp = max(hp1_start, hp2_start)

        msg = await ctx.send(
            embed=battle_embed(ctx.author, opponent, hp1_start, hp2_start, max_hp)
        )

        delay = await self.config.guild(ctx.guild).turn_delay()
        log_lines = []

        for turn in turns:
            side = turn[0]
            dmg = turn[1]
            hp = turn[2]
            attack = turn[3] if len(turn) > 3 else "Attack"
            crit = turn[4] if len(turn) > 4 else False

            await asyncio.sleep(delay)

            if side == "p1":
                hp2 = hp
                actor = ctx.author.display_name
            else:
                hp1 = hp
                actor = opponent.display_name

            crit_txt = " 💥 **CRITICAL HIT!**" if crit else ""
            log_lines.append(
                f"⚔️ **{actor}** used **{attack}** and dealt **{dmg}** damage!{crit_txt}"
            )

            await msg.edit(
                embed=battle_embed(
                    ctx.author,
                    opponent,
                    hp1,
                    hp2,
                    max_hp,
                    "\n".join(log_lines[-5:])  # rolling log
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

        winner_p["wins"] += 1
        loser_p["losses"] += 1

        winner_p["exp"] += g["exp_win"]
        loser_p["exp"] += g["exp_loss"]

        await self.players.save(ctx.author, p1)
        await self.players.save(opponent, p2)

        # -------------------------------
        # TEAM POINTS
        # -------------------------------
        await self.teams.award_win(
            ctx.guild, winner_user, g["crew_points_win"]
        )

        # -------------------------------
        # BERI CORE (CORRECT API)
        # -------------------------------
        beri = self.bot.get_cog("BeriCore")
        if beri:
            try:
                economy = beri.economy
                await economy.add_balance(
                    guild=ctx.guild,
                    user=winner_user,
                    amount=g["beri_win"],
                    reason="Crew Battle Win",
                    source="CrewBattles",
                    notify=True,
                    channel=ctx.channel,
                )
            except Exception as e:
                print(f"[CrewBattles] BeriCore win error: {e}")

            if g.get("beri_loss", 0):
                try:
                    await economy.add_balance(
                        guild=ctx.guild,
                        user=loser_user,
                        amount=g["beri_loss"],
                        reason="Crew Battle Loss",
                        source="CrewBattles",
                        notify=False,
                        channel=None,
                    )
                except Exception as e:
                    print(f"[CrewBattles] BeriCore loss error: {e}")

        await ctx.reply(
            f"🏆 **{winner_user.display_name}** won the Crew Battle against "
            f"**{loser_user.display_name}**!\n"
            f"• +{g['exp_win']} EXP\n"
            f"• +{g['beri_win']} Beri"
        )


async def setup(bot):
    await bot.add_cog(CrewBattles(bot))
