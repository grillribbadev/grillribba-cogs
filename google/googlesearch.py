from redbot.core import commands, Config
import aiohttp
import discord

class GoogleSearch(commands.Cog):
    """Search Google using SerpAPI and return top results."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7654321, force_registration=True)
        self.config.register_global(api_key=None)

    @commands.command()
    @commands.is_owner()
    async def setserpapi(self, ctx, key: str):
        """Set your SerpAPI key (owner only)."""
        await self.config.api_key.set(key)
        await ctx.send("✅ SerpAPI key set.")

    @commands.command(name="google")
    async def google(self, ctx, *, query: str):
        """Search Google and show top 3 results using SerpAPI."""
        api_key = await self.config.api_key()
        if not api_key:
            await ctx.send("❌ API key not set. Use `[p]setserpapi <your_key>` to set it.")
            return

        await ctx.send("🔍 Searching Google...")

        url = "https://serpapi.com/search.json"
        params = {
            "q": query,
            "api_key": api_key,
            "num": 3
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        await ctx.send("❌ Failed to contact SerpAPI.")
                        return
                    data = await resp.json()
        except Exception as e:
            await ctx.send(f"⚠️ Error: `{e}`")
            return

        results = data.get("organic_results", [])[:3]

        if not results:
            await ctx.send("❌ No results found.")
            return

        embed = discord.Embed(
            title=f"🔎 Google Results for: {query}",
            color=discord.Color.green()
        )

        for i, result in enumerate(results, 1):
            title = result.get("title", "No title")
            link = result.get("link", "")
            snippet = result.get("snippet", "*No description*")
            embed.add_field(
                name=f"{i}. {title}",
                value=f"[Link]({link})\n{snippet}",
                inline=False
            )

        await ctx.send(embed=embed)
