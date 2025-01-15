import discord
from redbot.core import commands, Config


class StarWarsRoles(commands.Cog):
    """
    A cog for listing self-assignable Star Wars-themed roles in separate embeds
    and enforcing exclusive selection.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543215)
        self.config.register_guild(
            reaction_roles={
                "blue saber": "🔵",
                "green saber": "🟢",
                "red saber": "🔴",
                "purple saber": "🟣",
                "yellow saber": "🟡",
                "white saber": "⚪",
                "dark saber": "⚫",
                "Rebel Alliance": "⭐",
                "Galactic Empire": "🛡️",
                "Jedi Order": "🌀",
                "Sith Order": "🔥",
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
            if k in ["Rebel Alliance", "Galactic Empire", "Jedi Order", "Sith Order"]
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
            saber_embed.add_field(name=role_name.title(), value=f"React with {emoji}", inline=False)
        saber_message = await ctx.send(embed=saber_embed)

        for emoji in saber_roles.values():
            await saber_message.add_reaction(emoji)

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

        for emoji in faction_roles.values():
            await faction_message.add_reaction(emoji)

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

        for emoji in specialty_roles.values():
            await specialty_message.add_reaction(emoji)

        await self.config.guild(guild).specialty_message_id.set(specialty_message.id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """
        Assign a role when a user reacts, enforcing exclusive selection for certain categories.
        """
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            print(f"Guild not found for guild_id: {payload.guild_id}")
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            print(f"Skipping reaction from bot or invalid user: {user}")
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        saber_message_id = await self.config.guild(guild).saber_message_id()
        faction_message_id = await self.config.guild(guild).faction_message_id()
        specialty_message_id = await self.config.guild(guild).specialty_message_id()

        # Identify category based on message ID
        if payload.message_id == saber_message_id:
            category_roles = ["blue saber", "green saber", "red saber", "purple saber", "yellow saber", "white saber", "dark saber"]
            print(f"Processing saber roles reaction for emoji: {payload.emoji}")
        elif payload.message_id == faction_message_id:
            category_roles = ["Rebel Alliance", "Galactic Empire", "Jedi Order", "Sith Order"]
            print(f"Processing faction roles reaction for emoji: {payload.emoji}")
        elif payload.message_id == specialty_message_id:
            category_roles = ["Mandalorian", "Bounty Hunters"]
            print(f"Processing specialty roles reaction for emoji: {payload.emoji}")
        else:
            print(f"Reaction not on a tracked message (message_id: {payload.message_id}).")
            return

        # Match emoji to role
        for role_name, emoji in reaction_roles.items():
            if role_name in category_roles and str(payload.emoji) == emoji:
                role = discord.utils.get(guild.roles, name=role_name)
                if role:
                    print(f"Found role: {role.name} for emoji: {payload.emoji}")
                    try:
                        # Remove conflicting roles in the same category
                        roles_to_remove = [r for r in user.roles if r.name in category_roles and r != role]
                        if roles_to_remove:
                            print(f"Removing conflicting roles: {[r.name for r in roles_to_remove]}")
                        await user.remove_roles(*roles_to_remove)
                        await user.add_roles(role)
                        print(f"Assigned role: {role.name} to {user.display_name}")
                        await self._send_dm(user, f"You have been assigned the role: **{role.name}**.")
                    except discord.Forbidden:
                        print("Bot lacks permission to assign roles.")
                    except discord.HTTPException as e:
                        print(f"HTTPException while assigning role: {e}")
                else:
                    print(f"Role not found in the server: {role_name}")
                break
        else:
            print(f"No matching role found for emoji: {payload.emoji}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """
        Remove a role when a user removes their reaction.
        """
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            print(f"Guild not found for guild_id: {payload.guild_id}")
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            print(f"Skipping reaction removal from bot or invalid user: {user}")
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        saber_message_id = await self.config.guild(guild).saber_message_id()
        faction_message_id = await self.config.guild(guild).faction_message_id()
        specialty_message_id = await self.config.guild(guild).specialty_message_id()

        # Identify category based on message ID
        if payload.message_id == saber_message_id:
            category_roles = ["blue saber", "green saber", "red saber", "purple saber", "yellow saber", "white saber", "dark saber"]
        elif payload.message_id == faction_message_id:
            category_roles = ["Rebel Alliance", "Galactic Empire", "Jedi Order", "Sith Order"]
        elif payload.message_id == specialty_message_id:
            category_roles = ["Mandalorian", "Bounty Hunters"]
        else:
            print(f"Reaction removal not on a tracked message (message_id: {payload.message_id}).")
            return

        # Match emoji to role
        for role_name, emoji in reaction_roles.items():
            if role_name in category_roles and str(payload.emoji) == emoji:
                role = discord.utils.get(guild.roles, name=role_name)
                if role and role in user.roles:
                    try:
                        await user.remove_roles(role)
                        print(f"Removed role: {role.name} from {user.display_name}")
                        await self._send_dm(user, f"The role **{role.name}** has been removed.")
                    except discord.Forbidden:
                        print("Bot lacks permission to remove roles.")
                    except discord.HTTPException as e:
                        print(f"HTTPException while removing role: {e}")
                else:
                    print(f"Role not found or user does not have the role: {role_name}")
                break

    async def _send_dm(self, user, message):
        """
        Send a DM to the user. Handle cases where DMs are disabled.
        """
        try:
            await user.send(message)
        except discord.Forbidden:
            print(f"Cannot send DM to {user}. They might have DMs disabled.")
        except discord.HTTPException as e:
            print(f"Failed to send DM: {e}")


# Setup function for Redbot
async def setup(bot):
    await bot.add_cog(StarWarsRoles(bot))
