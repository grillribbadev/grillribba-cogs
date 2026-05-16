from __future__ import annotations

from typing import Optional

import discord
from redbot.core import Config, commands


class MuteRole(commands.Cog):
    """Simple mute role system."""

    def __init__(self, bot):
        self.bot = bot

        self.config = Config.get_conf(
            self,
            identifier=987654321123456,
            force_registration=True,
        )

        default_guild = {
            "mute_role": None,
        }

        self.config.register_guild(**default_guild)

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def muterole(self, ctx):
        """Mute role configuration."""
        pass

    @muterole.command(name="set")
    async def muterole_set(
        self,
        ctx,
        role: discord.Role,
    ):
        """Set the mute role."""

        await self.config.guild(ctx.guild).mute_role.set(role.id)

        await ctx.send(f"✅ Mute role set to {role.mention}")

    async def get_mute_role(
        self,
        guild: discord.Guild,
    ) -> Optional[discord.Role]:

        role_id = await self.config.guild(guild).mute_role()

        if role_id is None:
            return None

        return guild.get_role(role_id)

    @commands.command()
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mute(
        self,
        ctx,
        member: discord.Member,
        *,
        reason: Optional[str] = None,
    ):
        """Mute a member."""

        mute_role = await self.get_mute_role(ctx.guild)

        if mute_role is None:
            return await ctx.send(
                "❌ No mute role configured. Use `[p]muterole set <role>` first."
            )

        if mute_role in member.roles:
            return await ctx.send("❌ That user is already muted.")

        try:
            await member.add_roles(
                mute_role,
                reason=reason or f"Muted by {ctx.author}",
            )

            await ctx.send(
                f"🔇 Muted {member.mention}"
                + (f"\nReason: {reason}" if reason else "")
            )

        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to mute that user.")

    @commands.command()
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def unmute(
        self,
        ctx,
        member: discord.Member,
        *,
        reason: Optional[str] = None,
    ):
        """Unmute a member."""

        mute_role = await self.get_mute_role(ctx.guild)

        if mute_role is None:
            return await ctx.send(
                "❌ No mute role configured. Use `[p]muterole set <role>` first."
            )

        if mute_role not in member.roles:
            return await ctx.send("❌ That user is not muted.")

        try:
            await member.remove_roles(
                mute_role,
                reason=reason or f"Unmuted by {ctx.author}",
            )

            await ctx.send(
                f"🔊 Unmuted {member.mention}"
                + (f"\nReason: {reason}" if reason else "")
            )

        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to unmute that user.")


async def setup(bot):
    await bot.add_cog(MuteRole(bot))