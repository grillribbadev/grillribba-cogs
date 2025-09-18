from __future__ import annotations

import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import logging

log = logging.getLogger("red.gemini")

GUILD_DEFAULTS = {"blocked_users": []}


class GeminiCog(commands.Cog):
    """Prevent selected users from changing their nickname by reverting changes."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2468101214, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)

    # ----------------- Listener -----------------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Only act when nickname/display_name changes
        if before.display_name != after.display_name:
            blocked = await self.config.guild(after.guild).blocked_users()
            if after.id in blocked:
                desired_nick = None  # reset to username (no nickname)
                # Prevent infinite loop: skip if already at desired nick
                if after.nick == desired_nick:
                    return
                try:
                    await after.edit(
                        nick=desired_nick,
                        reason="Gemini: blocked from nickname change",
                    )
                    log.info(
                        "Reverted nickname for %s (%s) in guild %s",
                        after, after.id, after.guild.id,
                    )
                except discord.Forbidden:
                    log.warning(
                        "Missing permission to reset nickname for %s in guild %s",
                        after.id, after.guild.id,
                    )
                except Exception as e:
                    log.error(
                        "Error reverting nickname for %s in guild %s: %s",
                        after.id, after.guild.id, e,
                    )

    # ----------------- Commands -----------------
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

        # Reset nickname immediately
        try:
            await member.edit(
                nick=None,
                reason="Gemini: blocked from nickname change",
            )
        except discord.Forbidden:
            await ctx.send("⚠️ I lack permission to reset that member's nickname.")
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