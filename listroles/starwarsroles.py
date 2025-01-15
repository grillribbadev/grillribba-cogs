import discord
from redbot.core import commands, Config

class StarWarsRoles(commands.Cog):
    """
    A cog for listing self-assignable Star Wars-themed roles in separate embeds and enforcing exclusive selection.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543215)  # Unique identifier
        self.config.register_guild(
            reaction_roles={
                "blue saber": "🔵",
                "green saber": "🟢",
                "red saber": "🔴",
                "purple saber": "🟣",
                "yellow saber": "🟡",
                "white saber": "⚪",
                "dark saber": "⚫",
                "Jedi Order": "🌀",
                "Sith Order": "🔥",
                "Rebel Alliance": "⭐",
                "Galactic Empire": "🛡️",
                "Mandalorian": "🤠",
                "Bounty Hunters": "💰",
            },
            saber_message_id=None,
            faction_message_id=None,
            specialty_message_id=None,
        )

    @commands.command(name="liststarwarsroles")
    @commands.admin_or_permissions(manage_roles=True)
    async def list_star_wars_roles(self, ctx):
        """
        Create three self-assignable role embeds: one for lightsaber colors, one for factions, and one for specialties.
        """
        guild = ctx.guild
        reaction_roles = await self.config.guild(guild).reaction_roles()

        # Separate roles into categories
        saber_roles = {
            k: v for k, v in reaction_roles.items()
            if k in ["blue saber", "green saber", "red saber", "purple saber", "yellow saber", "white saber", "dark saber"]
        }
        faction_roles = {
            k: v for k, v in reaction_roles.items()
            if k in ["Jedi Order", "Sith Order", "Rebel Alliance", "Galactic Empire"]
        }
        specialty_roles = {
            k: v for k, v in reaction_roles.items()
            if k in ["Mandalorian", "Bounty Hunters"]
        }

        # Lightsaber Roles Embed
        saber_embed = discord.Embed(
            title="Lightsaber Colors",
            description="React to pick a lightsaber color. You can only have **one saber color role**.",
            color=discord.Color.blue(),
        )
        for role_name, emoji in saber_roles.items():
            saber_embed.add_field(name=role_name.replace("_", " ").title(), value=f"React with {emoji}", inline=False)
        saber_message = await ctx.send(embed=saber_embed)

        # Add reactions to the saber message
        for emoji in saber_roles.values():
            await saber_message.add_reaction(emoji)

        # Save the saber message ID
        await self.config.guild(guild).saber_message_id.set(saber_message.id)

        # Faction Roles Embed
        faction_embed = discord.Embed(
            title="Star Wars Factions",
            description="React to pick a faction. You can only have **one faction role**.",
            color=discord.Color.red(),
        )
        for role_name, emoji in faction_roles.items():
            faction_embed.add_field(name=role_name, value=f"React with {emoji}", inline=False)
        faction_message = await ctx.send(embed=faction_embed)

        # Add reactions to the faction message
        for emoji in faction_roles.values():
            await faction_message.add_reaction(emoji)

        # Save the faction message ID
        await self.config.guild(guild).faction_message_id.set(faction_message.id)

        # Specialty Roles Embed
        specialty_embed = discord.Embed(
            title="Specialty Roles",
            description="React to pick a specialty role. You can have both roles if desired.",
            color=discord.Color.gold(),
        )
        for role_name, emoji in specialty_roles.items():
            specialty_embed.add_field(name=role_name, value=f"React with {emoji}", inline=False)
        specialty_message = await ctx.send(embed=specialty_embed)

        # Add reactions to the specialty message
        for emoji in specialty_roles.values():
            await specialty_message.add_reaction(emoji)

        # Save the specialty message ID
        await self.config.guild(guild).specialty_message_id.set(specialty_message.id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """
        Assign a role when a user reacts, enforcing exclusive selection for certain categories.
        """
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        saber_message_id = await self.config.guild(guild).saber_message_id()
        faction_message_id = await self.config.guild(guild).faction_message_id()
        specialty_message_id = await self.config.guild(guild).specialty_message_id()

        if payload.message_id == saber_message_id:
            category_roles = ["blue saber", "green saber", "red saber", "purple saber", "yellow saber", "white saber", "dark saber"]
        elif payload.message_id == faction_message_id:
            category_roles = ["Jedi Order", "Sith Order", "Rebel Alliance", "Galactic Empire"]
        elif payload.message_id == specialty_message_id:
            category_roles = ["Mandalorian", "Bounty Hunters"]
        else:
            return

        for role_name, emoji in reaction_roles.items():
            if role_name in category_roles and str(payload.emoji) == emoji:
                role = discord.utils.get(guild.roles, name=role_name)
                if role:
                    roles_to_remove = [r for r in user.roles if r.name in category_roles and r != role]
                    try:
                        await user.remove_roles(*roles_to_remove)
                        await user.add_roles(role)
                    except discord.Forbidden:
                        pass
                    except discord.HTTPException:
                        pass
                break

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """
        Remove a role when a user removes their reaction.
        """
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        saber_message_id = await self.config.guild(guild).saber_message_id()
        faction_message_id = await self.config.guild(guild).faction_message_id()
        specialty_message_id = await self.config.guild(guild).specialty_message_id()

        if payload.message_id == saber_message_id:
            category_roles = ["blue saber", "green saber", "red saber", "purple saber", "yellow saber", "white saber", "dark saber"]
        elif payload.message_id == faction_message_id:
            category_roles = ["Jedi Order", "Sith Order", "Rebel Alliance", "Galactic Empire"]
        elif payload.message_id == specialty_message_id:
            category_roles = ["Mandalorian", "Bounty Hunters"]
        else:
            return

        for role_name, emoji in reaction_roles.items():
            if role_name in category_roles and str(payload.emoji) == emoji:
                role = discord.utils.get(guild.roles, name=role_name)
                if role and role in user.roles:
                    try:
                        await user.remove_roles(role)
                    except discord.Forbidden:
                        pass
                    except discord.HTTPException:
                        pass
                break

# Setup function for Redbot
async def setup(bot):
    await bot.add_cog(StarWarsRoles(bot))
