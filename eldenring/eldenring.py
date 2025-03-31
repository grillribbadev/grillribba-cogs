import discord
from discord.ext import commands
import aiohttp
from bs4 import BeautifulSoup


class EldenRing(commands.Cog):
    """Search for Elden Ring weapons, spells, and more from the Fandom wiki."""

    def __init__(self, bot):
        self.bot = bot

    async def fetch_wiki_data(self, name: str):
        base_url = "https://eldenring.fandom.com/wiki/"
        search_name = name.strip().replace(" ", "_")
        url = base_url + search_name

        print(f"🔎 Henter: {url}")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as resp:
                print(f"🌐 Statuskode: {resp.status}")
                if resp.status != 200:
                    return None
                html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        # Tittel
        title = soup.title.string.replace(" | Elden Ring Wiki | Fandom", "") if soup.title else "Unknown Title"

        # Bilde fra infoboksen
        image = soup.select_one(".portable-infobox img")
        image_url = image["src"] if image else None

        # Parse .portable-infobox data-fields
        infobox = soup.select_one(".portable-infobox")
        fields = []
        if infobox:
            rows = infobox.select(".pi-data")
            for row in rows:
                label = row.select_one(".pi-data-label")
                value = row.select_one(".pi-data-value")
                if label and value:
                    key = label.get_text(strip=True)
                    val = value.get_text(" ", strip=True)
                    fields.append((key, val))

        return {
            "title": title,
            "image_url": image_url,
            "url": url,
            "fields": fields
        }

    @commands.command(name="weapon")
    async def weapon(self, ctx, *, weapon_name: str):
        """Search for a weapon in Elden Ring."""
        data = await self.fetch_wiki_data(weapon_name)

        if not data:
            return await ctx.send("❌ No data found.")

        print(f"🧩 FELTER FRA INFOBOKS FOR {data['title']}:")
        for key, val in data["fields"]:
            print(f" - {key}: {val}")

        embed = discord.Embed(
            title=data["title"],
            url=data["url"],
            description="Information pulled from the Elden Ring Wiki.",
            color=discord.Color.dark_gold()
        )

        if data["image_url"]:
            embed.set_thumbnail(url=data["image_url"])

        # Viktige felt vi ønsker å hente ut
        important_keys = [
            "type", "attack", "skill", "fp cost", "weight", "sell price",
            "attack power", "guard", "passive", "scaling", "required", "dex", "str"
        ]

        found_important = False

        for key, val in data["fields"]:
            normalized_key = key.lower()
            if any(imp in normalized_key for imp in important_keys):
                embed.add_field(name=key, value=val, inline=False)
                found_important = True

        # Hvis ingen viktige felt ble funnet, vis de første 5
        if not found_important:
            for key, val in data["fields"][:5]:
                embed.add_field(name=key, value=val, inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="spell")
    async def spell(self, ctx, *, spell_name: str):
        """Search for a spell in Elden Ring."""
        data = await self.fetch_wiki_data(spell_name)

        if not data:
            return await ctx.send("❌ No data found.")

        print(f"🧩 FELTER FRA SPELL INFOBOKS FOR {data['title']}:")
        for key, val in data["fields"]:
            print(f" - {key}: {val}")

        embed = discord.Embed(
            title=data["title"],
            url=data["url"],
            description="Information pulled from the Elden Ring Wiki.",
            color=discord.Color.blurple()
        )

        if data["image_url"]:
            embed.set_thumbnail(url=data["image_url"])

        # Spell-spesifikke viktige felter
        important_keys = [
            "type", "slot", "fp cost", "effect", "required", "int", "faith", "arc", "incant", "cast time"
        ]

        found_important = False

        for key, val in data["fields"]:
            normalized_key = key.lower()
            if any(imp in normalized_key for imp in important_keys):
                embed.add_field(name=key, value=val, inline=False)
                found_important = True

        if not found_important:
            for key, val in data["fields"][:5]:
                embed.add_field(name=key, value=val, inline=False)

        await ctx.send(embed=embed)



async def setup(bot):
    await bot.add_cog(EldenRing(bot))
