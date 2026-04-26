from redbot.core import commands, Config
from typing import Union
import discord

class SilentDeny(Exception):
    """Custom exception for silent permission denial."""
    pass

class BetterPermissions(commands.Cog):
    """A better permissions system for Redbot."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "global_permissions": {},
            "channel_permissions": {},
            "user_permissions": {},
            "role_permissions": {}
        }
        self.config.register_guild(**default_guild)

    def get_permission(self, perms, target):
        """Get the most specific permission for a target, checking groups."""
        if not perms:
            return None
        target = target.lower()
        normalized = {k.lower(): v for k, v in perms.items()}
        if target in normalized:
            return normalized[target]
        # Check for parent groups
        parts = target.split()
        for i in range(len(parts) - 1, 0, -1):
            group = " ".join(parts[:i])
            if group in normalized:
                return normalized[group]
        return None

    def get_command_targets(self, ctx):
        """Return all relevant names for the current command to resolve permissions."""
        if not ctx.command:
            return []

        targets = []
        if ctx.cog:
            targets.append(ctx.cog.qualified_name.lower())

        qualified_name = ctx.command.qualified_name.lower()
        targets.append(qualified_name)

        if getattr(ctx.command, 'full_parent_name', None):
            targets.append(ctx.command.full_parent_name.lower())

        targets.append(ctx.command.name.lower())

        parent = getattr(ctx.command, 'parent', None)
        while parent:
            if getattr(parent, 'qualified_name', None):
                targets.append(parent.qualified_name.lower())
            parent = getattr(parent, 'parent', None)

        # Preserve order and remove duplicates
        return list(dict.fromkeys(targets))

    def get_target_permission(self, perms, ctx):
        """Get the most specific permission for a context's command or cog."""
        if not perms:
            return None
        for target in self.get_command_targets(ctx):
            permission = self.get_permission(perms, target)
            if permission is not None:
                return permission
        return None

    @commands.group()
    @commands.has_permissions(manage_guild=True)
    async def permset(self, ctx):
        """Manage permissions."""
        pass

    @permset.group(name="global")
    async def global_perm(self, ctx):
        """Global permissions for the entire guild."""
        pass

    @global_perm.command()
    async def allow(self, ctx, target: str):
        """Allow a cog, command, or command group globally."""
        target = target.lower()
        async with self.config.guild(ctx.guild).global_permissions() as perms:
            perms[target] = "allow"
        embed = discord.Embed(
            title="Permission Set",
            description=f"✅ Globally allowed: `{target}`",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @global_perm.command()
    async def deny(self, ctx, target: str):
        """Deny a cog, command, or command group globally."""
        target = target.lower()
        async with self.config.guild(ctx.guild).global_permissions() as perms:
            perms[target] = "deny"
        embed = discord.Embed(
            title="Permission Set",
            description=f"❌ Globally denied: `{target}`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    async def resolve_scope(self, ctx, scope: str):
        """Resolve a scope string into a Member, Role, or TextChannel."""
        if scope.lower() == "everyone":
            return ctx.guild.default_role

        converters = [
            commands.MemberConverter(),
            commands.TextChannelConverter(),
            commands.RoleConverter()
        ]

        for converter in converters:
            try:
                return await converter.convert(ctx, scope)
            except commands.BadArgument:
                continue

        # Fallback support for bare channel IDs and channel names.
        if scope.isdigit():
            channel = ctx.guild.get_channel(int(scope))
            if channel is not None:
                return channel

        if scope.startswith("#"):
            channel = discord.utils.get(ctx.guild.channels, name=scope[1:])
            if channel is not None:
                return channel

        channel = discord.utils.get(ctx.guild.channels, name=scope)
        if channel is not None:
            return channel

        raise commands.BadArgument(f"Could not resolve scope: {scope}")

    @permset.command()
    async def local(self, ctx, action: str, scope: str, *targets: str):
        """Set local permissions for a user, role, or channel on one or more commands."""
        action = action.lower()
        if action not in ["allow", "deny"]:
            embed = discord.Embed(
                title="Error",
                description="Action must be 'allow' or 'deny'.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        if not targets:
            embed = discord.Embed(
                title="Error",
                description="You must specify at least one command or command group.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        try:
            scope_obj = await self.resolve_scope(ctx, scope)
        except commands.BadArgument as exc:
            embed = discord.Embed(
                title="Error",
                description=str(exc),
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        if isinstance(scope_obj, discord.Member):
            config_section = self.config.guild(ctx.guild).user_permissions()
            scope_id = str(scope_obj.id)
            subject_name = f"user {scope_obj.mention}"
        elif isinstance(scope_obj, discord.Role):
            config_section = self.config.guild(ctx.guild).role_permissions()
            scope_id = str(scope_obj.id)
            subject_name = f"role {scope_obj.mention}"
        elif isinstance(scope_obj, discord.abc.GuildChannel):
            config_section = self.config.guild(ctx.guild).channel_permissions()
            scope_id = str(scope_obj.id)
            subject_name = f"channel {scope_obj.mention}"
        else:
            embed = discord.Embed(
                title="Error",
                description="Invalid scope type.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        async with config_section as perms:
            if scope_id not in perms:
                perms[scope_id] = {}
            for target in targets:
                perms[scope_id][target.lower()] = action

        target_text = ", ".join(f"`{target}`" for target in targets)
        embed = discord.Embed(
            title="Permission Set",
            description=f"{'✅' if action == 'allow' else '❌'} Set {action} for {subject_name} on {target_text}",
            color=discord.Color.green() if action == 'allow' else discord.Color.red()
        )
        await ctx.send(embed=embed)

    @permset.command()
    async def list(self, ctx):
        """List all current permissions."""
        global_perms = await self.config.guild(ctx.guild).global_permissions()
        user_perms = await self.config.guild(ctx.guild).user_permissions()
        role_perms = await self.config.guild(ctx.guild).role_permissions()
        channel_perms = await self.config.guild(ctx.guild).channel_permissions()
        
        embed = discord.Embed(
            title="Permissions Overview",
            color=discord.Color.blue()
        )
        
        if global_perms:
            global_list = "\n".join(f"`{k}`: {v}" for k, v in global_perms.items())
            embed.add_field(name="Global Permissions", value=global_list, inline=False)
        else:
            embed.add_field(name="Global Permissions", value="None", inline=False)
        
        if user_perms:
            user_list = []
            for uid, perms in user_perms.items():
                user = ctx.guild.get_member(int(uid))
                name = user.mention if user else f"User {uid}"
                user_list.append(f"{name}: " + ", ".join(f"`{k}`: {v}" for k, v in perms.items()))
            embed.add_field(name="User Permissions", value="\n".join(user_list), inline=False)
        else:
            embed.add_field(name="User Permissions", value="None", inline=False)
        
        if role_perms:
            role_list = []
            for rid, perms in role_perms.items():
                role = ctx.guild.get_role(int(rid))
                name = role.mention if role else f"Role {rid}"
                role_list.append(f"{name}: " + ", ".join(f"`{k}`: {v}" for k, v in perms.items()))
            embed.add_field(name="Role Permissions", value="\n".join(role_list), inline=False)
        else:
            embed.add_field(name="Role Permissions", value="None", inline=False)
        
        if channel_perms:
            channel_list = []
            for cid, perms in channel_perms.items():
                channel = ctx.guild.get_channel(int(cid))
                name = channel.mention if channel else f"Channel {cid}"
                channel_list.append(f"{name}: " + ", ".join(f"`{k}`: {v}" for k, v in perms.items()))
            embed.add_field(name="Channel Permissions", value="\n".join(channel_list), inline=False)
        else:
            embed.add_field(name="Channel Permissions", value="None", inline=False)
        
        await ctx.send(embed=embed)

    async def global_check(self, ctx):
        """Global check for permissions."""
        # Allow this cog's commands
        if ctx.cog == self:
            return True

        if not ctx.guild:
            return True

        command_name = ctx.command.qualified_name.lower()
        cog_name = ctx.cog.qualified_name.lower() if ctx.cog else None

        # User permissions are most specific
        user_perms = await self.config.guild(ctx.guild).user_permissions()
        user_id = str(ctx.author.id)
        user_perm = None
        if user_id in user_perms:
            user_perm = self.get_target_permission(user_perms[user_id], ctx)
            if user_perm == "allow":
                return True
            if user_perm == "deny":
                raise SilentDeny()

        # Channel permissions override role/global permissions
        channel_perms = await self.config.guild(ctx.guild).channel_permissions()
        channel_perm = None
        channel_id = str(ctx.channel.id)
        if channel_id in channel_perms:
            channel_perm = self.get_target_permission(channel_perms[channel_id], ctx)
            if channel_perm == "allow":
                return True
            if channel_perm == "deny":
                raise SilentDeny()

        # Role permissions come next; deny wins among roles at the same scope
        role_perms = await self.config.guild(ctx.guild).role_permissions()
        role_perm = None
        for role in ctx.author.roles:
            role_id = str(role.id)
            if role_id in role_perms:
                perm = self.get_target_permission(role_perms[role_id], ctx)
                if perm == "deny":
                    role_perm = "deny"
                    break
                if perm == "allow":
                    role_perm = "allow"
        if role_perm == "allow":
            return True
        if role_perm == "deny":
            raise SilentDeny()

        # Global permissions are the least specific
        global_perms = await self.config.guild(ctx.guild).global_permissions()
        global_perm = self.get_target_permission(global_perms, ctx)
        if global_perm == "allow":
            return True
        if global_perm == "deny":
            raise SilentDeny()

        return True

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Suppress SilentDeny exceptions."""
        if isinstance(error, SilentDeny):
            return  # Suppress the error, no message sent

async def setup(bot):
    cog = BetterPermissions(bot)
    await bot.add_cog(cog)
    bot.add_check(cog.global_check)