from redbot.core import commands, Config
from typing import Union
import discord

class BetterPermissions(commands.Cog):
    """A better permissions system for Redbot."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "global_permissions": {},
            "channel_permissions": {},
            "user_permissions": {},
            "role_permissions": {},
            "channel_role_permissions": {}
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

    def get_command_targets_from_command(self, command, cog):
        """Return all relevant names for a command to resolve permissions."""
        if not command:
            return []

        targets = []
        if cog:
            targets.append(cog.qualified_name.lower())

        qualified_name = command.qualified_name.lower()
        targets.append(qualified_name)

        if getattr(command, 'full_parent_name', None):
            targets.append(command.full_parent_name.lower())

        targets.append(command.name.lower())

        parent = getattr(command, 'parent', None)
        while parent:
            if getattr(parent, 'qualified_name', None):
                targets.append(parent.qualified_name.lower())
            parent = getattr(parent, 'parent', None)

        # Preserve order and remove duplicates
        return list(dict.fromkeys(targets))

    def get_target_permission(self, perms, targets):
        """Get the most specific permission for a context's command or cog."""
        if not perms:
            return None
        for target in targets:
            permission = self.get_permission(perms, target)
            if permission is not None:
                return permission
        return None

    def normalize_name(self, name: str) -> str:
        """Normalize a name for fuzzy channel/role matching."""
        if not name:
            return ""
        normalized = name.lower()
        normalized = normalized.replace("#", "").replace("@", "")
        normalized = normalized.replace(" ", "-")
        normalized = "".join(ch for ch in normalized if ch.isalnum() or ch in "-_ ")
        return normalized.strip("-_ ")

    def get_channel_role_permission(self, perms, channel_id, targets, ctx):
        """Check channel+role scoped permissions for the current author."""
        if not perms or channel_id not in perms:
            return None
        channel_roles = perms[channel_id]
        allowed = None
        for role in ctx.author.roles:
            role_id = str(role.id)
            if role_id not in channel_roles:
                continue
            permission = self.get_target_permission(channel_roles[role_id], targets)
            if permission == "deny":
                return "deny"
            if permission == "allow":
                allowed = "allow"
        return allowed

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
            commands.RoleConverter(),
            commands.TextChannelConverter()
        ]

        for converter in converters:
            try:
                return await converter.convert(ctx, scope)
            except commands.BadArgument:
                continue

        if scope.isdigit():
            role = ctx.guild.get_role(int(scope))
            if role is not None:
                return role
            channel = ctx.guild.get_channel(int(scope))
            if channel is not None:
                return channel

        normalized_scope = self.normalize_name(scope)

        channel = discord.utils.get(ctx.guild.channels, name=scope)
        if channel is not None:
            return channel

        channel = discord.utils.get(ctx.guild.channels, name=normalized_scope)
        if channel is not None:
            return channel

        for channel in ctx.guild.channels:
            if self.normalize_name(channel.name) == normalized_scope:
                return channel

        role = discord.utils.get(ctx.guild.roles, name=scope)
        if role is not None:
            return role

        role = discord.utils.get(ctx.guild.roles, name=normalized_scope)
        if role is not None:
            return role

        for role in ctx.guild.roles:
            if self.normalize_name(role.name) == normalized_scope:
                return role

        raise commands.BadArgument(f"Could not resolve scope: {scope}")

    @permset.command()
    async def local(self, ctx, action: str, *args: str):
        """Set local permissions for a user, role, channel, or role+channel on one or more commands."""
        action = action.lower()
        if action not in ["allow", "deny"]:
            embed = discord.Embed(
                title="Error",
                description="Action must be 'allow' or 'deny'.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        if len(args) < 2:
            embed = discord.Embed(
                title="Error",
                description="You must specify a scope and at least one command or command group.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        scope_matches = []
        for i, candidate in enumerate(args):
            try:
                candidate_obj = await self.resolve_scope(ctx, candidate)
            except commands.BadArgument:
                continue
            scope_matches.append((i, candidate_obj))

        if not scope_matches:
            embed = discord.Embed(
                title="Error",
                description=f"Could not resolve scope from arguments: {' '.join(args)}",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        scope_obj = None
        channel_obj = None
        role_obj = None
        targets = []

        if len(scope_matches) == 1:
            idx, scope_obj = scope_matches[0]
            targets = list(args[:idx] + args[idx+1:])
        else:
            scope_indexes = {idx for idx, _ in scope_matches}
            for _, candidate_obj in scope_matches:
                if isinstance(candidate_obj, discord.abc.GuildChannel) and channel_obj is None:
                    channel_obj = candidate_obj
                elif isinstance(candidate_obj, discord.Role) and role_obj is None:
                    role_obj = candidate_obj
                elif isinstance(candidate_obj, discord.Member) and role_obj is None:
                    role_obj = candidate_obj

            if channel_obj and role_obj:
                targets = [arg for i, arg in enumerate(args) if i not in scope_indexes]
            else:
                idx, scope_obj = scope_matches[0]
                targets = list(args[:idx] + args[idx+1:])

        if not targets:
            embed = discord.Embed(
                title="Error",
                description="You must specify at least one command or command group.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        # Validate targets
        for target in targets:
            parts = target.split()
            if len(parts) == 1:
                # Cog
                cog = self.bot.get_cog(parts[0])
                if not cog:
                    embed = discord.Embed(
                        title="Error",
                        description=f"Cog `{parts[0]}` not found.",
                        color=discord.Color.red()
                    )
                    return await ctx.send(embed=embed)
            else:
                # Command
                cog_name = parts[0]
                command_parts = parts[1:]
                cog = self.bot.get_cog(cog_name)
                if not cog:
                    embed = discord.Embed(
                        title="Error",
                        description=f"Cog `{cog_name}` not found.",
                        color=discord.Color.red()
                    )
                    return await ctx.send(embed=embed)
                command = cog
                for part in command_parts:
                    command = command.get_command(part)
                    if not command:
                        embed = discord.Embed(
                            title="Error",
                            description=f"Command `{part}` not found in {command.qualified_name if hasattr(command, 'qualified_name') else 'cog'}.",
                            color=discord.Color.red()
                        )
                        return await ctx.send(embed=embed)

        scope_secondary_id = None
        if channel_obj and role_obj:
            config_section = self.config.guild(ctx.guild).channel_role_permissions()
            scope_id = str(channel_obj.id)
            scope_secondary_id = str(role_obj.id)
            subject_name = f"role {role_obj.mention} in channel {channel_obj.mention}"
        elif isinstance(scope_obj, discord.Member):
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
            if scope_secondary_id is not None:
                if scope_secondary_id not in perms[scope_id]:
                    perms[scope_id][scope_secondary_id] = {}
                for target in targets:
                    perms[scope_id][scope_secondary_id][target.lower()] = action
            else:
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
    async def reset(self, ctx):
        """Reset all stored permissions for this guild."""
        async with self.config.guild(ctx.guild).global_permissions() as perms:
            perms.clear()
        async with self.config.guild(ctx.guild).user_permissions() as perms:
            perms.clear()
        async with self.config.guild(ctx.guild).role_permissions() as perms:
            perms.clear()
        async with self.config.guild(ctx.guild).channel_permissions() as perms:
            perms.clear()
        async with self.config.guild(ctx.guild).channel_role_permissions() as perms:
            perms.clear()

        embed = discord.Embed(
            title="Permissions Reset",
            description="✅ All permissions have been cleared for this guild.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @permset.command()
    async def debug(self, ctx, *, command_name: str):
        """Debug permission resolution for a command."""
        if not ctx.guild:
            await ctx.send("Not in a guild.")
            return

        # Parse command_name
        parts = command_name.split()
        if not parts:
            await ctx.send("Specify a command, e.g. `mafia unban`")
            return

        cog_name = parts[0]
        command_parts = parts[1:]

        cog = self.bot.get_cog(cog_name)
        if not cog:
            await ctx.send(f"Cog '{cog_name}' not found.")
            return

        command = cog
        for part in command_parts:
            if hasattr(command, 'get_command'):
                command = command.get_command(part)
            else:
                await ctx.send(f"Command '{part}' not found in {command.qualified_name if hasattr(command, 'qualified_name') else 'cog'}.")
                return
            if not command:
                await ctx.send(f"Command '{part}' not found in {getattr(command, 'qualified_name', 'cog')}.")
                return

        targets = self.get_command_targets_from_command(command, cog)
        debug_msg = f"**Command Targets:** {', '.join(f'`{t}`' for t in targets)}\n\n"

        # User permissions
        user_perms = await self.config.guild(ctx.guild).user_permissions()
        user_id = str(ctx.author.id)
        if user_id in user_perms:
            user_perm = self.get_target_permission(user_perms[user_id], targets)
            debug_msg += f"**User Permission:** {user_perm or 'None'}\n"
        else:
            debug_msg += "**User Permission:** None\n"

        # Channel+role permissions
        channel_role_perms = await self.config.guild(ctx.guild).channel_role_permissions()
        channel_id = str(ctx.channel.id)
        channel_role_perm = self.get_channel_role_permission(channel_role_perms, channel_id, targets, ctx)
        debug_msg += f"**Channel+Role Permission:** {channel_role_perm or 'None'}\n"

        # Channel permissions
        channel_perms = await self.config.guild(ctx.guild).channel_permissions()
        if channel_id in channel_perms:
            channel_perm = self.get_target_permission(channel_perms[channel_id], targets)
            debug_msg += f"**Channel Permission:** {channel_perm or 'None'}\n"
        else:
            debug_msg += "**Channel Permission:** None\n"

        # Role permissions
        role_perms = await self.config.guild(ctx.guild).role_permissions()
        role_perm = None
        for role in ctx.author.roles:
            role_id = str(role.id)
            if role_id in role_perms:
                perm = self.get_target_permission(role_perms[role_id], targets)
                if perm == "deny":
                    role_perm = "deny"
                    break
                if perm == "allow":
                    role_perm = "allow"
        debug_msg += f"**Role Permission:** {role_perm or 'None'}\n"

        # Global permissions
        global_perms = await self.config.guild(ctx.guild).global_permissions()
        global_perm = self.get_target_permission(global_perms, targets)
        debug_msg += f"**Global Permission:** {global_perm or 'None'}\n"

        embed = discord.Embed(
            title="Permission Debug",
            description=debug_msg,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @permset.command()
    async def list(self, ctx):
        """List all current permissions."""
        global_perms = await self.config.guild(ctx.guild).global_permissions()
        user_perms = await self.config.guild(ctx.guild).user_permissions()
        role_perms = await self.config.guild(ctx.guild).role_permissions()
        channel_perms = await self.config.guild(ctx.guild).channel_permissions()
        channel_role_perms = await self.config.guild(ctx.guild).channel_role_permissions()

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

        if channel_role_perms:
            channel_role_list = []
            for cid, role_map in channel_role_perms.items():
                channel = ctx.guild.get_channel(int(cid))
                channel_name = channel.mention if channel else f"Channel {cid}"
                for rid, perms in role_map.items():
                    role = ctx.guild.get_role(int(rid))
                    if role is None and str(ctx.guild.default_role.id) == rid:
                        role = ctx.guild.default_role
                    role_name = role.mention if role else f"Role {rid}"
                    channel_role_list.append(f"{channel_name} / {role_name}: " + ", ".join(f"`{k}`: {v}" for k, v in perms.items()))
            embed.add_field(name="Channel Role Permissions", value="\n".join(channel_role_list), inline=False)
        else:
            embed.add_field(name="Channel Role Permissions", value="None", inline=False)

        await ctx.send(embed=embed)

    async def global_check(self, ctx):
        """Global check for permissions."""
        if ctx.cog == self:
            return True

        if not ctx.guild:
            return True

        targets = self.get_command_targets(ctx)

        # User permissions are most specific
        user_perms = await self.config.guild(ctx.guild).user_permissions()
        user_id = str(ctx.author.id)
        if user_id in user_perms:
            user_perm = self.get_target_permission(user_perms[user_id], targets)
            if user_perm == "allow":
                return True
            if user_perm == "deny":
                return False

        # Channel+role permissions override channel/role/global.
        channel_role_perms = await self.config.guild(ctx.guild).channel_role_permissions()
        channel_id = str(ctx.channel.id)
        channel_role_perm = self.get_channel_role_permission(channel_role_perms, channel_id, targets, ctx)
        if channel_role_perm == "allow":
            return True
        if channel_role_perm == "deny":
            return False

        # Channel permissions override role/global permissions
        channel_perms = await self.config.guild(ctx.guild).channel_permissions()
        if channel_id in channel_perms:
            channel_perm = self.get_target_permission(channel_perms[channel_id], targets)
            if channel_perm == "allow":
                return True
            if channel_perm == "deny":
                return False

        # Role permissions come next; deny wins among roles at the same scope
        role_perms = await self.config.guild(ctx.guild).role_permissions()
        role_perm = None
        for role in ctx.author.roles:
            role_id = str(role.id)
            if role_id in role_perms:
                perm = self.get_target_permission(role_perms[role_id], targets)
                if perm == "deny":
                    role_perm = "deny"
                    break
                if perm == "allow":
                    role_perm = "allow"
        if role_perm == "allow":
            return True
        if role_perm == "deny":
            return False

        # Global permissions are the least specific
        global_perms = await self.config.guild(ctx.guild).global_permissions()
        global_perm = self.get_target_permission(global_perms, targets)
        if global_perm == "allow":
            return True
        if global_perm == "deny":
            return False

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