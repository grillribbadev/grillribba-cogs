import asyncio
import random
import time
import discord
import math
import copy
import json
from redbot.core import commands, Config

from .constants import DEFAULT_GUILD, DEFAULT_USER, BASE_HP, MAX_LEVEL
from .player_manager import PlayerManager
from .fruits import FruitManager
from .battle_engine import simulate
from .teams_bridge import TeamsBridge
from .embeds import battle_embed
from .utils import exp_to_next


# Haki training configuration (cost in Beri per point, cooldown seconds)
HAKI_TRAIN_COST = 500
HAKI_TRAIN_COOLDOWN = 60 * 60  # 1 hour


class CrewBattles(commands.Cog):
    """Crew Battles – PvP battles with Devil Fruits, Teams & BeriCore integration"""

    def __init__(self, bot):
        self.bot = bot
        # config for guild defaults
        self.config = Config.get_conf(self, identifier=0xC0A55EE, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        # ensure global maintenance key exists
        self.config.register_global(maintenance=False)

        # managers
        self.players = PlayerManager(self)
        self.fruits = FruitManager()
        self.teams = TeamsBridge(bot)

        # one-battle-per-channel lock
        self._active_battles = set()

    # =========================================================
    # INTERNAL HELPERS
    # =========================================================
    def _apply_exp(self, player: dict, gain: int) -> int:
        """
        Add EXP to a player dict, level up while thresholds are met.
        Returns number of levels gained (0 if none).
        Mutates player['exp'] and player['level'] (ensures ints).
        """
        try:
            gain = int(gain or 0)
        except Exception:
            gain = 0
        # normalize stored values to ints
        try:
            cur_level = int(player.get("level", 1) or 1)
        except Exception:
            cur_level = 1
        try:
            cur_exp = int(player.get("exp", 0) or 0)
        except Exception:
            cur_exp = 0
        cur_exp += gain
        leveled = 0
        # loop until we can't level or hit MAX_LEVEL
        while cur_level < MAX_LEVEL:
            needed = exp_to_next(cur_level)
            if cur_exp >= needed:
                cur_exp -= needed
                cur_level += 1
                leveled += 1
            else:
                break
        # If at max level, clamp exp to one below next threshold for display
        if cur_level >= MAX_LEVEL:
            # keep some exp but not overflow; set to min(remaining, next-1) for consistency
            try:
                cur_exp = min(cur_exp, exp_to_next(cur_level) - 1)
            except Exception:
                cur_exp = 0
        # ensure integers stored
        player["level"] = int(cur_level)
        player["exp"] = int(cur_exp)
        return leveled

    def _beri(self):
        """Safe accessor for BeriCore cog (may be None)."""
        return self.bot.get_cog("BeriCore")

    async def _team_of(self, guild, member):
        """
        Return a normalized team identifier (string) for a member in a guild, or None.
        - First tries the real 'Teams' cog structure (common implementation).
        - Falls back to calling common bridge methods if available.
        """
        # 1) Direct Teams cog support (most reliable for your setup)
        teams_cog = self.bot.get_cog("Teams")
        if teams_cog:
            try:
                guild_map = getattr(teams_cog, "teams", None)
                if isinstance(guild_map, dict):
                    guild_teams = guild_map.get(guild.id, {}) or {}
                    for team in guild_teams.values():
                        # team.members may be a list of Member objects or member ids
                        members = getattr(team, "members", None)
                        if members:
                            try:
                                if member in members or member.id in members:
                                    # prefer id/name/display_name
                                    tid = getattr(team, "id", None) or getattr(team, "team_id", None) or getattr(team, "name", None) or getattr(team, "display_name", None)
                                    return str(tid).strip().lower() if tid is not None else None
                            except Exception:
                                # fallback: iterate and compare ids
                                for m in members:
                                    try:
                                        if (hasattr(m, "id") and m.id == member.id) or (isinstance(m, int) and m == member.id) or (isinstance(m, str) and str(m) == str(member.id)):
                                            tid = getattr(team, "id", None) or getattr(team, "team_id", None) or getattr(team, "name", None) or getattr(team, "display_name", None)
                                            return str(tid).strip().lower() if tid is not None else None
                                    except Exception:
                                        pass
            except Exception:
                pass

        # 2) Fallback: try bridge-like methods on any bridge object (self.teams or other)
        # Keep previous flexible approach but call real Teams cog first to avoid missed matches.
        bridge_candidates = (self.teams, self.bot.get_cog("TeamsBridge"), self.bot.get_cog("Teams"))
        tried = set()
        async def _try_fn(fn, *args):
            try:
                res = fn(*args)
            except TypeError:
                return None
            except Exception:
                return None
            if asyncio.iscoroutine(res):
                try:
                    res = await res
                except Exception:
                    return None
            return res

        def _normalize(res):
            if res is None:
                return None
            if isinstance(res, dict):
                for key in ("id", "team_id", "name", "team"):
                    if key in res and res[key] is not None:
                        return str(res[key]).strip().lower()
                for v in res.values():
                    if isinstance(v, (str, int)):
                        return str(v).strip().lower()
            if hasattr(res, "id") or hasattr(res, "name"):
                val = getattr(res, "id", None) or getattr(res, "name", None)
                return str(val).strip().lower() if val is not None else None
            if isinstance(res, (list, tuple)) and len(res):
                for item in res:
                    if isinstance(item, (str, int)):
                        return str(item).strip().lower()
                    if hasattr(item, "id") or hasattr(item, "name"):
                        v = getattr(item, "id", None) or getattr(item, "name", None)
                        if v is not None:
                            return str(v).strip().lower()
            if isinstance(res, (str, int)):
                return str(res).strip().lower()
            return None

        candidate_names = (
            "get_team",
            "get_member_team",
            "get_team_of",
            "member_team",
            "team_of",
            "get_team_for",
            "get_member_team_async",
            "fetch_member_team",
        )
        for bridge in bridge_candidates:
            if not bridge:
                continue
            for name in candidate_names:
                fn = getattr(bridge, name, None)
                if not fn or (bridge, name) in tried:
                    continue
                tried.add((bridge, name))
                # try multiple call signatures
                for call_sig in (
                    (guild, member),
                    (guild, member.id),
                    (guild.id, member),
                    (guild.id, member.id),
                    (member,),
                    (member.id,),
                ):
                    res = await _try_fn(fn, *call_sig)
                    val = _normalize(res)
                    if val:
                        return val

        return None

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
    async def setfruitstock(self, ctx, name: str, stock):
        """Set shop stock for a fruit: .cbadmin setfruitstock <name> <stock|none>"""
        fruit = self.fruits.get(name)
        if not fruit:
            return await ctx.reply("❌ Fruit not found.")

        # allow "none"/"unlimited"/"∞" to mean unlimited stock
        if isinstance(stock, str) and stock.lower() in ("none", "unlimited", "∞"):
            stock_val = None
        else:
            try:
                stock_val = int(stock)
                if stock_val < 0:
                    return await ctx.reply("❌ Stock must be 0 or a positive integer, or 'none' for unlimited.")
            except Exception:
                return await ctx.reply("❌ Stock must be an integer or 'none' for unlimited.")

        fruit["stock"] = stock_val
        self.fruits.update(fruit)
        await ctx.reply(f"📦 Stock for **{fruit['name']}** set to **{stock_val if stock_val is not None else '∞'}**")

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
    async def resetalldata(self, ctx, confirm: str = None):
        """
        Reset all players' Crew Battles data to defaults (devil fruit, exp, level, haki, wins/losses).
        Usage: .cbadmin resetalldata confirm
        """
        if confirm != "confirm":
            return await ctx.reply("❗ This will reset ALL player data. To proceed run: `.cbadmin resetalldata confirm`")

        try:
            raw = await self.players.all()
        except Exception as e:
            return await ctx.reply(f"❌ Could not read player storage: {e}")

        count = 0
        # handle dict-like raw storage ({uid: pdata})
        if isinstance(raw, dict):
            for k in list(raw.keys()):
                try:
                    uid = int(k)
                except Exception:
                    continue
                try:
                    await self.players.save(uid, copy.deepcopy(DEFAULT_USER))
                    count += 1
                except Exception:
                    # ignore per-user failures
                    pass
        elif isinstance(raw, (list, tuple)):
            # handle list shapes: [ { "id": uid, ... }, ... ] or [ (uid, data), ... ]
            for item in raw:
                try:
                    uid = None
                    if isinstance(item, dict) and item.get("id"):
                        uid = int(item["id"])
                    elif isinstance(item, (list, tuple)) and len(item) >= 1:
                        uid = int(item[0])
                    if uid is None:
                        continue
                    await self.players.save(uid, copy.deepcopy(DEFAULT_USER))
                    count += 1
                except Exception:
                    pass
        else:
            return await ctx.reply("❌ Unrecognized player storage format; manual reset required.")

        await ctx.reply(f"✅ Reset Crew Battles data for {count} users. Leaderboard cleared accordingly.")

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

    @commands.command()
    async def battle(self, ctx, opponent: discord.Member):
        """Challenge another player to a battle"""
        if ctx.author == opponent:
            return await ctx.reply("❌ You cannot battle yourself.")

        if opponent.bot:
            return await ctx.reply("❌ You cannot battle bots.")

        # Check if already in a battle
        if ctx.channel.id in self._active_battles:
            return await ctx.reply("❌ A battle is already in progress in this channel.")

        # Fetch players
        p1 = await self.players.get(ctx.author)
        p2 = await self.players.get(opponent)

        if not p1.get("started") or not p2.get("started"):
            return await ctx.reply("❌ Both players must `.startcb` first.")

        # Enforce cross-team-only duels when team info is available
        try:
            t1 = await self._team_of(ctx.guild, ctx.author)
            t2 = await self._team_of(ctx.guild, opponent)
        except Exception:
            t1 = t2 = None
        # only block if both players have a team and it's the same
        if t1 is not None and t2 is not None and t1 == t2:
            return await ctx.reply("❌ You can only challenge players from other teams.")

        # Mark channel as busy
        self._active_battles.add(ctx.channel.id)

        # Prepare battle data
        battle_data = {
            "channel": ctx.channel,
            "player1": ctx.author,
            "player2": opponent,
            "fruits": [p1.get("fruit"), p2.get("fruit")],
            "teams": [p1.get("team"), p2.get("team")],
            "haki": [p1.get("haki"), p2.get("haki")],
            "players": [p1, p2],
            "turn_delay": 5,  # seconds
            "last_action": [0, 0],  # timestamps
            "log": [],  # battle log
        }

        # Start the battle loop
        await ctx.reply(f"⚔️ **{ctx.author.display_name}** has challenged **{opponent.display_name}**!")
        # build a proper initial embed and create the single message we'll edit each turn
        max_hp1 = BASE_HP + int(p1.get("level", 1)) * 6
        max_hp2 = BASE_HP + int(p2.get("level", 1)) * 6
        hp1 = int(max_hp1)
        hp2 = int(max_hp2)
        initial_log = "⚔️ Battle started!"

        # Ensure these exist in the function scope so any path can't reference them before assignment
        attack_default = "Attack"
        crit = False

        msg = await ctx.reply(embed=battle_embed(ctx.author, opponent, hp1, hp2, max_hp1, max_hp2, initial_log))

        try:
            # Run deterministic simulation and iterate its turns (shows abilities/haki)
            winner, turns, final_hp1, final_hp2 = simulate(p1, p2, self.fruits)

            # (msg already created above) reuse it for per-turn edits
            delay = await self.config.guild(ctx.guild).turn_delay()
            battle_log = []

            # safe defaults in outer scope
            attack_default = "Attack"
            crit = False

            for turn in turns:
                # start each turn with safe defaults
                attack = attack_default
                crit = False

                # flexible unpack with safe fallbacks
                if isinstance(turn, (list, tuple)):
                    side = str(turn[0]) if len(turn) > 0 else "p1"
                    try:
                        dmg = int(turn[1]) if len(turn) > 1 else 0
                    except Exception:
                        dmg = 0
                    try:
                        hp = int(turn[2]) if len(turn) > 2 else (hp2 if side == "p1" else hp1)
                    except Exception:
                        hp = (hp2 if side == "p1" else hp1)
                    if len(turn) > 3 and turn[3] is not None:
                        attack = str(turn[3])
                    if len(turn) > 4:
                        crit = bool(turn[4])
                else:
                    side, dmg, hp = "p1", 0, (hp2 if "hp2" in locals() else 0)

                # apply hp update from engine's hp value
                await asyncio.sleep(max(0.1, float(delay or 1)))
                if side == "p1":
                    hp2 = int(hp)
                    actor_user = ctx.author
                    defender_user = opponent
                    actor_p = p1
                    defender_p = p2
                else:
                    hp1 = int(hp)
                    actor_user = opponent
                    defender_user = ctx.author
                    actor_p = p2
                    defender_p = p1

                attack_str = str(attack or "")

                # nicer human-friendly events
                if "Frightened" in attack_str:
                    line = f"😨 **{actor_user.display_name}** was frightened and skipped their turn!"
                elif "Dodged" in attack_str or attack_str.strip().lower() == "dodged":
                    obs_val = int((defender_p.get("haki") or {}).get("observation", 0))
                    if obs_val > 0:
                        line = f"👁️ **{defender_user.display_name}** used Observation Haki and dodged!"
                    else:
                        line = f"🛡️ **{defender_user.display_name}** dodged the attack!"
                else:
                    crit_txt = " 💥 **CRITICAL HIT!**" if crit else ""
                    if int(dmg) <= 0:
                        line = f"⚔️ **{actor_user.display_name}** attacked with **{attack_str}** but it dealt no damage.{crit_txt}"
                    else:
                        line = f"⚔️ **{actor_user.display_name}** used **{attack_str}** and dealt **{int(dmg)}** damage!{crit_txt}"

                battle_log.append(line)
                log_text = "\n".join(battle_log[-6:])

                await msg.edit(embed=battle_embed(ctx.author, opponent, hp1, hp2, max_hp1, max_hp2, log_text))

            # apply results/stats/rewards
            g = await self.config.guild(ctx.guild).all()
            winner_user = ctx.author if winner == "p1" else opponent
            loser_user = opponent if winner == "p1" else ctx.author
            winner_p = p1 if winner == "p1" else p2
            loser_p = p2 if winner == "p1" else p1

            # record wins/losses
            winner_p["wins"] = winner_p.get("wins", 0) + 1
            loser_p["losses"] = loser_p.get("losses", 0) + 1

            # apply EXP and handle level ups
            gain_win = int(g.get("exp_win", 0) or 0)
            gain_loss = int(g.get("exp_loss", 0) or 0)
            leveled_w = self._apply_exp(winner_p, gain_win)
            leveled_l = self._apply_exp(loser_p, gain_loss)

            # persist both player records using explicit winner/loser mapping
            try:
                await self.players.save(winner_user, winner_p)
            except Exception:
                # best-effort fallback
                await self.players.save(ctx.author, p1)
            try:
                await self.players.save(loser_user, loser_p)
            except Exception:
                await self.players.save(opponent, p2)

            # beri rewards (if BeriCore present)
            core = self._beri()
            if core:
                try:
                    await core.add_beri(winner_user, int(g.get("beri_win", 0) or 0), reason="pvp:crew_battle:win")
                except Exception:
                    pass
                try:
                    loss_amt = int(g.get("beri_loss", 0) or 0)
                    if loss_amt:
                        await core.add_beri(loser_user, loss_amt, reason="pvp:crew_battle:loss")
                except Exception:
                    pass

            # Award crew/team points via TeamsBridge (best-effort)
            try:
                crew_points = int(g.get("crew_points_win", 0) or 0)
            except Exception:
                crew_points = 0
            if crew_points and getattr(self, "teams", None):
                try:
                    ok = await self.teams.award_win(ctx.guild, winner_user, crew_points)
                    if not ok:
                        print(f"[CrewBattles] teams_bridge: failed to award {crew_points} points for {winner_user} (no matching Teams method).")
                except Exception as e:
                    print(f"[CrewBattles] teams_bridge error awarding points: {e}")

            # final result embed
            try:
                winner_avatar = getattr(winner_user.display_avatar, "url", None) if hasattr(winner_user, "display_avatar") else getattr(winner_user, "avatar_url", None)
            except Exception:
                winner_avatar = None

            res = discord.Embed(
                title="🏆 Crew Battle Result",
                description=f"**{winner_user.display_name}** defeated **{loser_user.display_name}**",
                color=discord.Color.green()
            )
            if winner_avatar:
                res.set_thumbnail(url=winner_avatar)
            res.add_field(name="Rewards", value=f"EXP: **+{int(g.get('exp_win',0))}**\nBeri: **+{int(g.get('beri_win',0)):,}**", inline=False)
            # show any level-ups
            level_lines = []
            try:
                if leveled_w:
                    level_lines.append(f"🏅 **{winner_user.display_name}** leveled up +{leveled_w} → Level {winner_p.get('level')}")
                if leveled_l:
                    level_lines.append(f"🔰 **{loser_user.display_name}** leveled up +{leveled_l} → Level {loser_p.get('level')}")
                if level_lines:
                    res.add_field(name="Level Ups", value="\n".join(level_lines), inline=False)
            except Exception:
                pass
            res.set_footer(text="Crew Battles • Results")
            await ctx.reply(embed=res)

        except Exception as e:
            await ctx.reply(f"❌ Battle error: {e}")
        finally:
            # always free the channel lock
            self._active_battles.discard(ctx.channel.id)

    @commands.command()
    async def cbleaderboard(self, ctx, metric: str = "wins", limit: int = 10):
        """
        Show a simple leaderboard with a guild-member fallback when raw storage is unavailable.
        metric: wins | winrate | level | exp
        """
        metric = (metric or "wins").lower()
        if metric not in ("wins", "winrate", "level", "exp"):
            return await ctx.reply("❌ Metric must be one of: wins, winrate, level, exp")

        try:
            limit = max(1, min(25, int(limit)))
        except Exception:
            limit = 10

        # Try to read raw storage first
        try:
            raw = await self.players.all()
        except Exception as e:
            print(f"[CrewBattles] cbleaderboard: players.all() raised: {e}")
            raw = {}

        # If storage empty, fall back to scanning guild members (works reliably)
        entries = []
        if raw:
            if isinstance(raw, dict):
                for k, v in raw.items():
                    try:
                        uid = int(k)
                    except Exception:
                        try:
                            uid = int(str(k))
                        except Exception:
                            continue
                    entries.append((uid, v or {}))
            else:
                # attempt best-effort normalization for non-dict raw shapes
                try:
                    for item in raw:
                        if isinstance(item, dict) and item.get("id"):
                            entries.append((int(item["id"]), item))
                        elif isinstance(item, (list, tuple)) and len(item) >= 2:
                            entries.append((int(item[0]), item[1] or {}))
                except Exception:
                    pass

        if not entries:
            # fallback: scan guild members and gather started players
            for member in ctx.guild.members:
                try:
                    pdata = await self.players.get(member)
                except Exception:
                    continue
                if pdata and pdata.get("started"):
                    entries.append((member.id, pdata))

        if not entries:
            return await ctx.reply("⚠️ No player data found. Make sure players have used `.startcb` and the cog has initialized storage.")

        # compute score rows
        rows = []
        for uid, pdata in entries:
            if not isinstance(pdata, dict):
                continue
            wins = int(pdata.get("wins", 0) or 0)
            losses = int(pdata.get("losses", 0) or 0)
            total = wins + losses
            winrate = (wins / total * 100) if total else 0.0
            level = int(pdata.get("level", 1) or 1)
            exp = int(pdata.get("exp", 0) or 0)

            if metric == "wins":
                score = wins
                score_txt = f"{wins} wins"
            elif metric == "winrate":
                score = winrate
                score_txt = f"{winrate:.1f}% winrate ({wins}/{total})"
            elif metric == "level":
                score = level
                score_txt = f"Level {level} • {exp} EXP"
            else:  # exp
                score = exp
                score_txt = f"{exp} EXP • Level {level}"

            rows.append((score, uid, score_txt, wins, losses, level, exp))

        if not rows:
            return await ctx.reply("❌ No valid player entries to show on leaderboard.")

        rows.sort(key=lambda r: r[0], reverse=True)
        top = rows[:limit]

        # Build a flashier embed
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}
        colors = {
            "wins": discord.Color.gold(),
            "winrate": discord.Color.blurple(),
            "level": discord.Color.dark_teal(),
            "exp": discord.Color.green(),
        }
        emb = discord.Embed(
            title=f"🏆 Crew Battles — {metric.title()} Leaderboard",
            description=f"Top {len(top)} players by {metric}",
            color=colors.get(metric, discord.Color.gold())
        )

        lines = []
        for idx, (score, uid, score_txt, wins, losses, level, exp) in enumerate(top, start=1):
            user = self.bot.get_user(uid)
            display = user.display_name if user else f"<@{uid}>"
            name_line = f"**{display}**"
            prefix = medal.get(idx, f"#{idx}")
            # extra micro-flair: show small summary after name
            summary = f"• Lv{level} • {wins}W/{losses}L"
            lines.append(f"{prefix} {name_line} — {score_txt} {summary}")

        emb.add_field(name="Leaderboard", value="\n".join(lines), inline=False)

        # thumbnail/avatar of top1 if available
        try:
            top1_uid = top[0][1]
            top1_user = self.bot.get_user(top1_uid)
            if top1_user:
                url = getattr(top1_user.display_avatar, "url", None) if hasattr(top1_user, "display_avatar") else getattr(top1_user, "avatar_url", None)
                if url:
                    emb.set_thumbnail(url=url)
        except Exception:
            pass

        emb.set_footer(text=f"Use .cbleaderboard <metric> <limit> • Metrics: wins, winrate, level, exp")
        await ctx.reply(embed=emb)

    @commands.command()
    async def cbunlockconqueror(self, ctx):
        """
        Player command to unlock Conqueror's Haki.
        Requirements: started, level >= 10, pays configured cost (default 5000 Beri).
        """
        p = await self.players.get(ctx.author)
        if not p.get("started"):
            return await ctx.reply("❌ You must start Crew Battles first with `.startcb`.")

        haki = p.get("haki", {}) or {}
        if bool(haki.get("conquerors")):
            return await ctx.reply("✅ You already have Conqueror's Haki unlocked.")

        lvl = int(p.get("level", 1) or 1)
        if lvl < 10:
            return await ctx.reply("❌ You must reach level 10 to unlock Conqueror's Haki.")

        g = await self.config.guild(ctx.guild).all()
        cost = int(g.get("conqueror_unlock_cost", 5000) or 5000)

        core = self._beri()
        if not core:
            return await ctx.reply("❌ Economy (BeriCore) is required to buy this. Ask an admin to enable BeriCore.")

        try:
            bal = await core.get_beri(ctx.author)
        except Exception:
            return await ctx.reply("❌ Could not read your Beri balance. Try again later.")

        if bal < cost:
            return await ctx.reply(f"❌ You need **{cost:,} Beri** to unlock Conqueror's Haki (you have {bal:,}).")

        try:
            await core.add_beri(ctx.author, -cost, reason="haki:unlock_conqueror", bypass_cap=True)
        except Exception:
            return await ctx.reply("❌ Failed to charge Beri. Unlock aborted.")

        haki["conquerors"] = True
        p["haki"] = haki
        await self.players.save(ctx.author, p)

        await ctx.reply(f"🏆 **{ctx.author.display_name}** unlocked Conqueror's Haki!")

    @commands.command(name="cbmaintenance")
    async def cbmaintenance(self, ctx, mode: str = None):
        """
        Toggle or view maintenance mode.
        Usage:
          .cbmaintenance           -> show current status
          .cbmaintenance on|off    -> enable/disable maintenance
        Allowed to toggle: bot owner or server administrators.
        When enabled, non-admins cannot use any Crew Battles commands.
        """
        # permission: bot owner OR guild administrator
        is_owner = False
        try:
            is_owner = await self.bot.is_owner(ctx.author)
        except Exception:
            pass
        is_admin = ctx.guild and getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator
        if not (is_owner or is_admin):
            return await ctx.reply("❌ You must be the bot owner or a server administrator to change maintenance mode.")
 
        # read current
        try:
            current = await self.config.maintenance()
        except Exception:
            current = False
 
        if not mode:
            return await ctx.reply(f"⚙️ Maintenance mode is currently **{'ON' if current else 'OFF'}**.")
 
        m = mode.lower().strip()
        if m in ("on", "true", "enable"):
            await self.config.maintenance.set(True)
            return await ctx.reply("🔧 Maintenance mode enabled. Non-admin users cannot use Crew Battles commands.")
        if m in ("off", "false", "disable"):
            await self.config.maintenance.set(False)
            return await ctx.reply("✅ Maintenance mode disabled. Crew Battles commands are available again.")
 
        await ctx.reply("❌ Usage: `.cbmaintenance on|off`")

    async def cog_check(self, ctx):
        """
        Cog-level check to enforce global maintenance mode.
        When maintenance is enabled only the bot owner or guild administrators may use commands.
        """
        # Read global maintenance flag (safe)
        try:
            maintenance = await self.config.maintenance()
        except Exception:
            maintenance = False

        if not maintenance:
            return True

        # Bot owner always allowed
        try:
            if await self.bot.is_owner(ctx.author):
                return True
        except Exception:
            pass

        # Guild administrators allowed (when in a guild)
        if ctx.guild and getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator:
            return True

        # Deny for everyone else
        try:
            await ctx.reply("⚠️ Crew Battles is currently in maintenance mode. Only server administrators or the bot owner may use these commands.")
        except Exception:
            pass
        return False
