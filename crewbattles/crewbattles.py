from redbot.core import commands, Config
from .constants import DEFAULT_GUILD
from .player_manager import PlayerManager
from .battle_engine import simulate
from .fruits import FruitManager
from .haki import can_train, can_conqueror
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
        self.config = Config.get_conf(self, identifier=667788990)
        self.config.register_guild(**DEFAULT_GUILD)

    # -------- ADMIN CONFIG --------
    @commands.group()
    @commands.admin()
    async def cbadmin(self, ctx):
        pass

    @cbadmin.command()
    async def setexp(self, ctx, win: int, loss: int):
        await self.config.guild(ctx.guild).exp_win.set(win)
        await self.config.guild(ctx.guild).exp_loss.set(loss)
        await ctx.reply("EXP values updated.")

    @cbadmin.command()
    async def addfruit(self, ctx, name: str, ftype: str, bonus: int):
        self.fruits.add(name, ftype, bonus)
        await ctx.reply(f"Fruit **{name}** added.")

    # -------- PLAYER COMMANDS --------
    @commands.command()
    async def startcb(self, ctx):
        p = await self.players.get(ctx.author)
        if p["fruit"] is None:
            fruit = self.fruits.random()
            p["fruit"] = fruit["name"] if fruit else None
            await self.players.save(ctx.author, p)
            await ctx.reply(f"🍈 Devil Fruit: **{p['fruit'] or 'None'}**")
        else:
            await ctx.reply("You already started.")

    @commands.command()
    async def battle(self, ctx, opponent: commands.MemberConverter):
        if opponent == ctx.author:
            return await ctx.reply("You can't battle yourself.")

        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(opponent)

        winner, log = simulate(p1, p2)

        g = await self.config.guild(ctx.guild).all()
        if winner == "p1":
            p1["wins"] += 1
            p1["exp"] += g["exp_win"]
            p2["losses"] += 1
            p2["exp"] += g["exp_loss"]
            team = await self.teams.award(ctx.guild, ctx.author, g["crew_points"])
            await self.beri.reward(ctx.author, 500)
        else:
            p2["wins"] += 1
            p2["exp"] += g["exp_win"]
            p1["losses"] += 1
            p1["exp"] += g["exp_loss"]
            team = await self.teams.award(ctx.guild, opponent, g["crew_points"])
            await self.beri.reward(opponent, 500)

        await self.players.save(ctx.author, p1)
        await self.players.save(opponent, p2)

        emb = battle_embed(ctx.author, opponent, log, winner)
        await ctx.send(embed=emb)
