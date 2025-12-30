import json
import random
from pathlib import Path

DATA = Path(__file__).parent / "data" / "fruits.json"

class FruitManager:
    def __init__(self):
        DATA.parent.mkdir(parents=True, exist_ok=True)
        if not DATA.exists():
            DATA.write_text("[]")

    def all(self):
        return json.loads(DATA.read_text())

    def add(self, name, ftype, bonus=0, price=25000, stock=None):
        data = self.all()
        data.append({
            "name": name,
            "type": ftype,
            "bonus": bonus,
            "price": price,
            "stock": stock,  # None = unlimited
        })
        self.save(data)

    def save(self, data):
        DATA.write_text(json.dumps(data, indent=2))

    def random(self):
        fruits = self.all()
        if not fruits or random.random() < 0.35:
            return None
        return random.choice(fruits)

    def get(self, name):    # ...existing code...
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
    # ...existing code...
        return next((f for f in self.all() if f["name"].lower() == name.lower()), None)

    def update(self, fruit):
        data = self.all()
        for i, f in enumerate(data):
            if f["name"] == fruit["name"]:
                data[i] = fruit
                self.save(data)
                return
        # if not found, append and save
        data.append(fruit)
        self.save(data)
