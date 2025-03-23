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
        self.num_emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']

    async def cog_load(self):
        await self.fetch_page()

    async def fetch_page(self):
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
        Search Discord.py docs, show 10 results, and let user pick via reaction.
        """
        await ctx.trigger_typing()
        if not self.page_content:
            await self.fetch_page()

        soup = BeautifulSoup(self.page_content, 'html.parser')
        sections = soup.find_all(['dt', 'dd'])
        base_url = "https://discordpy.readthedocs.io/en/stable/"

        matches = []

        for sec in sections:
            text = sec.get_text().lower()
            if query.lower() in text:
                # Attempt to get link anchor
                parent = sec.find_previous('dt')
                if parent and parent.find('a', class_='headerlink'):
                    link = base_url + parent.find('a', class_='headerlink')['href']
                else:
                    link = self.doc_url

                snippet = sec.get_text()[:400]
                matches.append((snippet, link))

            if len(matches) >= 10:
                break

        if not matches:
            embed = discord.Embed(
                title="No Results Found",
                description=f"No matches found. Check the [docs here]({self.doc_url}).",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Create embed with numbered results
        embed = discord.Embed(
            title=f"Results for: {query}",
            description="React with the corresponding number to select a result!",
            color=discord.Color.blurple()
        )

        for idx, (snippet, _) in enumerate(matches):
            embed.add_field(
                name=f"{self.num_emojis[idx]} Result {idx+1}",
                value=f"```{snippet[:200]}...```",  # Trim snippet
                inline=False
            )

        message = await ctx.send(embed=embed)

        # Add reactions
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
                title=f"Result {choice_index+1} Selected",
                description=f"[Click here to view documentation section]({chosen_link})",
                color=discord.Color.green()
            )
            await message.edit(embed=result_embed)
            await message.clear_reactions()

        except asyncio.TimeoutError:
            timeout_embed = discord.Embed(
                title="Timeout",
                description="No selection made in time. Please try again.",
                color=discord.Color.red()
            )
            await message.edit(embed=timeout_embed)
            await message.clear_reactions()

# Setup function
async def setup(bot):
    await bot.add_cog(DiscordPySearcher(bot))
