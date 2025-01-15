import discord
from redbot.core import commands
from discord.ext import tasks
import os
import json


class QOTD(commands.Cog):
    """A QOTD cog for managing daily One Piece-themed questions."""

    def __init__(self, bot):
        self.bot = bot
        self.qotd_pool_file = "data/qotd/qotd_pool.json"
        self.suggestions_file = "data/qotd/qotd_suggestions.json"
        self.channels_file = "data/qotd/qotd_channels.json"
        self.current_question_file = "data/qotd/current_question.json"
        self.answered_users_file = "data/qotd/answered_users.json"
        self.attempts_file = "data/qotd/attempts.json"

        # Ensure data directories and files exist
        os.makedirs("data/qotd", exist_ok=True)
        self._ensure_file(self.qotd_pool_file, {})
        self._ensure_file(self.suggestions_file, {})
        self._ensure_file(self.channels_file, {})
        self._ensure_file(self.current_question_file, {})
        self._ensure_file(self.answered_users_file, {})
        self._ensure_file(self.attempts_file, {})

        self.qotd_task.start()

    def _ensure_file(self, file_name, default_data):
        """Ensure a JSON file exists and contains default data."""
        if not os.path.exists(file_name):
            with open(file_name, "w") as file:
                json.dump(default_data, file, indent=4)
        else:
            with open(file_name, "r") as file:
                try:
                    json.load(file)  # Check if the file is valid JSON
                except json.JSONDecodeError:
                    with open(file_name, "w") as file:
                        json.dump(default_data, file, indent=4)

    def _load_json(self, file_name):
        """Load data from a JSON file and ensure it's a dictionary."""
        with open(file_name, "r") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            data = {}
            self._save_json(file_name, data)
        return data

    def _save_json(self, file_name, data):
        """Save data to a JSON file."""
        with open(file_name, "w") as file:
            json.dump(data, file, indent=4)

    def cog_unload(self):
        self.qotd_task.cancel()

    async def restrict_user(self, channel: discord.TextChannel, user: discord.Member, reason: str):
        """Restrict a user from sending messages in the QOTD channel."""
        try:
            await channel.set_permissions(user, send_messages=False)
            feedback = await channel.send(f"🔒 {user.mention} {reason}")
            await feedback.delete(delay=5)  # Delete the feedback message
        except discord.Forbidden:
            pass  # Bot lacks permissions to restrict the user

    async def clear_restrictions(self, channel: discord.TextChannel):
        """Clear all user-specific permissions for the QOTD channel."""
        for overwrite in channel.overwrites:
            if isinstance(overwrite, discord.Member):
                try:
                    await channel.set_permissions(overwrite, overwrite=None)
                except discord.Forbidden:
                    pass  # Bot lacks permissions to clear permissions

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def setqotdch(self, ctx, channel: discord.TextChannel):
        """Set the QOTD channel."""
        data = self._load_json(self.channels_file)
        data[str(ctx.guild.id)] = data.get(str(ctx.guild.id), {})
        data[str(ctx.guild.id)]["qotd_channel"] = channel.id
        self._save_json(self.channels_file, data)
        await ctx.send(f"QOTD channel has been set to {channel.mention}.")

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def setreviewch(self, ctx, channel: discord.TextChannel):
        """Set the review channel."""
        data = self._load_json(self.channels_file)
        data[str(ctx.guild.id)] = data.get(str(ctx.guild.id), {})
        data[str(ctx.guild.id)]["review_channel"] = channel.id
        self._save_json(self.channels_file, data)
        await ctx.send(f"Review channel has been set to {channel.mention}.")

    @commands.guild_only()
    @commands.command()
    async def qotdsuggest(self, ctx, *, suggestion: str):
        """Suggest a QOTD question."""
        data = self._load_json(self.channels_file)
        review_channel_id = data.get(str(ctx.guild.id), {}).get("review_channel")

        if not review_channel_id:
            return await ctx.send("Review channel has not been set.")

        review_channel = ctx.guild.get_channel(review_channel_id)
        if not review_channel:
            return await ctx.send("Review channel is invalid.")

        embed = discord.Embed(
            title="New QOTD Suggestion",
            description=suggestion,
            color=discord.Color.blue(),
            timestamp=ctx.message.created_at
        )
        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar.url)
        message = await review_channel.send(embed=embed)

        await message.add_reaction("✅")
        await message.add_reaction("❌")
        await ctx.send("Your suggestion has been sent for review!")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        data = self._load_json(self.channels_file)
        guild_id = str(payload.guild_id)
        review_channel_id = data.get(guild_id, {}).get("review_channel")

        if not review_channel_id or payload.channel_id != review_channel_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        channel = guild.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        user = guild.get_member(payload.user_id)

        if not user or user.bot:
            return

        if str(payload.emoji) == "✅":
            await self.add_to_pool(guild_id, message)
            await message.delete()
            await channel.send(
                f"✅ Suggestion approved: **{message.embeds[0].description}**"
            )
        elif str(payload.emoji) == "❌":
            await message.delete()
            await channel.send(
                f"❌ Suggestion denied: **{message.embeds[0].description}**"
            )

    async def add_to_pool(self, guild_id, message):
        """Add a suggestion to the QOTD pool."""
        qotd_pool = self._load_json(self.qotd_pool_file)
        suggestion = message.embeds[0].description

        qotd_pool[guild_id] = qotd_pool.get(guild_id, [])
        qotd_pool[guild_id].append(suggestion)
        self._save_json(self.qotd_pool_file, qotd_pool)

    @commands.guild_only()
    @commands.command()
    async def qotdpool(self, ctx):
        """View the QOTD pool."""
        qotd_pool = self._load_json(self.qotd_pool_file)
        pool = qotd_pool.get(str(ctx.guild.id), [])

        if not pool:
            await ctx.send("The QOTD pool is empty.")
            return

        embed = discord.Embed(
            title="QOTD Pool",
            description="\n".join(pool),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def qotdstart(self, ctx):
        """Start the QOTD posting task."""
        channels_data = self._load_json(self.channels_file)
        qotd_pool_data = self._load_json(self.qotd_pool_file)
        current_question_data = self._load_json(self.current_question_file)
        answered_users_data = self._load_json(self.answered_users_file)
        attempts_data = self._load_json(self.attempts_file)
        guild_id = str(ctx.guild.id)

        qotd_channel_id = channels_data.get(guild_id, {}).get("qotd_channel")
        qotd_pool = qotd_pool_data.get(guild_id, [])

        if not qotd_channel_id:
            await ctx.send("QOTD channel is not set.")
            return

        if not qotd_pool:
            await ctx.send("The QOTD pool is empty. Add some questions using the review process.")
            return

        # Fetch and send the first question immediately
        channel = ctx.guild.get_channel(qotd_channel_id)
        if not channel:
            await ctx.send("The QOTD channel is invalid or inaccessible.")
            return

        # Clear all user restrictions from the previous QOTD
        await self.clear_restrictions(channel)

        first_question = qotd_pool.pop(0)  # Get the first question

        # Validate the question format
        if "&" not in first_question:
            await ctx.send(
                "The first question in the pool is improperly formatted (missing '&'). Skipping it."
            )
            self._save_json(self.qotd_pool_file, qotd_pool)  # Save updated pool
            return

        question, answer = first_question.split("&", 1)
        current_question_data[guild_id] = {"question": question.strip(), "answer": answer.strip()}
        answered_users_data[guild_id] = []  # Reset answered users for this QOTD
        attempts_data[guild_id] = {}  # Reset attempts for this QOTD
        self._save_json(self.current_question_file, current_question_data)
        self._save_json(self.answered_users_file, answered_users_data)
        self._save_json(self.attempts_file, attempts_data)

        # Mention the QOTD role
        qotd_role = discord.utils.get(ctx.guild.roles, name="QOTD")
        role_mention = qotd_role.mention if qotd_role else ""

        # Embed the QOTD
        embed = discord.Embed(
            title="🏴‍☠️ One Piece Question of the Day 🏴‍☠️",
            description=f"**{question.strip()}**",
            color=discord.Color.gold()
        )
        embed.add_field(name="How to Answer", value="Respond directly in this channel with your answer!")
        embed.set_footer(text="Think carefully, Nakama! 🍖")

        # Send the embed and ping the role
        await channel.send(content=role_mention, embed=embed)

        self._save_json(self.qotd_pool_file, qotd_pool)  # Update the pool

        # Start the task if not already running
        if not self.qotd_task.is_running():
            self.qotd_task.start()

        await ctx.send("QOTD timer started. The first question has been posted.")

    @tasks.loop(hours=24)
    async def qotd_task(self):
        channels = self._load_json(self.channels_file)
        qotd_pool = self._load_json(self.qotd_pool_file)
        current_question_data = self._load_json(self.current_question_file)
        answered_users_data = self._load_json(self.answered_users_file)

        for guild_id, settings in channels.items():
            qotd_channel_id = settings.get("qotd_channel")
            if not qotd_channel_id or not qotd_pool.get(guild_id):
                continue

            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue

            channel = guild.get_channel(qotd_channel_id)
            if not channel:
                continue

            question_data = qotd_pool[guild_id].pop(0)

            # Validate the question format
            if "&" not in question_data:
                await channel.send("⚠️ Skipping an improperly formatted question in the QOTD pool.")
                continue

            question, answer = question_data.split("&", 1)
            current_question_data[guild_id] = {"question": question.strip(), "answer": answer.strip()}
            answered_users_data[guild_id] = []  # Reset answered users for this QOTD
            self._save_json(self.current_question_file, current_question_data)
            self._save_json(self.answered_users_file, answered_users_data)

            # Mention the QOTD role
            qotd_role = discord.utils.get(guild.roles, name="QOTD")
            role_mention = qotd_role.mention if qotd_role else ""

            # Embed the QOTD
            embed = discord.Embed(
                title="🏴‍☠️ One Piece Question of the Day 🏴‍☠️",
                description=f"**{question.strip()}**",
                color=discord.Color.gold()
            )
            embed.add_field(name="How to Answer", value="Respond directly in this channel with your answer!")
            embed.set_footer(text="Think carefully, Nakama! 🍖")
            await channel.send(content=role_mention, embed=embed)

        self._save_json(self.qotd_pool_file, qotd_pool)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        data = self._load_json(self.channels_file)
        current_question_data = self._load_json(self.current_question_file)
        answered_users_data = self._load_json(self.answered_users_file)
        attempts_data = self._load_json(self.attempts_file)
        guild_id = str(message.guild.id)

        qotd_channel_id = data.get(guild_id, {}).get("qotd_channel")
        if message.channel.id != qotd_channel_id:
            return

        current_question = current_question_data.get(guild_id, {})
        correct_answer = current_question.get("answer")
        answered_users = answered_users_data.get(guild_id, [])
        attempts = attempts_data.get(guild_id, {})

        if not correct_answer:
            return

        # Delete the user's message to prevent others from seeing the input
        await message.delete()

        # Prevent multiple answers from the same user
        if message.author.id in answered_users:
            feedback = await message.channel.send(
                f"⚠️ {message.author.mention}, you've already answered this question!"
            )
            await feedback.delete(delay=5)  # Delete the feedback message
            return

        user_attempts = attempts.get(str(message.author.id), 0)

        # Check if the message is a valid answer
        if message.content.strip().lower() == correct_answer.strip().lower():
            answered_users.append(message.author.id)
            answered_users_data[guild_id] = answered_users
            self._save_json(self.answered_users_file, answered_users_data)

            feedback = await message.channel.send(
                f"✅ {message.author.mention}, that's the correct answer! You can answer again tomorrow."
            )
            await feedback.delete(delay=5)  # Delete the feedback message

            await self.restrict_user(
                message.channel, message.author, "has answered correctly and can answer again tomorrow."
            )
        else:
            user_attempts += 1
            attempts[str(message.author.id)] = user_attempts
            attempts_data[guild_id] = attempts  # Ensure guild-level tracking
            self._save_json(self.attempts_file, attempts_data)

            if user_attempts >= 3:
                feedback = await message.channel.send(
                    f"❌ {message.author.mention}, you've reached 3 incorrect attempts and cannot answer again until the next question is sent."
                )
                await feedback.delete(delay=5)  # Delete the feedback message
                await self.restrict_user(
                    message.channel, message.author, "has got the wrong answer 3 times and cannot answer again until the next question is sent."
                )
            else:
                remaining_attempts = 3 - user_attempts
                feedback = await message.channel.send(
                    f"❌ {message.author.mention}, that's incorrect. You have {remaining_attempts} attempts remaining."
                )
                await feedback.delete(delay=5)  # Delete the feedback message


def setup(bot):
    bot.add_cog(QOTD(bot))
