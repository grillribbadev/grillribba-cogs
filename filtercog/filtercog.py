import json
import os
import re
from redbot.core import commands
from redbot.core.bot import Red


class FilterCog(commands.Cog):
    """Persistent keyword + regex filter with whitelist role support."""

    def __init__(self, bot: Red):
        self.bot = bot

        self.file_path = os.path.join(
            os.path.dirname(__file__),
            "banned_words.json"
        )

        self.banned_keywords = []
        self.banned_patterns = []
        self.immune_role_id = None

        self.load_words()

    # =========================
    # FILE HANDLING
    # =========================

    def load_words(self):
        if not os.path.exists(self.file_path):
            self.save_words()
            return

        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)

            self.banned_keywords = data.get("keywords", [])
            self.banned_patterns = data.get("patterns", [])
            self.immune_role_id = data.get("immune_role_id", None)

        except Exception:
            self.banned_keywords = []
            self.banned_patterns = []
            self.immune_role_id = None

    def save_words(self):
        data = {
            "keywords": self.banned_keywords,
            "patterns": self.banned_patterns,
            "immune_role_id": self.immune_role_id
        }

        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=4)

    # =========================
    # EMBED PARSER
    # =========================

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

    # =========================
    # MAIN FILTER
    # =========================

    @commands.Cog.listener()
    async def on_message(self, message):

        if not message.guild:
            return

        # ---- whitelist role check ----
        if self.immune_role_id:
            role = message.guild.get_role(self.immune_role_id)
            if role and role in message.author.roles:
                return

        content = (message.content or "").lower()
        embeds = self.extract_embed_text(message).lower()

        full_text = content + " " + embeds

        # ---- keyword check ----
        for word in self.banned_keywords:
            if word.lower() in full_text:
                try:
                    await message.delete()
                except:
                    pass
                return

        # ---- regex check ----
        for pattern in self.banned_patterns:
            if re.search(pattern, full_text):
                try:
                    await message.delete()
                except:
                    pass
                return

    # =========================
    # COMMAND GROUP
    # =========================

    @commands.group()
    async def filter(self, ctx):
        """Manage message filter system."""
        pass

    # ---- keyword commands ----

    @filter.command()
    async def add(self, ctx, *, word: str):
        word = word.lower()

        if word not in self.banned_keywords:
            self.banned_keywords.append(word)
            self.save_words()
            await ctx.send(f"✅ Added keyword: `{word}`")

    @filter.command()
    async def remove(self, ctx, *, word: str):
        word = word.lower()

        if word in self.banned_keywords:
            self.banned_keywords.remove(word)
            self.save_words()
            await ctx.send(f"❌ Removed keyword: `{word}`")

    @filter.command()
    async def list(self, ctx):
        if not self.banned_keywords:
            await ctx.send("No keywords set.")
            return

        await ctx.send("**Banned keywords:**\n" + "\n".join(self.banned_keywords))

    # ---- regex commands ----

    @filter.command()
    async def addregex(self, ctx, *, pattern: str):
        self.banned_patterns.append(pattern)
        self.save_words()
        await ctx.send(f"✅ Added regex: `{pattern}`")

    @filter.command()
    async def removeregex(self, ctx, *, pattern: str):
        if pattern in self.banned_patterns:
            self.banned_patterns.remove(pattern)
            self.save_words()
            await ctx.send(f"❌ Removed regex: `{pattern}`")

    # ---- whitelist role ----

    @filter.command()
    async def whitelist(self, ctx, role: commands.RoleConverter = None):
        """
        Set or clear whitelist role.
        """

        if role is None:
            self.immune_role_id = None
            self.save_words()
            await ctx.send("🟡 Whitelist role cleared.")
            return

        self.immune_role_id = role.id
        self.save_words()
        await ctx.send(f"🟢 Whitelist role set to: {role.name}")
