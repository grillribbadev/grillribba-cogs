import discord
from discord.ext import commands
import aiohttp
from bs4 import BeautifulSoup
import urllib.parse


class EldenRing(commands.Cog):
    """Search for Elden Ring weapons, spells, and more from the Fandom wiki."""

    def __init__(self, bot):
        self.bot = bot

    async def search_wiki(self, query: str):
        base_search_url = "https://eldenring.fandom.com/wiki/Special:Search"
        params = {"query": query}
        search_url = f"{base_search_url}?{urllib.parse.urlencode(params)}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(search_url) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        result = soup.select_one(".unified-search__result__title")

        if result and result.has_attr("href"):
            return result.get_text(strip=True), result["href"]
        return None

    async def fetch_wiki_data(self, name: str):
        base_url = "https://eldenring.fandom.com/wiki/"
        search_name = name.strip().title().replace(" ", "_")
        url = base_url + search_name

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        soup = None
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as resp:
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")

                if (
                    resp.status == 404
                    or "Create the page" in html
                    or "search results" in html.lower()
                    or not soup.select_one(".portable-infobox")
                ):
                    print("⚠️ Page not valid. Searching instead...")
                    search = await self.search_wiki(name)
                    if not search:
                        return None
                    _, url = search
                    async with session.get(url) as new_resp:
                        if new_resp.status != 200:
                            return None
                        html = await new_resp.text()
                        soup = BeautifulSoup(html, "html.parser")

        title = soup.title.string.replace(" | Elden Ring Wiki | Fandom", "") if soup.title else "Unknown Title"
        image = soup.select_one(".portable-infobox img")
        image_url = image["src"] if image else None

        # Extract general fields
        infobox = soup.select_one(".portable-infobox")
        fields = []
        if infobox:
            for row in infobox.select(".pi-data"):
                label = row.select_one(".pi-data-label")
                value = row.select_one(".pi-data-value")
                if label and value:
                    key = label.get_text(strip=True)
                    val = value.get_text(" ", strip=True)
                    fields.append((key, val))

        # Extract from "Stats" table
        attributes = {}
        scaling = {}

        for h in soup.find_all(["h2", "h3"]):
            span = h.find("span", class_="mw-headline")
            if span and "stats" in span.text.lower():
                stats_table = h.find_next("table", class_="wikitable")
                if stats_table:
                    rows = stats_table.find_all("tr")[1:]  # Skip headers
                    for row in rows:
                        cols = row.find_all("td")
                        if len(cols) >= 3:
                            stat_name = cols[0].get_text(strip=True)
                            requirement = cols[1].get_text(strip=True)
                            scale = cols[2].get_text(strip=True)

                            if requirement and requirement != "-":
                                attributes[stat_name] = requirement
                            if scale and scale != "-":
                                scaling[stat_name] = scale
                break

        return {
            "title": title,
            "image_url": image_url,
            "url": url,
            "fields": fields,
            "attributes": attributes,
            "scaling": scaling
        }

    @commands.command(name="weapon")
    async def weapon(self, ctx, *, weapon_name: str):
        """Search for a weapon in Elden Ring."""
        data = await self.fetch_wiki_data(weapon_name)

        if not data:
            return await ctx.send("❌ No data found for that weapon.")

        embed = discord.Embed(
            title=data["title"],
            url=data["url"],
            description="Information retrieved from the Elden Ring Wiki.",
            color=discord.Color.dark_gold()
        )

        if data["image_url"]:
            embed.set_thumbnail(url=data["image_url"])

        if data["attributes"]:
            embed.add_field(
                name="Required Attributes",
                value="\n".join(f"**{k}**: {v}" for k, v in data["attributes"].items()),
                inline=False
            )

        if data["scaling"]:
            embed.add_field(
                name="Scaling",
                value="\n".join(f"**{k}**: {v}" for k, v in data["scaling"].items()),
                inline=False
            )

        important_keys = [
            "type", "attack", "skill", "fp cost", "weight", "sell price",
            "attack power", "guard", "passive"
        ]

        added_keys = set()
        for key, val in data["fields"]:
            norm_key = key.lower()
            if any(k in norm_key for k in important_keys) and norm_key not in added_keys:
                embed.add_field(name=key, value=val, inline=False)
                added_keys.add(norm_key)

        await ctx.send(embed=embed)

    @commands.command(name="spell")
    async def spell(self, ctx, *, spell_name: str):
        """Search for a spell in Elden Ring."""
        data = await self.fetch_wiki_data(spell_name)

        if not data:
            return await ctx.send("❌ No data found for that spell.")

        embed = discord.Embed(
            title=data["title"],
            url=data["url"],
            description="Information retrieved from the Elden Ring Wiki.",
            color=discord.Color.blurple()
        )

        if data["image_url"]:
            embed.set_thumbnail(url=data["image_url"])

        if data["attributes"]:
            embed.add_field(
                name="Required Attributes",
                value="\n".join(f"**{k}**: {v}" for k, v in data["attributes"].items()),
                inline=False
            )

        if data["scaling"]:
            embed.add_field(
                name="Scaling",
                value="\n".join(f"**{k}**: {v}" for k, v in data["scaling"].items()),
                inline=False
            )

        important_keys = [
            "type", "slot", "fp cost", "effect", "required", "intelligence",
            "faith", "arcane", "incantation", "cast time"
        ]

        added_keys = set()
        for key, val in data["fields"]:
            norm_key = key.lower()
            if any(k in norm_key for k in important_keys) and norm_key not in added_keys:
                embed.add_field(name=key, value=val, inline=False)
                added_keys.add(norm_key)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(EldenRing(bot))
