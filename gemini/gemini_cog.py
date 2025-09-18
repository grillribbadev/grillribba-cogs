from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red

log = logging.getLogger("red.gemini")

GUILD_DEFAULTS = {"blocked_users": []}


class GeminiCog(commands.Cog):
    """Prevent selected users from changing their nickname (audit + 10s periodic sweep)."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2468101214, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)

        # start background sweep
        self._sweep_loop.start()

    def cog_unload(self) -> None:
        self._sweep_loop.cancel()

    # ----------------- Helpers -----------------
    async def _reset_nick(self, member: discord.Member, *, reason: Optional[str] = None) -> None:
        """Reset a member's nickname to their username (nick=None) with loop-safety."""
        # If already at username, skip.
        if member.nick is None:
            return
        try:
            await member.edit(nick=None, reason=reason or "Gemini: blocked from nickname change")
            log.debug("Reset nickname for %s (%s) in guild %s", member, member.id, member.guild.id)
        except discord.Forbidden:
            log.warning(
                "Missing permission to reset nickname for %s in guild %s",
                member.id,
                member.guild.id,
            )
        except discord.HTTPException as e:
            log.error(
                "HTTPException while resetting nickname for %s in guild %s: %s",
                member.id,
                member.guild.id,
                e,
            )
        except Exception as e:
            log.exception("Unexpected error resetting nickname for %s: %r", member.id, e)

    # ----------------- Event Listener (fast reaction) -----------------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Only act if the *nickname* field actually changed (not just global display name)
        if before.nick != after.nick:
            blocked = await self.config.guild(after.guild).blocked_users()
            if after.id in blocked:
                await self._reset_nick(after, reason="Gemini (event): blocked from nickname change")

    # ----------------- Background Sweep (reliability) -----------------
    @tasks.loop(seconds=10)
    async def _sweep_loop(self):
        for guild in list(self.bot.guilds):
            try:
                blocked = await self.config.guild(guild).blocked_users()
                if not blocked:
                    continue
                for uid in list(blocked):
                    member = guild.get_member(uid)
                    if member is None:
                        continue
                    # If they have a nickname, clear it.
                    if member.nick is not None:
                        await self._reset_nick(member, reason="Gemini (sweep): blocked from nickname change")
            except Exception as e:
                log.exception("Sweep loop error in guild %s: %r", getattr(guild, "id", "?"), e)

    @_sweep_loop.before_loop
    async def _before_sweep(self):
        await self.bot.wait_until_red_ready()

    # ----------------- Commands -----------------
    @commands.guild_only()
    @checks.admin()
    @commands.group(name="nickblock", invoke_without_command=True)
    async def nickblock(self, ctx: commands.Context):
        """Manage nickname blocklist (add/remove/list)."""
        await ctx.send_help()

    @nickblock.command(name="add")
    async def nickblock_add(self, ctx: commands.Context, member: discord.Member):
        """Add a member to the blocklist (their nick will be auto-reset)."""
        blocked = await self.config.guild(ctx.guild).blocked_users()
        if member.id in blocked:
            await ctx.send(f"{member.mention} is already blocked.")
            return
        blocked.append(member.id)
        await self.config.guild(ctx.guild).blocked_users.set(blocked)

        # Reset immediately to avoid a 10s wait
        await self._reset_nick(member, reason="Gemini (command add): blocked from nickname change")
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
        """Show all blocked members in this server."""
        blocked = await self.config.guild(ctx.guild).blocked_users()
        if not blocked:
            await ctx.send("No one is blocked.")
            return

        lines = []
        for uid in blocked:
            m = ctx.guild.get_member(uid)
            lines.append(f"- {m.mention}" if m else f"- User ID `{uid}`")
        await ctx.send("🚫 **Blocked users:**\n" + "\n".join(lines))