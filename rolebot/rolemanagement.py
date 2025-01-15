from redbot.core import commands, checks
import discord

class RoleManagement(commands.Cog):
    """
    A cog for managing roles in the server.
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="crole")
    @commands.admin_or_permissions(manage_roles=True)
    async def create_role(self, ctx, *, name: str):
        """
        Create a new role with the specified name.

        Example:
        (p)crole RoleName
        """
        guild = ctx.guild

        try:
            new_role = await guild.create_role(name=name)
            await ctx.send(f"✅ Successfully created the role: `{new_role.name}`")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to manage roles.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ An error occurred: {e}")

    @commands.command(name="drole")
    @commands.admin_or_permissions(manage_roles=True)
    async def delete_role(self, ctx, *, name: str):
        """
        Delete an existing role by its name.

        Example:
        (p)drole RoleName
        """
        guild = ctx.guild
        role = discord.utils.get(guild.roles, name=name)

        if role:
            try:
                await role.delete()
                await ctx.send(f"✅ Successfully deleted the role: `{name}`")
            except discord.Forbidden:
                await ctx.send("❌ I don't have permission to manage roles.")
            except discord.HTTPException as e:
                await ctx.send(f"❌ An error occurred: {e}")
        else:
            await ctx.send(f"❌ Role `{name}` not found.")

    @commands.command(name="roles")
    @commands.guild_only()
    async def list_roles(self, ctx):
        """
        List all roles in the server.

        Example:
        (p)roles
        """
        guild = ctx.guild
        roles = guild.roles  # List of roles, from highest to lowest position
        if not roles:
            await ctx.send("❌ No roles found in this server.")
            return

        # Format the roles into a sorted, readable list
        role_list = [
            f"{role.position}. {role.name} - Mentionable: {'✅' if role.mentionable else '❌'}"
            for role in sorted(roles, key=lambda r: r.position, reverse=True)
        ]

        # Send the list in chunks if it's too long
        chunks = [role_list[i:i + 10] for i in range(0, len(role_list), 10)]
        for chunk in chunks:
            await ctx.send("```\n" + "\n".join(chunk) + "\n```")

# Setup function for Redbot
async def setup(bot):
    await bot.add_cog(RoleManagement(bot))
