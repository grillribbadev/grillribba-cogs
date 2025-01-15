from redbot.core import commands, Config
import discord

class AutoRole(commands.Cog):
    """
    Automatically assigns a role to new members and allows reaction-based role assignment.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(auto_role=None, reaction_roles={})

    # Auto-role Commands
    @commands.group(name="autorole", invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def autorole(self, ctx):
        """
        Configure or display the current auto-role settings.
        """
        auto_role_id = await self.config.guild(ctx.guild).auto_role()
        if auto_role_id:
            role = ctx.guild.get_role(auto_role_id)
            if role:
                await ctx.send(f"✅ Auto-role is currently set to: `{role.name}`")
                return
        await ctx.send("❌ Auto-role is not currently set.")

    @autorole.command(name="set")
    @commands.admin_or_permissions(manage_roles=True)
    async def set_autorole(self, ctx, role: discord.Role):
        """
        Set the role to be automatically assigned to new members.

        Example:
        (p)autorole set @Member
        """
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send("❌ I don't have permission to manage roles.")
            return

        # Check bot role hierarchy
        if role >= ctx.guild.me.top_role:
            await ctx.send("❌ I can't assign a role higher than or equal to my highest role.")
            return

        await self.config.guild(ctx.guild).auto_role.set(role.id)
        await ctx.send(f"✅ Auto-role set to: `{role.name}`")

    @autorole.command(name="clear")
    @commands.admin_or_permissions(manage_roles=True)
    async def clear_autorole(self, ctx):
        """
        Clear the auto-role setting.

        Example:
        (p)autorole clear
        """
        await self.config.guild(ctx.guild).auto_role.set(None)
        await ctx.send("✅ Auto-role has been cleared.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """
        Automatically assign the configured role to new members.
        """
        guild = member.guild
        auto_role_id = await self.config.guild(guild).auto_role()

        if not auto_role_id:
            return  # No auto-role set

        role = guild.get_role(auto_role_id)
        if not role:
            return  # Role no longer exists

        # Assign the role
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            pass  # Bot doesn't have permissions to add the role
        except discord.HTTPException:
            pass  # Something went wrong during the role assignment

    # Reaction Role Commands
    @commands.group(name="reactionrole", aliases=["rr"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def reaction_role(self, ctx):
        """
        Manage reaction roles in the server.
        """
        await ctx.send_help(ctx.command)

    @reaction_role.command(name="add")
    @commands.admin_or_permissions(manage_roles=True)
    async def add_reaction_role(self, ctx, message_id: int, emoji: str, role: discord.Role):
        """
        Add a reaction role to a specific message.

        Example:
        (p)reactionrole add <message_id> <emoji> @Role
        """
        if role >= ctx.guild.me.top_role:
            await ctx.send("❌ I can't manage roles higher than or equal to my highest role.")
            return

        # Save the reaction role
        reaction_roles = await self.config.guild(ctx.guild).reaction_roles()
        if str(message_id) not in reaction_roles:
            reaction_roles[str(message_id)] = {}
        reaction_roles[str(message_id)][emoji] = role.id
        await self.config.guild(ctx.guild).reaction_roles.set(reaction_roles)

        await ctx.send(f"✅ Reaction role added: React with `{emoji}` to get `{role.name}` on message ID `{message_id}`.")

    @reaction_role.command(name="remove")
    @commands.admin_or_permissions(manage_roles=True)
    async def remove_reaction_role(self, ctx, message_id: int, emoji: str):
        """
        Remove a reaction role from a specific message.

        Example:
        (p)reactionrole remove <message_id> <emoji>
        """
        reaction_roles = await self.config.guild(ctx.guild).reaction_roles()
        if str(message_id) in reaction_roles and emoji in reaction_roles[str(message_id)]:
            del reaction_roles[str(message_id)][emoji]
            if not reaction_roles[str(message_id)]:
                del reaction_roles[str(message_id)]
            await self.config.guild(ctx.guild).reaction_roles.set(reaction_roles)
            await ctx.send(f"✅ Reaction role removed for emoji `{emoji}` on message ID `{message_id}`.")
        else:
            await ctx.send("❌ No such reaction role found.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """
        Assign a role when a user reacts to a configured message.
        """
        if payload.guild_id is None:
            return  # Ignore DMs

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        if str(payload.message_id) in reaction_roles and payload.emoji.name in reaction_roles[str(payload.message_id)]:
            role_id = reaction_roles[str(payload.message_id)][payload.emoji.name]
            role = guild.get_role(role_id)
            member = guild.get_member(payload.user_id)

            if role and member:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """
        Remove a role when a user removes their reaction from a configured message.
        """
        if payload.guild_id is None:
            return  # Ignore DMs

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        if str(payload.message_id) in reaction_roles and payload.emoji.name in reaction_roles[str(payload.message_id)]:
            role_id = reaction_roles[str(payload.message_id)][payload.emoji.name]
            role = guild.get_role(role_id)
            member = guild.get_member(payload.user_id)

            if role and member:
                try:
                    await member.remove_roles(role)
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

# Setup function for Redbot
async def setup(bot):
    await bot.add_cog(AutoRole(bot))
