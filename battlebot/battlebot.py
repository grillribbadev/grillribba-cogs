import random
import discord
import json
import os
import asyncio
from redbot.core import commands

class BattleBot(commands.Cog):
    """A cog for managing battles, factions, and leaderboards."""

    def __init__(self, bot):
        self.bot = bot
        self.active_battles = {}
        self.win_data = self.load_win_data()
        self.faction_data = self.load_faction_data()

    def load_win_data(self):
        """Load win data from a JSON file."""
        if os.path.exists('win_data.json'):
            with open('win_data.json', 'r') as file:
                return json.load(file)
        return {}

    def save_win_data(self):
        """Save win data to a JSON file."""
        with open('win_data.json', 'w') as file:
            json.dump(self.win_data, file, indent=4)

    def load_faction_data(self):
        """Load faction data from a JSON file."""
        if os.path.exists('faction_data.json'):
            with open('faction_data.json', 'r') as file:
                return json.load(file)
        return {}

    def save_faction_data(self):
        """Save faction data to a JSON file."""
        with open('faction_data.json', 'w') as file:
            json.dump(self.faction_data, file, indent=4)

    def increment_win(self, guild_id, user_id):
        """Increment the win count for a user in a specific guild."""
        user = self.bot.get_user(user_id)
        user_name = user.display_name if user else "Unknown User"

        guild_id_str = str(guild_id)
        user_id_str = str(user_id)

        if guild_id_str not in self.win_data:
            self.win_data[guild_id_str] = {}

        if user_id_str in self.win_data[guild_id_str]:
            self.win_data[guild_id_str][user_id_str]['wins'] += 1
            self.win_data[guild_id_str][user_id_str]['name'] = user_name
        else:
            self.win_data[guild_id_str][user_id_str] = {'name': user_name, 'wins': 1}

        self.save_win_data()
        asyncio.create_task(self.assign_top_fighter_role(guild_id))

    async def assign_top_fighter_role(self, guild_id):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        guild_id_str = str(guild_id)
        if guild_id_str not in self.win_data or not self.win_data[guild_id_str]:
            return

        # Find the top user
        sorted_win_data = sorted(self.win_data[guild_id_str].items(), key=lambda x: x[1]['wins'], reverse=True)
        top_user_id = int(sorted_win_data[0][0])

        # Assign the top-fighter role
        role = discord.utils.get(guild.roles, name="top-fighter")
        if role and guild.me.guild_permissions.manage_roles:
            # Remove the role from all members
            for member in guild.members:
                if role in member.roles:
                    await member.remove_roles(role)

            # Add the role to the top user
            top_user = guild.get_member(top_user_id)
            if top_user:
                await top_user.add_roles(role)

    def generate_health_bar(self, health):
        """Generate a health bar with hearts."""
        hearts = '❤️' * (health // 10)
        return hearts.ljust(10, '🖤')  # Fill remaining space with black hearts

    @commands.command()
    async def battle(self, ctx, target: discord.Member = None):
        """Start a battle with another member."""
        guild_id_str = str(ctx.guild.id)
        user_id_str = str(ctx.author.id)

        # Check if the user has joined a faction
        if guild_id_str not in self.faction_data or user_id_str not in self.faction_data[guild_id_str]:
            await ctx.send(f"{ctx.author.mention}, you must join a faction (Marines or Pirates) before starting a battle! Use the `factions` command to pick a faction.")
            return

        # Select a random target if none is provided
        if target is None:
            # Filter members with valid factions
            eligible_members = [
                member for member in ctx.guild.members
                if not member.bot
                and member.id != ctx.author.id
                and str(member.id) in self.faction_data.get(guild_id_str, {})
            ]

            if not eligible_members:
                await ctx.send("There's no one who can battle at this time.")
                return

            target = random.choice(eligible_members)

        # Prevent battling oneself
        if target.id == ctx.author.id:
            await ctx.send("You can't battle yourself!")
            return

        # Check if the target has joined a faction
        target_id_str = str(target.id)
        if guild_id_str not in self.faction_data or target_id_str not in self.faction_data[guild_id_str]:
            await ctx.send(f"{target.mention} has not joined a faction (Marines or Pirates) and cannot be battled.")
            return

        # Check if the user or target is already in a battle
        if ctx.author.id in self.active_battles:
            await ctx.send("You are already in a battle!")
            return
        if target.id in self.active_battles:
            await ctx.send(f"{target.name} is already in a battle!")
            return

        # Set the battle flag for both participants
        self.active_battles[ctx.author.id] = True
        self.active_battles[target.id] = True

        # Battle logic
        ctx_author_health = 100
        target_health = 100
        damage_log = []  # List to store damage logs

        battle_embed = discord.Embed(
            description=f"Battle between {ctx.author.name} and {target.name} has begun!",
            color=discord.Color.blue()
        )
        battle_embed.add_field(name=f"{ctx.author.name} Health", value=self.generate_health_bar(ctx_author_health))
        battle_embed.add_field(name=f"{target.name} Health", value=self.generate_health_bar(target_health))
        battle_embed.add_field(name="Damage Log", value="No damage dealt yet.", inline=False)
        battle_msg = await ctx.send(embed=battle_embed)

        while ctx_author_health > 0 and target_health > 0:
            # Author attacks
            attack_damage = random.randint(5, 25)
            if random.random() < 0.15:  # 15% chance for a critical hit
                attack_damage = int(attack_damage * 1.5)
                damage_log.append(f"**Critical!** {ctx.author.name} dealt {attack_damage} damage to {target.name}! 💥")
            else:
                damage_log.append(f"{ctx.author.name} dealt {attack_damage} damage to {target.name}.")
            target_health = max(0, target_health - attack_damage)
            if target_health == 0:
                break

            # Target attacks
            attack_damage = random.randint(5, 25)
            if random.random() < 0.15:  # 15% chance for a critical hit
                attack_damage = int(attack_damage * 1.5)
                damage_log.append(f"**Critical!** {target.name} dealt {attack_damage} damage to {ctx.author.name}! 💥")
            else:
                damage_log.append(f"{target.name} dealt {attack_damage} damage to {ctx.author.name}.")
            ctx_author_health = max(0, ctx_author_health - attack_damage)

            # Update embed
            battle_embed.set_field_at(0, name=f"{ctx.author.name} Health",
                                      value=self.generate_health_bar(ctx_author_health))
            battle_embed.set_field_at(1, name=f"{target.name} Health",
                                      value=self.generate_health_bar(target_health))
            battle_embed.set_field_at(2, name="Damage Log", value="\n".join(damage_log[-5:]), inline=False)  # Show last 5 logs
            await battle_msg.edit(embed=battle_embed)
            await asyncio.sleep(1)

        # End of battle
        del self.active_battles[ctx.author.id]
        del self.active_battles[target.id]

        winner = ctx.author if target_health == 0 else target
        self.increment_win(ctx.guild.id, winner.id)
        await ctx.send(f"The battle is over! {winner.mention} wins!")

    @commands.command()
    async def factions(self, ctx):
        """Send an embed for users to choose their faction."""
        marine_emoji = "⚓"
        pirate_emoji = "🏴‍☠️"
        embed = discord.Embed(
            title="Choose Your Faction",
            description=(f"React with {marine_emoji} to join the **Marines**.\nReact with {pirate_emoji} to join the **Pirates**."),
            color=discord.Color.blue()
        )
        embed.set_footer(text="You can only be in one faction at a time.")

        # Send the embed and add reactions
        faction_message = await ctx.send(embed=embed)
        await faction_message.add_reaction(marine_emoji)
        await faction_message.add_reaction(pirate_emoji)

        def check(reaction, user):
            return (
                user != self.bot.user
                and reaction.message.id == faction_message.id
                and str(reaction.emoji) in [marine_emoji, pirate_emoji]
            )

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)

            guild_id_str = str(ctx.guild.id)
            user_id_str = str(user.id)

            if guild_id_str not in self.faction_data:
                self.faction_data[guild_id_str] = {}

            if str(reaction.emoji) == marine_emoji:
                faction = "Marines"
            elif str(reaction.emoji) == pirate_emoji:
                faction = "Pirates"
            else:
                return  # Unhandled reaction

            existing_faction = self.faction_data[guild_id_str].get(user_id_str)
            if existing_faction and existing_faction != faction:
                existing_role = discord.utils.get(ctx.guild.roles, name=existing_faction)
                if existing_role and existing_role in user.roles:
                    await user.remove_roles(existing_role)

            self.faction_data[guild_id_str][user_id_str] = faction
            self.save_faction_data()

            role = discord.utils.get(ctx.guild.roles, name=faction)
            if role:
                if ctx.guild.me.guild_permissions.manage_roles:
                    await user.add_roles(role)
                    await ctx.send(f"{user.display_name} has joined the **{faction}**!")
                else:
                    await ctx.send("I don't have permission to manage roles.")
            else:
                await ctx.send(f"The role '{faction}' does not exist. Please create it first.")

        except asyncio.TimeoutError:
            await ctx.send("Faction selection timed out. Please try again.")

    @commands.command()
    async def leavefaction(self, ctx):
        """Leave your current faction."""
        guild_id_str = str(ctx.guild.id)
        user_id_str = str(ctx.author.id)

        if guild_id_str not in self.faction_data or user_id_str not in self.faction_data[guild_id_str]:
            await ctx.send(f"{ctx.author.mention}, you are not part of any faction.")
            return

        faction = self.faction_data[guild_id_str].pop(user_id_str, None)
        self.save_faction_data()

        if not faction:
            await ctx.send(f"{ctx.author.mention}, you are not part of any faction.")
            return

        role = discord.utils.get(ctx.guild.roles, name=faction)
        if role and role in ctx.author.roles:
            await ctx.author.remove_roles(role)

        await ctx.send(f"{ctx.author.display_name} has left the **{faction}**.")

    @commands.command()
    async def status(self, ctx, target: discord.Member = None):
        """Show the user's current stats, including wins and faction."""
        if target is None:
            target = ctx.author

        guild_id_str = str(ctx.guild.id)
        user_id_str = str(target.id)

        faction = self.faction_data.get(guild_id_str, {}).get(user_id_str, "None")
        wins = self.win_data.get(guild_id_str, {}).get(user_id_str, {}).get("wins", 0)

        attack = 100 + (wins * 0.2)
        speed = 100 + (wins * 0.2)
        intelligence = 100 + (wins * 0.2)

        embed = discord.Embed(title=f"{target.display_name}'s Player Stats", color=discord.Color.green())
        embed.add_field(name="Faction", value=faction, inline=True)
        embed.add_field(name="Wins", value=wins, inline=True)
        embed.add_field(name="Attack", value=f"{attack:.1f}", inline=True)
        embed.add_field(name="Speed", value=f"{speed:.1f}", inline=True)
        embed.add_field(name="Intelligence", value=f"{intelligence:.1f}", inline=True)

        await ctx.send(embed=embed)

    @commands.command()
    async def top(self, ctx):
        """Show the leaderboard for the top players in the server."""
        guild_id_str = str(ctx.guild.id)

        if guild_id_str not in self.win_data or not self.win_data[guild_id_str]:
            await ctx.send("No wins have been recorded yet!")
            return

        sorted_win_data = sorted(self.win_data[guild_id_str].items(), key=lambda x: x[1]['wins'], reverse=True)

        leaderboard = []
        for idx, (user_id, data) in enumerate(sorted_win_data[:10]):  # Show top 10 players
            user = ctx.guild.get_member(int(user_id))
            if user:
                leaderboard.append(f"{idx + 1}. **{user.display_name}** - {data['wins']} Wins")
            else:
                leaderboard.append(f"{idx + 1}. **{data['name']}** - {data['wins']} Wins")

        embed = discord.Embed(title="Server Leaderboard", color=discord.Color.gold())
        embed.description = "\n".join(leaderboard)

        await ctx.send(embed=embed)

    @commands.command()
    async def topclear(self, ctx):
        """Clear the leaderboard for the current server."""
        guild_id_str = str(ctx.guild.id)

        if guild_id_str in self.win_data:
            self.win_data[guild_id_str] = {}
            self.save_win_data()
            await ctx.send("The leaderboard has been cleared.")
        else:
            await ctx.send("No leaderboard data to clear.")
