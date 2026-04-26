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
        if target in perms:
            return perms[target]
        # Check for parent groups
        parts = target.split()
        for i in range(len(parts) - 1, 0, -1):
            group = " ".join(parts[:i])
            if group in perms:
                return perms[group]
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
        async with self.config.guild(ctx.guild).global_permissions() as perms:
            perms[target] = "deny"
        embed = discord.Embed(
            title="Permission Set",
            description=f"❌ Globally denied: `{target}`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    @permset.command()
    async def local(self, ctx, target: str, action: str, scope: Union[discord.Member, discord.TextChannel, discord.Role]):
        """Set local permissions for a user, channel, or role."""
        if action.lower() not in ["allow", "deny"]:
            embed = discord.Embed(
                title="Error",
                description="Action must be 'allow' or 'deny'.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)
        
        if isinstance(scope, discord.Member):
            async with self.config.guild(ctx.guild).user_permissions() as perms:
                user_id = str(scope.id)
                if user_id not in perms:
                    perms[user_id] = {}
                perms[user_id][target] = action.lower()
            embed = discord.Embed(
                title="Permission Set",
                description=f"{'✅' if action.lower() == 'allow' else '❌'} Set {action} for user {scope.mention} on `{target}`",
                color=discord.Color.green() if action.lower() == 'allow' else discord.Color.red()
            )
            await ctx.send(embed=embed)
        elif isinstance(scope, discord.Role):
            async with self.config.guild(ctx.guild).role_permissions() as perms:
                role_id = str(scope.id)
                if role_id not in perms:
                    perms[role_id] = {}
                perms[role_id][target] = action.lower()
            embed = discord.Embed(
                title="Permission Set",
                description=f"{'✅' if action.lower() == 'allow' else '❌'} Set {action} for role {scope.mention} on `{target}`",
                color=discord.Color.green() if action.lower() == 'allow' else discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            async with self.config.guild(ctx.guild).channel_permissions() as perms:
                channel_id = str(scope.id)
                if channel_id not in perms:
                    perms[channel_id] = {}
                perms[channel_id][target] = action.lower()
            embed = discord.Embed(
                title="Permission Set",
                description=f"{'✅' if action.lower() == 'allow' else '❌'} Set {action} for channel {scope.mention} on `{target}`",
                color=discord.Color.green() if action.lower() == 'allow' else discord.Color.red()
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
        
        # Get command and cog names
        command_name = ctx.command.qualified_name
        cog_name = ctx.cog.qualified_name if ctx.cog else None
        
        # Check user permissions
        user_perms = await self.config.guild(ctx.guild).user_permissions()
        user_id = str(ctx.author.id)
        if user_id in user_perms:
            perms = user_perms[user_id]
            cmd_perm = self.get_permission(perms, command_name)
            if cmd_perm == "deny":
                raise SilentDeny()
            cog_perm = self.get_permission(perms, cog_name) if cog_name else None
            if cog_perm == "deny":
                raise SilentDeny()
        
        # Check role permissions (any role deny denies)
        role_perms = await self.config.guild(ctx.guild).role_permissions()
        for role in ctx.author.roles:
            role_id = str(role.id)
            if role_id in role_perms:
                perms = role_perms[role_id]
                cmd_perm = self.get_permission(perms, command_name)
                if cmd_perm == "deny":
                    raise SilentDeny()
                cog_perm = self.get_permission(perms, cog_name) if cog_name else None
                if cog_perm == "deny":
                    raise SilentDeny()
        
        # Check channel permissions
        channel_perms = await self.config.guild(ctx.guild).channel_permissions()
        channel_id = str(ctx.channel.id)
        if channel_id in channel_perms:
            perms = channel_perms[channel_id]
            cmd_perm = self.get_permission(perms, command_name)
            if cmd_perm == "deny":
                raise SilentDeny()
            cog_perm = self.get_permission(perms, cog_name) if cog_name else None
            if cog_perm == "deny":
                raise SilentDeny()
        
        # Check global permissions
        global_perms = await self.config.guild(ctx.guild).global_permissions()
        cmd_perm = self.get_permission(global_perms, command_name)
        if cmd_perm == "deny":
            raise SilentDeny()
        cog_perm = self.get_permission(global_perms, cog_name) if cog_name else None
        if cog_perm == "deny":
            raise SilentDeny()
        
        # If not denied, allow
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