import json
import os
import re
from redbot.core import commands
from redbot.core.bot import Red


class FilterCog(commands.Cog):
    """Persistent keyword filter with import/export support."""

    def __init__(self, bot: Red):
        self.bot = bot

        self.file_path = os.path.join(
            os.path.dirname(__file__),
            "banned_words.json"
        )

        self.banned_keywords = []
        self.banned_patterns = []

        self.load_words()

    # ----------------------------
    # FILE HANDLING
    # ----------------------------

    def load_words(self):
        """Load banned words from file."""
        if not os.path.exists(self.file_path):
            self.save_words()
            return

        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)

            self.banned_keywords = data.get("keywords", [])
            self.banned_patterns = data.get("patterns", [])

        except Exception:
            self.banned_keywords = []
            self.banned_patterns = []

    def save_words(self):
        """Save banned words to file."""
        data = {
            "keywords": self.banned_keywords,
            "patterns": self.banned_patterns
        }

        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=4)

    # ----------------------------
    # EMBED EXTRACTION
    # ----------------------------

    def extract_embed_text(self, message):
        text = ""

        for embed in message.embeds:
            if embed.title:
                text += embed.title + " "
            if embed.description:
                text += embed.description + " "

            for field in getattr(embed, "fields", []):
                text += field.name + " " + field.value + " "

        return text

    # ----------------------------
    # FILTER CORE
    # ----------------------------

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild:
            return

        content = (message.content or "").lower()
        embeds = self.extract_embed_text(message).lower()

        full_text = content + " " + embeds

        # keyword check
        for word in self.banned_keywords:
            if word.lower() in full_text:
                try:
                    await message.delete()
                except:
                    pass
                return

        # regex check
        for pattern in self.banned_patterns:
            if re.search(pattern, full_text):
                try:
                    await message.delete()
                except:
                    pass
                return

    # ----------------------------
    # COMMANDS
    # ----------------------------

    @commands.group()
    async def filter(self, ctx):
        """Manage banned words."""
        pass

    @filter.command()
    async def add(self, ctx, *, word: str):
        """Add a banned word."""
        word = word.lower()

        if word not in self.banned_keywords:
            self.banned_keywords.append(word)
            self.save_words()
            await ctx.send(f"Added banned word: `{word}`")

    @filter.command()
    async def remove(self, ctx, *, word: str):
        """Remove a banned word."""
        word = word.lower()

        if word in self.banned_keywords:
            self.banned_keywords.remove(word)
            self.save_words()
            await ctx.send(f"Removed banned word: `{word}`")

    @filter.command()
    async def list(self, ctx):
        """List banned words."""
        if not self.banned_keywords:
            await ctx.send("No banned words set.")
            return

        await ctx.send("Banned words:\n" + "\n".join(self.banned_keywords))

    @filter.command()
    async def addregex(self, ctx, *, pattern: str):
        """Add regex pattern."""
        self.banned_patterns.append(pattern)
        self.save_words()
        await ctx.send(f"Added regex pattern: `{pattern}`")

    @filter.command()
    async def removeregex(self, ctx, *, pattern: str):
        """Remove regex pattern."""
        if pattern in self.banned_patterns:
            self.banned_patterns.remove(pattern)
            self.save_words()
            await ctx.send(f"Removed regex pattern: `{pattern}`")