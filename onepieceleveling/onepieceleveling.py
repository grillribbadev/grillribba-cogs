import discord
from redbot.core import commands, Config
from discord.ext import tasks

class OnePieceLeveling(commands.Cog):
    """A leveling system based on One Piece ranks."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(
            xp_per_message=10,
            levels={
                1: "Recruit",
                5: "Seaman Recruit",
                10: "Seaman Apprentice",
                15: "Seaman First Class",
                20: "Petty Officer",
                25: "Chief Petty Officer",
                30: "Master Chief Petty Officer",
                35: "Ensign",
                40: "Lieutenant Junior Grade",
                45: "Lieutenant",
                50: "Lieutenant Commander",
                55: "Commander",
                60: "Captain",
                65: "Commodore",
                70: "Rear Admiral",
                75: "Vice Admiral",
                80: "Admiral",
                85: "Fleet Admiral",
                90: "World Noble",
                95: "Celestial Dragon",
                100: "Gorosei",
            },
        )
        self.config.register_member(xp=0, level=1)
        self.check_roles.start()

    def cog_unload(self):
        self.check_roles.cancel()

    @tasks.loop(minutes=5)
    async def check_roles(self):
        """Ensure all roles exist in the server."""
        for guild in self.bot.guilds:
            levels = await self.config.guild(guild).levels()
            for level, role_name in levels.items():
                role = discord.utils.get(guild.roles, name=role_name)
                if not role:
                    await guild.create_role(name=role_name)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Assign the Level 1 role (Recruit) to a new member."""
        guild = member.guild
        levels = await self.config.guild(guild).levels()
        level_1_role_name = levels.get(1)

        if level_1_role_name:
            role = discord.utils.get(guild.roles, name=level_1_role_name)
            if not role:
                role = await guild.create_role(name=level_1_role_name)
            await member.add_roles(role)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle leveling up based on messages."""
        if message.author.bot or not message.guild:
            return
        
        guild = message.guild
        member = message.author

        xp_per_message = await self.config.guild(guild).xp_per_message()
        member_data = self.config.member(member)
        current_xp = await member_data.xp()
        current_level = await member_data.level()

        # Add XP
        new_xp = current_xp + xp_per_message
        await member_data.xp.set(new_xp)

        # Check for level up
        new_level = self.calculate_level(new_xp)
        if new_level > current_level:
            await member_data.level.set(new_level)
            await self.assign_role(guild, member, new_level)
            if new_level % 10 == 0:
                await self.announce_level_up(guild, member, new_level)

    def calculate_level(self, xp):
        """Calculate level based on XP (linear progression)."""
        return xp // 50 + 1  # Adjust XP needed per level as needed

    async def assign_role(self, guild, member, level):
        """Assign the appropriate role based on level and remove the old one."""
        levels = await self.config.guild(guild).levels()
        role_name = levels.get(level)
        if not role_name:
            return
        
        # Ensure the role exists
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            role = await guild.create_role(name=role_name)

        # Remove previous roles in the leveling system
        for lvl, lvl_role_name in levels.items():
            lvl_role = discord.utils.get(guild.roles, name=lvl_role_name)
            if lvl_role and lvl_role in member.roles and lvl != level:
                try:
                    await member.remove_roles(lvl_role)
                except discord.Forbidden:
                    print(f"Forbidden: Could not remove role {lvl_role.name}.")
                except Exception as e:
                    print(f"Error removing role {lvl_role.name}: {e}")

        # Add the new role
        if role not in member.roles:
            try:
                await member.add_roles(role)
                print(f"Assigned role {role.name} to {member.display_name}.")
            except discord.Forbidden:
                print(f"Forbidden: Could not assign role {role.name}. Check bot's permissions.")
            except Exception as e:
                print(f"Error assigning role {role.name}: {e}")

    async def announce_level_up(self, guild, member, level):
        """Announce a level up milestone."""
        embed = discord.Embed(
            title="Level Up!",
            description=f"🎉 {member.mention} has reached **Level {level}**!",
            color=discord.Color.gold(),
        )
        channel = guild.system_channel  # Change this to a specific channel if needed
        if channel:
            await channel.send(embed=embed)

    @commands.command()
    async def setxp(self, ctx, member: discord.Member, xp: int):
        """Set a member's XP."""
        if xp < 0:
            await ctx.send("XP cannot be negative.")
            return

        await self.config.member(member).xp.set(xp)
        await ctx.send(f"{member.display_name}'s XP has been set to {xp}.")

    @commands.command()
    async def rank(self, ctx, member: discord.Member = None):
        """Check a member's current rank and level."""
        if member is None:
            member = ctx.author

        member_data = self.config.member(member)
        current_xp = await member_data.xp()
        current_level = await member_data.level()

        levels = await self.config.guild(ctx.guild).levels()
        current_rank = levels.get(current_level, "Unknown Rank")

        embed = discord.Embed(
            title=f"{member.display_name}'s Rank Info",
            description=f"**Rank:** {current_rank}\n**Level:** {current_level}\n**XP:** {current_xp}",
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)

    @commands.command()
    async def levelboard(self, ctx):
        """Show the server's leveling leaderboard."""
        guild = ctx.guild
        members = guild.members

        leaderboard = []
        for member in members:
            if member.bot:
                continue

            member_data = self.config.member(member)
            current_xp = await member_data.xp()
            current_level = await member_data.level()

            leaderboard.append((member.display_name, current_level, current_xp))

        # Sort leaderboard by level, then XP
        leaderboard.sort(key=lambda x: (-x[1], -x[2]))

        embed = discord.Embed(
            title=f"{guild.name} Level Leaderboard",
            description="\n".join(
                [f"{idx+1}. **{name}** - Level {level} ({xp} XP)" for idx, (name, level, xp) in enumerate(leaderboard[:10])]
            ),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    @commands.command()
    async def levelclear(self, ctx):
        """Clear the leveling leaderboard."""
        guild = ctx.guild
        members = guild.members

        for member in members:
            if member.bot:
                continue

            await self.config.member(member).xp.set(0)
            await self.config.member(member).level.set(1)

        await ctx.send("Leaderboard has been reset successfully.")
