from __future__ import annotations

import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import logging

log = logging.getLogger("red.nickblocker")

GUILD_DEFAULTS = {"blocked_users": []}
ROLE_NAME = "🚫 NoNickChange"

class NickBlocker(commands.Cog):
    """Prevent selected users from changing their nickname by assigning a deny-role."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)

    async def _get_or_create_role(self, guild: discord.Guild) -> discord.Role | None:
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role:
            return role
        try:
            role = await guild.create_role(
                name=ROLE_NAME,
                permissions=discord.Permissions.none(),
                reason="NickBlocker setup",
            )
            perms = role.permissions
            perms.update(change_nickname=False)
            await role.edit(permissions=perms)
            log.info(f"Created {ROLE_NAME} role in guild {guild.id}")
            return role
        except discord.Forbidden:
            log.error(f"Missing manage roles permission to create {ROLE_NAME} in guild {guild.id}")
            return None

    async def _assign_role(self, member: discord.Member):
        role = await self._get_or_create_role(member.guild)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="NickBlocker add")
            except discord.Forbidden:
                log.warning(f"Cannot assign {ROLE_NAME} role in guild {member.guild.id}")

    async def _remove_role(self, member: discord.Member):
        role = discord.utils.get(member.guild.roles, name=ROLE_NAME)
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="NickBlocker remove")
            except discord.Forbidden:
                log.warning(f"Cannot remove {ROLE_NAME} role in guild {member.guild.id}")

    @commands.guild_only()
    @checks.admin()
    @commands.group(name="nickblock", invoke_without_command=True)
    async def nickblock(self, ctx: commands.Context):
        """Manage nickname blocklist."""
        await ctx.send_help()

    @nickblock.command(name="add")
    async def nickblock_add(self, ctx: commands.Context, member: discord.Member):
        """Add a member to the blocklist."""
        blocked = await self.config.guild(ctx.guild).blocked_users()
        if member.id in blocked:
            await ctx.send(f"{member.mention} is already blocked.")
            return
        blocked.append(member.id)
        await self.config.guild(ctx.guild).blocked_users.set(blocked)
        await self._assign_role(member)
        await ctx.send(f"🚫 {member.mention} is now blocked from changing their nickname.")

    @nickblock.command(name="remove")
    async def nickblock_remove(self, ctx: commands.Context, member: discord.Member):
        """Remove a member from the blocklist."""
        blocked = await self.config.guild(ctx.guild).blocked_users()
        if member.id not in blocked:
            await ctx.send(f"{member.mention} is not blocked.")
            return
        blocked.remove(member.id)
        await self.config.guild(ctx.guild).blocked_users.set(blocked)
        await self._remove_role(member)
        await ctx.send(f"✅ {member.mention} removed from blocklist.")

    @nickblock.command(name="list")
    async def nickblock_list(self, ctx: commands.Context):
        """Show all blocked members."""
        blocked = await self.config.guild(ctx.guild).blocked_users()
        if not blocked:
            await ctx.send("No one is blocked.")
            return
        entries = []
        for uid in blocked:
            member = ctx.guild.get_member(uid)
            if member:
                entries.append(f"- {member.mention}")
            else:
                entries.append(f"- User ID {uid}")
        msg = "🚫 Blocked users:\n" + "\n".join(entries)
        await ctx.send(msg)