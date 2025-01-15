import discord
from redbot.core import commands, Config
import openai


class AICharacter(commands.Cog):
    """Interact with fictional characters using OpenAI API."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210)
        default_global = {"api_key": None, "character_profiles": {}}
        self.config.register_global(**default_global)

    async def red_delete_data_for_user(self, **kwargs):
        """Handle data deletion requests (not applicable here)."""
        pass

    # ==============================
    # COMMANDS
    # ==============================
    @commands.group()
    async def ai(self, ctx):
        """AI Character Interaction settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ai.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setapikey(self, ctx, api_key: str):
        """Set the OpenAI API key."""
        if not api_key.startswith("sk-"):
            await ctx.send("Invalid API key. Make sure you're using the correct OpenAI key.")
            return
        await self.config.api_key.set(api_key)
        await ctx.send("The OpenAI API key has been successfully set!")

    @ai.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def addcharacter(self, ctx, name: str, *, description: str):
        """Add a character profile."""
        profiles = await self.config.character_profiles()
        name_key = name.lower()  # Convert to lowercase for case-insensitive matching
        if name_key in profiles:
            await ctx.send(f"Character `{name}` already exists. Use a different name.")
            return
        profiles[name_key] = description
        await self.config.character_profiles.set(profiles)
        await ctx.send(f"Character `{name}` has been added!")

    @ai.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def removecharacter(self, ctx, name: str):
        """Remove a character profile."""
        profiles = await self.config.character_profiles()
        name_key = name.lower()  # Convert to lowercase for case-insensitive matching
        if name_key not in profiles:
            await ctx.send(f"Character `{name}` does not exist.")
            return
        del profiles[name_key]
        await self.config.character_profiles.set(profiles)
        await ctx.send(f"Character `{name}` has been removed!")

    @ai.command()
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

        content = message.content.lower().strip()  # Convert to lowercase for case-insensitive matching
        profiles = await self.config.character_profiles()

        # Check if any character name is mentioned in the message
        for name, description in profiles.items():
            if name in content:  # Check if the character's name exists anywhere in the message
                # Extract the question by removing the character's name from the message
                question = content.replace(name, "").strip()

                api_key = await self.config.api_key()
                if not api_key:
                    await message.channel.send("The OpenAI API key is not set.")
                    return

                openai.api_key = api_key

                try:
                    # Roleplay as the matched character
                    system_message = (
                        f"You are now roleplaying as {name.capitalize()}. "
                        f"{description} "
                        "Stay completely in character. Answer questions, engage in conversation, "
                        "and behave as this character would in their world. Avoid breaking character "
                        "or talking about being an AI."
                    )

                    response = await openai.ChatCompletion.acreate(
                        model="gpt-4",
                        messages=[
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": question},
                        ],
                        max_tokens=150,
                        temperature=0.9,
                    )
                    answer = response["choices"][0]["message"]["content"].strip()
                    await message.channel.send(f"**{name.capitalize()} says:** {answer}")

                except openai.AuthenticationError:
                    await message.channel.send("Authentication failed. Please check your OpenAI API key.")
                except openai.RateLimitError:
                    await message.channel.send("The OpenAI API is currently rate-limited. Please try again later.")
                except openai.APIError as e:
                    await message.channel.send(f"An API error occurred: {e}")
                except openai.OpenAIError as e:
                    await message.channel.send(f"An OpenAI error occurred: {e}")
                except Exception as e:
                    await message.channel.send(f"An unexpected error occurred: {e}")

                return  # Only respond to the first matched character

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
    await bot.add_cog(AICharacter(bot))
