import json
import os
import re
from redbot.core import commands
from redbot.core.bot import Red


class FilterCog(commands.Cog):
    """Persistent keyword + regex filter with multi-role whitelist support."""

    def __init__(self, bot: Red):
        self.bot = bot

        self.file_path = os.path.join(
            os.path.dirname(__file__),
            "banned_words.json"
        )

        self.banned_keywords = []
        self.banned_patterns = []
        self.immune_role_ids = []

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
            self.immune_role_ids = data.get("immune_role_ids", [])

        except Exception:
            self.banned_keywords = []
            self.banned_patterns = []
            self.immune_role_ids = []

    def save_words(self):
        data = {
            "keywords": self.banned_keywords,
            "patterns": self.banned_patterns,
            "immune_role_ids": self.immune_role_ids
        }

        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=4)

    # =========================
    # EMBED EXTRACTION
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
    # MAIN FILTER LOGIC
    # =========================

    @commands.Cog.listener()
    async def on_message(self, message):

        if not message.guild:
            return

        # ---- whitelist role check (MULTI ROLE) ----
        if self.immune_role_ids:
            for role in message.author.roles:
                if role.id in self.immune_role_ids:
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

    # ---- MULTI WHITELIST ROLES ----

    @filter.command()
    async def whitelist(self, ctx, role: commands.RoleConverter = None):
        """
        Toggle whitelist role.
        - Add if not present
        - Remove if already present
        - No role = clear all
        """

        if role is None:
            self.immune_role_ids = []
            self.save_words()
            await ctx.send("🟡 Cleared all whitelist roles.")
            return

        if role.id in self.immune_role_ids:
            self.immune_role_ids.remove(role.id)
            self.save_words()
            await ctx.send(f"❌ Removed whitelist role: {role.name}")
        else:
            self.immune_role_ids.append(role.id)
            self.save_words()
            await ctx.send(f"🟢 Added whitelist role: {role.name}")
