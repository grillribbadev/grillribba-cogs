import discord
import os
import random
import json
import asyncio
from redbot.core import commands, Config
from redbot.core.bot import Red


class TriviaCog(commands.Cog):
    """A cog for running trivia games."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=123456789)
        self.questions = {}  # Holds questions loaded from the JSON file
        self.active_games = {}  # Tracks active games per channel
        self.allowed_channels = set()  # Tracks allowed channels for trivia
        self.load_questions()

    def load_questions(self):
        """Load questions from the JSON file."""
        file_path = os.path.join(os.path.dirname(__file__), "trivia_questions.json")
        try:
            with open(file_path, "r") as file:
                self.questions = json.load(file)
        except FileNotFoundError:
            self.questions = {}
            print("Trivia questions file not found. Please create a `trivia_questions.json` file.")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON: {e}")
            self.questions = {}
        except Exception as e:
            print(f"Unexpected error: {e}")
            self.questions = {}

    @commands.group(invoke_without_command=True)
    async def trivia(self, ctx, category: str):
        """Start a trivia game with the specified category."""
        channel = ctx.channel

        if channel.id not in self.allowed_channels:
            await ctx.send("Trivia is not allowed in this channel. Please ask an admin to enable it.")
            return

        trivia_role = discord.utils.get(ctx.guild.roles, name="Trivia")
        if trivia_role not in ctx.author.roles:
            await ctx.send("You must have the `Trivia` role to start a game.")
            return

        if category not in self.questions:
            available_categories = ", ".join(self.questions.keys())
            await ctx.send(f"Invalid category. Available categories: {available_categories}")
            return

        question_set = self.questions[category]
        if not question_set:
            await ctx.send("The selected category has no questions.")
            return

        if channel.id in self.active_games:
            await ctx.send(f"A trivia game is already active in {channel.mention}.")
            return

        self.active_games[channel.id] = {"host": ctx.author, "scores": {}, "running": True}

        # Allow role pings
        allowed_mentions = discord.AllowedMentions(roles=True)
        await ctx.send(
            f"{trivia_role.mention} A new trivia game is starting in {channel.mention}! 🎉",
            allowed_mentions=allowed_mentions,
        )

        await self.run_trivia_game(ctx, channel, question_set)

    async def run_trivia_game(self, ctx, channel, question_set):
        """Run the trivia game."""
        random.shuffle(question_set)  # Shuffle the questions
        used_questions = set()  # Track already used questions
        scores = {}
        game_data = self.active_games[channel.id]

        for _ in range(min(25, len(question_set))):  # Limit to 25 questions
            if not game_data["running"]:
                break

            # Pick a unique question
            question = None
            while question_set and question is None:
                potential_question = question_set.pop(0)
                question_id = potential_question.get("question")  # Use question text as a unique identifier
                if question_id not in used_questions:
                    question = potential_question
                    used_questions.add(question_id)

            if not question:  # If no unique questions remain, exit the loop
                break

            question_text = question.get("question")
            correct_answers = [answer.lower() for answer in question.get("answer", [])]
            if not question_text or not correct_answers:
                continue

            embed = discord.Embed(title="Trivia Question", description=question_text, color=discord.Color.blue())
            await channel.send(embed=embed)

            def check(msg):
                return msg.channel == channel and msg.content.lower() in correct_answers

            try:
                response = await self.bot.wait_for("message", timeout=30.0, check=check)
                if response.author not in scores:
                    scores[response.author] = 0
                scores[response.author] += 1
                await channel.send(f"✅ Correct! {response.author.mention} gains a point!")
            except asyncio.TimeoutError:
                await channel.send(f"⏰ Time's up! The correct answer(s) were: **{', '.join(correct_answers)}**.")

        # Announce the winner
        if game_data["running"]:
            await self.announce_winner(channel, scores)

        del self.active_games[channel.id]

    @trivia.command(name="stop")
    @commands.admin_or_permissions(administrator=True)
    async def stop_trivia(self, ctx):
        """Stop an ongoing trivia game in the current channel."""
        channel = ctx.channel
        if channel.id not in self.active_games:
            await ctx.send(f"There is no active trivia game in {channel.mention}.")
            return

        game_data = self.active_games[channel.id]
        if ctx.author != game_data["host"] and not ctx.author.guild_permissions.administrator:
            await ctx.send("Only the trivia host or an admin can stop the game.")
            return

        game_data["running"] = False
        scores = game_data["scores"]

        await ctx.send(f"🛑 The trivia game in {channel.mention} has been stopped.")
        await self.announce_winner(channel, scores)
        del self.active_games[channel.id]

    async def announce_winner(self, channel, scores):
        """Announce the winner of a trivia game."""
        if not scores:
            await channel.send("No one scored any points. Better luck next time!")
            return

        leaderboard = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        embed = discord.Embed(title="Trivia Game Over!", color=discord.Color.gold())
        embed.description = "\n".join(
            f"{idx + 1}. {user} - {score} points" for idx, (user, score) in enumerate(leaderboard)
        )
        await channel.send(embed=embed)

    @trivia.command(name="allow")
    @commands.admin_or_permissions(administrator=True)
    async def allow_channel(self, ctx, channel: discord.TextChannel = None):
        """Allow trivia games in a specific channel."""
        channel = channel or ctx.channel
        self.allowed_channels.add(channel.id)
        await ctx.send(f"✅ Trivia games are now allowed in {channel.mention}.")

    @trivia.command(name="deny")
    @commands.admin_or_permissions(administrator=True)
    async def deny_channel(self, ctx, channel: discord.TextChannel = None):
        """Deny trivia games in a specific channel."""
        channel = channel or ctx.channel
        if channel.id in self.allowed_channels:
            self.allowed_channels.remove(channel.id)
            await ctx.send(f"❌ Trivia games are no longer allowed in {channel.mention}.")
        else:
            await ctx.send(f"Trivia games are already not allowed in {channel.mention}.")

    @trivia.command(name="categories")
    async def list_categories(self, ctx):
        """List available trivia categories."""
        if not self.questions:
            await ctx.send("No trivia questions are loaded.")
            return

        categories = ", ".join(self.questions.keys())
        await ctx.send(f"Available categories: {categories}")

    @trivia.command(name="addcategory")
    async def add_category(self, ctx, category: str):
        """Add a new trivia category."""
        if category in self.questions:
            await ctx.send(f"The category `{category}` already exists.")
            return

        self.questions[category] = []
        try:
            file_path = os.path.join(os.path.dirname(__file__), "trivia_questions.json")
            with open(file_path, "w") as file:
                json.dump(self.questions, file, indent=4)
            await ctx.send(f"Category `{category}` has been added successfully.")
        except Exception as e:
            await ctx.send(f"Failed to add category: {e}")

    @trivia.command(name="deletecategory")
    @commands.admin_or_permissions(administrator=True)
    async def delete_category(self, ctx, category: str):
        """Delete an existing trivia category."""
        if category not in self.questions:
            await ctx.send(f"The category `{category}` does not exist.")
            return

        del self.questions[category]
        try:
            file_path = os.path.join(os.path.dirname(__file__), "trivia_questions.json")
            with open(file_path, "w") as file:
                json.dump(self.questions, file, indent=4)
            await ctx.send(f"Category `{category}` has been deleted successfully.")
        except Exception as e:
            await ctx.send(f"Failed to delete category: {e}")

    @commands.command()
    async def add_question(self, ctx, category: str, *, content: str):
        """
        Add a new trivia question with multiple possible answers.
        Example: .add_question <category> | What is Luffy's dream? | Pirate King,King of Pirates
        """
        if "|" not in content:
            await ctx.send("Invalid format! Use: `<category> | <question> | <answer1>,<answer2>,<answer3>`.")
            return

        try:
            question_part, answers_part = [part.strip() for part in content.split("|", 1)]
            answers = [answer.strip().lower() for answer in answers_part.split(",")]
            if not answers:
                await ctx.send("You must provide at least one answer.")
                return

            if category not in self.questions:
                self.questions[category] = []

            self.questions[category].append({"question": question_part, "answer": answers})

            # Save to JSON file
            file_path = os.path.join(os.path.dirname(__file__), "trivia_questions.json")
            with open(file_path, "w") as file:
                json.dump(self.questions, file, indent=4)

            await ctx.send(f"Question added successfully to `{category}` with {len(answers)} possible answer(s).")
        except Exception as e:
            await ctx.send(f"Failed to add question: {e}")

    @commands.command()
    async def triviarole(self, ctx):
        """
        Send an embed for users to get or remove the Trivia role by reacting.
        """
        role_name = "Trivia"
        guild = ctx.guild
        role = discord.utils.get(guild.roles, name=role_name)

        if not role:
            await ctx.send(f"The `{role_name}` role does not exist. Please create it first.")
            return

        embed = discord.Embed(
            title="Get or Remove the Trivia Role",
            description="React with 🎮 to get the Trivia role or remove it if you already have it.",
            color=discord.Color.green()
        )
        embed.set_footer(text="React to toggle the Trivia role.")

        # Send the embed
        message = await ctx.send(embed=embed)
        await message.add_reaction("🎮")

        # Define the check for the reaction
        def check(reaction, user):
            return (
                reaction.message.id == message.id
                and str(reaction.emoji) == "🎮"
                and not user.bot
            )

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=315569260.0, check=check)

                # Add or remove the role
                member = guild.get_member(user.id)
                if role in member.roles:
                    await member.remove_roles(role)
                    confirmation = await user.send("✅ The Trivia role has been removed.")
                else:
                    await member.add_roles(role)
                    confirmation = await user.send("✅ You now have the Trivia role.")

                # Delete confirmation after 5 seconds
                await asyncio.sleep(5)
                await confirmation.delete()

                # Remove the user's reaction
                await message.remove_reaction(reaction.emoji, user)

            except asyncio.TimeoutError:
                break  # Exit the loop after 10 years of no reactions

        await ctx.send("The reaction-based Trivia role assignment has ended.")
