from redbot.core import commands
import aiohttp
from bs4 import BeautifulSoup
import discord
import asyncio

class DiscordPySearcher(commands.Cog):
    """Search and fetch Discord.py documentation info with interactive selection."""

    def __init__(self, bot):
        self.bot = bot
        self.page_content = None
        self.doc_url = "https://discordpy.readthedocs.io/en/stable/api.html"
        self.base_url = "https://discordpy.readthedocs.io/en/stable/"
        self.num_emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']

    async def cog_load(self):
        """Pre-load the documentation page when the cog loads."""
        await self.fetch_page()

    async def fetch_page(self):
        """Fetch the Discord.py documentation HTML if not already cached."""
        if self.page_content:
            return self.page_content

        async with aiohttp.ClientSession() as session:
            async with session.get(self.doc_url) as resp:
                if resp.status != 200:
                    return None
                self.page_content = await resp.text()
                return self.page_content

    @commands.command(name="pysearch")
    async def pysearch(self, ctx, *, query: str):
        """
        Search Discord.py documentation and show up to 10 results.
        Users can select a result via reaction to view its direct link.
        """
        async with ctx.typing():
            if not self.page_content:
                await self.fetch_page()

            soup = BeautifulSoup(self.page_content, 'html.parser')
            sections = soup.find_all(['dt', 'dd'])

            matches = []

            for sec in sections:
                text = sec.get_text().lower()
                if query.lower() in text:
                    # Attempt to get an anchor link for context
                    parent = sec.find_previous('dt')
                    if parent and parent.find('a', class_='headerlink'):
                        link = self.base_url + parent.find('a', class_='headerlink')['href']
                    else:
                        link = self.doc_url

                    snippet = sec.get_text()[:400]
                    matches.append((snippet.strip(), link))

                if len(matches) >= 10:
                    break

        if not matches:
            embed = discord.Embed(
                title="❌ No Results Found",
                description=f"No matches for `{query}`.\n[Check the full docs here]({self.doc_url})",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Build embed showing top 10 results
        embed = discord.Embed(
            title=f"🔍 Search Results for: {query}",
            description="React with a number (1-10) to select a result.",
            color=discord.Color.blurple()
        )

        for idx, (snippet, _) in enumerate(matches):
            embed.add_field(
                name=f"{self.num_emojis[idx]} Result {idx + 1}",
                value=f"```{snippet[:200]}...```",
                inline=False
            )

        message = await ctx.send(embed=embed)

        # Add numbered reactions
        for i in range(len(matches)):
            await message.add_reaction(self.num_emojis[i])

        def check(reaction, user):
            return (
                user == ctx.author and
                reaction.message.id == message.id and
                str(reaction.emoji) in self.num_emojis[:len(matches)]
            )

        try:
            reaction, _ = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
            choice_index = self.num_emojis.index(str(reaction.emoji))
            chosen_snippet, chosen_link = matches[choice_index]

            result_embed = discord.Embed(
                title=f"📘 Result {choice_index + 1} Selected",
                description=f"[Click here to view this section]({chosen_link})",
                color=discord.Color.green()
            )
            result_embed.add_field(
                name="Snippet",
                value=f"```{chosen_snippet[:400]}...```",
                inline=False
            )

            await message.edit(embed=result_embed)
            await message.clear_reactions()

        except asyncio.TimeoutError:
            timeout_embed = discord.Embed(
                title="⌛ Timed Out",
                description="You didn’t react in time. Try the command again.",
                color=discord.Color.red()
            )
            await message.edit(embed=timeout_embed)
            await message.clear_reactions()
