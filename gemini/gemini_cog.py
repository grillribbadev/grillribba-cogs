import discord
from redbot.core import commands, Config
import google.generativeai as gemini  # Import Google Generative AI SDK


class Gemini(commands.Cog):
    """Interact with fictional characters using Google Gemini API."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543211)
        default_global = {"api_key": None, "character_profiles": {}}
        self.config.register_global(**default_global)

    async def red_delete_data_for_user(self, **kwargs):
        """Handle data deletion requests (not applicable here)."""
        pass

    # ==============================
    # COMMANDS
    # ==============================
    @commands.group()
    async def gemini(self, ctx):
        """Gemini Character Interaction settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @gemini.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setapikey(self, ctx, api_key: str):
        """Set the Google Gemini API key."""
        if not api_key.startswith("gm-"):  # Assuming Gemini API keys start with "gm-"
            await ctx.send("Invalid API key. Make sure you're using the correct Google Gemini key.")
            return
        await self.config.api_key.set(api_key)
        await ctx.send("The Google Gemini API key has been successfully set!")

    @gemini.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def addcharacter(self, ctx, name: str, *, description: str):
        """Add a character profile."""
        profiles = await self.config.character_profiles()
        name_lower = name.lower()
        if name_lower in profiles:
            await ctx.send(f"Character `{name}` already exists. Use a different name.")
            return
        profiles[name_lower] = description
        await self.config.character_profiles.set(profiles)
        await ctx.send(f"Character `{name}` has been added!")

    @gemini.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def removecharacter(self, ctx, name: str):
        """Remove a character profile."""
        profiles = await self.config.character_profiles()
        name_lower = name.lower()
        if name_lower not in profiles:
            await ctx.send(f"Character `{name}` does not exist.")
            return
        del profiles[name_lower]
        await self.config.character_profiles.set(profiles)
        await ctx.send(f"Character `{name}` has been removed!")

    @gemini.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def listcharacters(self, ctx):
        """List all available character profiles."""
        profiles = await self.config.character_profiles()
        if not profiles:
            await ctx.send("No character profiles have been added yet.")
            return
        character_list = "\n".join(f"- {name.capitalize()}" for name in profiles.keys())
        await ctx.send(f"Available characters:\n{character_list}")

    # ==============================
    # MESSAGE LISTENER
    # ==============================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages and check for character invocation."""
        if message.author.bot:
            return  # Ignore bot messages

        content = message.content.strip()
        profiles = await self.config.character_profiles()

        # Check if the first word matches a character name
        words = content.split(" ", 1)  # Split into name and rest of the message
        if len(words) < 2:
            return  # No question provided
        name, question = words[0].lower(), words[1]

        if name in profiles:
            # Character is invoked
            character_description = profiles[name]
            api_key = await self.config.api_key()

            if not api_key:
                await message.channel.send("The Google Gemini API key is not set.")
                return

            # Set the API key for the SDK
            gemini.configure(api_key=api_key)

            try:
                # Use Gemini's chat model
                response = gemini.chat(
                    model="models/chat-bison-001",  # Replace with appropriate Gemini model
                    messages=[
                        {"author": "system", "content": character_description},
                        {"author": "user", "content": question},
                    ],
                )
                # Extract the response
                answer = response["candidates"][0]["content"]
                await message.channel.send(f"**{name.capitalize()} says:** {answer}")

            except Exception as e:
                await message.channel.send(f"An error occurred: {e}")

    # ==============================
    # UTILITIES
    # ==============================
    async def get_character_description(self, name: str) -> str:
        """Fetch a character's description."""
        profiles = await self.config.character_profiles()
        return profiles.get(name.lower(), None)


# Cog setup function
async def setup(bot):
    """Standard setup function for Redbot."""
    await bot.add_cog(Gemini(bot))
