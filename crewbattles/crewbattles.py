import asyncio
import random
import discord
from redbot.core import commands, Config
from .constants import DEFAULT_GUILD
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .bericore_bridge import BeriBridge
from .embeds import battle_embed

class CrewBattles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = PlayerManager(self)
        self.fruits = FruitManager()
        self.teams = TeamsBridge(bot)
        self.beri = BeriBridge(bot)
        self.config = Config.get_conf(self, identifier=444888221)
        self.config.register_guild(**DEFAULT_GUILD)

    # ---------- ADMIN ----------
    @commands.group()
    @commands.admin()
    async def cbadmin(self, ctx):
        pass

    @cbadmin.command()
    async def setberi(self, ctx, win: int, loss: int):
        await self.config.guild(ctx.guild).beri_win.set(win)
        await self.config.guild(ctx.guild).beri_loss.set(loss)
        await ctx.reply("Beri rewards updated.")

    @cbadmin.command()
    async def addfruit(self, ctx, name: str, ftype: str, bonus: int):
        self.fruits.add(name, ftype, bonus)
        await ctx.reply(f"Fruit **{name}** added.")

    @cbadmin.command()
    async def givefruit(self, ctx, member: discord.Member, *, name: str):
        p = await self.players.get(member)
        p["fruit"] = name
        await self.players.save(member, p)
        await ctx.reply(f"{member.mention} assigned **{name}**")

    # ---------- PLAYER ----------
    @commands.command()
    async def startcb(self, ctx):
        p = await self.players.get(ctx.author)
        if p["started"]:
            return await ctx.reply("You already started.")
        fruit = self.fruits.random()
        p["started"] = True
        p["fruit"] = fruit["name"] if fruit else None
        await self.players.save(ctx.author, p)
        if not fruit:
            await ctx.reply("No fruit was assigned to you. You can still play, but try again later for a fruit!")
        await ctx.reply(
            embed=discord.Embed(
                title="🏴‍☠️ Journey Started",
                description=f"Fruit: **{p['fruit'] or 'None'}**\nLevel: **1**"
            )
        )

    @commands.command(name="cbprofile")
    async def cbprofile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        p = await self.players.get(member)

        if not p["started"]:
            return await ctx.reply("❌ This player has not started Crew Battles yet.")
        haki = p.get("haki", {})
        embed = discord.Embed(
            title=f"🏴‍☠️ {member.display_name}'s Crew Battle Profile",
            color=discord.Color.gold(),
        )

        wins = p.get("wins", 0)
        losses = p.get("losses", 0)
        total = wins + losses
        winrate = (wins / total * 100) if total > 0 else 0.0

        embed.add_field(
            name="📊 Stats",
            value=(
                f"**Level:** {p.get('level', 1)}\n"
                f"**Wins:** {wins} • **Losses:** {losses}\n"
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
                f"🛡️ **Armament:** {haki.get('armament', 0)}\n"
                f"👁️ **Observation:** {haki.get('observation', 0)}\n"
                f"👑 **Conqueror’s:** {'Unlocked' if haki.get('conquerors') else 'Locked'}"
            ),
            inline=False,
        )

        embed.set_footer(text="Crew Battles • Progress is saved")

        await ctx.reply(embed=embed)


    @commands.command()
    async def battle(self, ctx, opponent: discord.Member):
        if opponent == ctx.author:
            return await ctx.reply("No self battles.")
        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(opponent)
        if not p1["started"] or not p2["started"]:
            return await ctx.reply("Both players must `.startcb` first.")

        winner, turns, hp1, hp2 = simulate(p1, p2)
        hp1_start = 100 + p1["level"] * 6
        hp2_start = 100 + p2["level"] * 6
        max_hp = max(hp1_start, hp2_start)

        msg = await ctx.send(embed=battle_embed(ctx.author, opponent, hp1_start, hp2_start, max_hp))
        delay = await self.config.guild(ctx.guild).turn_delay()

        for side, dmg, hp in turns:
            if side == "p1":
                hp2 = hp
                text = f"{ctx.author.display_name} dealt **{dmg}** damage!"
            else:
                hp1 = hp
                text = f"{opponent.display_name} dealt **{dmg}** damage!"
            await asyncio.sleep(delay)
            await msg.edit(embed=battle_embed(ctx.author, opponent, hp1, hp2, max_hp, text))

        g = await self.config.guild(ctx.guild).all()
        if winner == "p1":
            p1["wins"] += 1
            p1["exp"] += g["exp_win"]
            p2["losses"] += 1
            p2["exp"] += g["exp_loss"]
            await self.teams.award_win(ctx.guild, ctx.author, g["crew_points_win"])
            await self.beri.reward(ctx.author, g["beri_win"])
        else:
            p2["wins"] += 1
            p2["exp"] += g["exp_win"]
            p1["losses"] += 1
            p1["exp"] += g["exp_loss"]
            await self.teams.award_win(ctx.guild, opponent, g["crew_points_win"])
            await self.beri.reward(opponent, g["beri_win"])

        # Announce the result
        winner_user = ctx.author if winner == "p1" else opponent
        loser_user = opponent if winner == "p1" else ctx.author

        # save updated player data
        await self.players.save(ctx.author, p1)
        await self.players.save(opponent, p2)

        await ctx.reply(
            f"🏆 {winner_user.mention} won the Crew Battle against {loser_user.mention}!\n"
            f"Rewards: +{g['exp_win']} exp to the winner, +{g['exp_loss']} exp to the loser."
        )
