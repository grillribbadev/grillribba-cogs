from redbot.core import commands, Config
import discord

class CustomRoleCog(commands.Cog):
    """A cog to allow users to create a custom role with a specified color."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(roles={})

    async def has_required_role(self, member: discord.Member, required_role_name: str):
        """Check if a member has the required role."""
        required_role = discord.utils.get(member.guild.roles, name=required_role_name)
        return required_role in member.roles if required_role else False

    @commands.command()
    @commands.guild_only()
    async def createrole(self, ctx, name: str, color: discord.Color):
        """
        Create a custom role with the given name and color.
        Each user can create only one custom role.

        Example:
        .createrole "My Role" #FF5733
        """
        guild = ctx.guild
        member = ctx.author

        # Check if the member has the required role
        required_role_name = "Lieutenant Commander"  # Role for level 50
        if not await self.has_required_role(member, required_role_name):
            await ctx.send(f"You must have the '{required_role_name}' role to use this command.")
            return

        # Check if the member already created a role
        roles = await self.config.guild(guild).roles()
        if str(member.id) in roles:
            await ctx.send("You can only create one custom role!")
            return

        # Ensure the bot has permission to manage roles
        if not guild.me.guild_permissions.manage_roles:
            await ctx.send("I don't have permission to manage roles.")
            return

        # Create the role
        role = await guild.create_role(name=name, color=color, reason=f"Custom role created by {member.name}")
        await member.add_roles(role)

        # Save the role information
        roles[str(member.id)] = role.id
        await self.config.guild(guild).roles.set(roles)

        await ctx.send(f"Role '{name}' created and assigned to you!")

    @commands.command()
    @commands.guild_only()
    async def deleterole(self, ctx):
        """
        Delete your custom role.
        """
        guild = ctx.guild
        member = ctx.author

        # Check if the member has the required role
        required_role_name = "Lieutenant Commander"  # Role for level 50
        if not await self.has_required_role(member, required_role_name):
            await ctx.send(f"You must have the '{required_role_name}' role to use this command.")
            return

        roles = await self.config.guild(guild).roles()
        role_id = roles.get(str(member.id))

        if not role_id:
            await ctx.send("You don't have a custom role to delete.")
            return

        role = discord.utils.get(guild.roles, id=role_id)
        if role:
            await role.delete(reason=f"Custom role deleted by {member.name}")
            await ctx.send(f"Your custom role '{role.name}' has been deleted.")
        else:
            await ctx.send("Your custom role could not be found. It might have already been deleted.")

        # Remove from config
        del roles[str(member.id)]
        await self.config.guild(guild).roles.set(roles)
