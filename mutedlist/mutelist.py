from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .constants import CONFIG_IDENTIFIER, EMBED_COLOR
from .utils import member_has_any_role, format_user_line

log = logging.getLogger(__name__)


class _MemberActionViews:
    """Helper container for UI views used by `mutedlist` to avoid polluting the cog namespace.

    This groups the Select and action Buttons used to pick a member and perform
    moderation actions (unmute/kick/ban). Views send ephemeral followups to the
    invoking staff member and verify the clicker is the original invoker.
    """

    class MemberSelect(discord.ui.Select):
        def __init__(self, options: list[discord.SelectOption], parent_view: "_MemberActionViews.ActionView") -> None:
            super().__init__(placeholder="Select a muted member...", min_values=1, max_values=1, options=options)
            self.parent_view = parent_view

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            try:
                if interaction.user.id != self.parent_view.invoker.id:
                    # quick response, no defer
                    await interaction.response.send_message("Only the command invoker may use this menu.", ephemeral=True)
                    return
                sel = int(self.values[0])
                member = self.parent_view.guild.get_member(sel)
                if member is None:
                    await interaction.response.send_message("That member is no longer in the guild.", ephemeral=True)
                    return
                # defer then send followup with action buttons to avoid timing out
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                await interaction.followup.send(f"Selected **{member}**. Choose an action:", view=_MemberActionViews.ActionButtons(self.parent_view, member), ephemeral=True)
            except Exception as e:
                log.exception("MemberSelect callback failed: %r", e)
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("An internal error occurred.", ephemeral=True)
                    else:
                        await interaction.followup.send("An internal error occurred.", ephemeral=True)
                except Exception:
                    pass

    class ReasonModal(discord.ui.Modal, title="Set mute reason"):
        reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.long, required=True, max_length=400)

        def __init__(self, parent_view: "_MemberActionViews.ActionView", member: discord.Member) -> None:
            super().__init__()
            self.parent_view = parent_view
            self.member = member

        async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Guild context lost.", ephemeral=True)
                return
            try:
                await self.parent_view.cog.set_reason(guild, self.member.id, self.reason.value)
                await interaction.response.send_message(f"Set reason for {self.member}.", ephemeral=True)
            except Exception as e:
                log.exception("ReasonModal submit failed: %r", e)
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Failed to set reason.", ephemeral=True)
                    else:
                        await interaction.followup.send("Failed to set reason.", ephemeral=True)
                except Exception:
                    pass

    class ActionButtons(discord.ui.View):
        def __init__(self, parent_view: "_MemberActionViews.ActionView", member: discord.Member) -> None:
            super().__init__(timeout=60)
            self.parent_view = parent_view
            self.member = member

        async def _check_invoker(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.parent_view.invoker.id:
                await interaction.response.send_message("Only the command invoker may perform actions.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Unmute", style=discord.ButtonStyle.green)
        async def unmute(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
            try:
                if not await self._check_invoker(interaction):
                    return
                guild = interaction.guild
                assert guild is not None
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                # remove configured mute roles
                role_ids = set(await self.parent_view.cog.config.guild(guild).roles())
                to_remove = [r for r in self.member.roles if r.id in role_ids]
                if not to_remove:
                    await interaction.followup.send(f"{self.member} has no configured mute roles.", ephemeral=True)
                    return
                await self.member.remove_roles(*to_remove, reason=f"Unmuted by {interaction.user}")
                await self.parent_view.cog.clear_mute(guild, self.member.id)
                await interaction.followup.send(f"Removed mute roles from {self.member}.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I do not have permission to remove those roles.", ephemeral=True)
            except Exception as e:
                log.exception("Unmute button failed: %r", e)
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Failed to unmute.", ephemeral=True)
                    else:
                        await interaction.followup.send("Failed to unmute.", ephemeral=True)
                except Exception:
                    pass

        @discord.ui.button(label="Kick", style=discord.ButtonStyle.blurple)
        async def kick(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
            try:
                if not await self._check_invoker(interaction):
                    return
                guild = interaction.guild
                assert guild is not None
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                if not interaction.user.guild_permissions.kick_members:
                    await interaction.followup.send("You lack `Kick Members` permission.", ephemeral=True)
                    return
                if guild.me is None:
                    await interaction.followup.send("Bot member not found (missing Members intent).", ephemeral=True)
                    return
                if not guild.me.guild_permissions.kick_members:
                    await interaction.followup.send("I lack `Kick Members` permission.", ephemeral=True)
                    return
                await guild.kick(self.member, reason=f"Kicked by {interaction.user}")
                await self.parent_view.cog.clear_mute(guild, self.member.id)
                await interaction.followup.send(f"Kicked {self.member}.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I do not have permission to kick that member.", ephemeral=True)
            except Exception as e:
                log.exception("Kick button failed: %r", e)
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Failed to kick.", ephemeral=True)
                    else:
                        await interaction.followup.send("Failed to kick.", ephemeral=True)
                except Exception:
                    pass

        @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger)
        async def ban(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
            try:
                if not await self._check_invoker(interaction):
                    return
                guild = interaction.guild
                assert guild is not None
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                if not interaction.user.guild_permissions.ban_members:
                    await interaction.followup.send("You lack `Ban Members` permission.", ephemeral=True)
                    return
                if guild.me is None:
                    await interaction.followup.send("Bot member not found (missing Members intent).", ephemeral=True)
                    return
                if not guild.me.guild_permissions.ban_members:
                    await interaction.followup.send("I lack `Ban Members` permission.", ephemeral=True)
                    return
                await guild.ban(self.member, reason=f"Banned by {interaction.user}")
                await self.parent_view.cog.clear_mute(guild, self.member.id)
                await interaction.followup.send(f"Banned {self.member}.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I do not have permission to ban that member.", ephemeral=True)
            except Exception as e:
                log.exception("Ban button failed: %r", e)
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Failed to ban.", ephemeral=True)
                    else:
                        await interaction.followup.send("Failed to ban.", ephemeral=True)
                except Exception:
                    pass

        @discord.ui.button(label="Set Reason", style=discord.ButtonStyle.secondary)
        async def setreason(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
            try:
                if not await self._check_invoker(interaction):
                    return
                # open modal to capture reason
                modal = _MemberActionViews.ReasonModal(self.parent_view, self.member)
                await interaction.response.send_modal(modal)
            except Exception as e:
                log.exception("SetReason button failed: %r", e)
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Failed to open reason modal.", ephemeral=True)
                    else:
                        await interaction.followup.send("Failed to open reason modal.", ephemeral=True)
                except Exception:
                    pass

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
            try:
                if not await self._check_invoker(interaction):
                    return
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                await interaction.followup.send("Action cancelled.", ephemeral=True)
            except Exception as e:
                log.exception("Cancel button failed: %r", e)
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Failed to cancel.", ephemeral=True)
                    else:
                        await interaction.followup.send("Failed to cancel.", ephemeral=True)
                except Exception:
                    pass

    class ActionView(discord.ui.View):
        def __init__(self, cog: "MuteList", ctx: commands.Context, guild: discord.Guild, members: list[discord.Member]) -> None:
            super().__init__(timeout=120)
            self.cog = cog
            self.invoker = ctx.author
            self.guild = guild
            # build select options (max 25)
            opts: list[discord.SelectOption] = []
            for m in members[:25]:
                label = (m.display_name or str(m))[:100]
                desc = f"id: {m.id}"
                opts.append(discord.SelectOption(label=label, description=desc, value=str(m.id)))
            if not opts:
                return
            self.add_item(_MemberActionViews.MemberSelect(opts, self))


# Config schema:
# guild:
#   roles: list[int]        # role IDs that count as 'muted'
#   use_auditlog: bool      # attempt to read reason from Audit Log
#   mutes: { str(member_id): {"reason": str, "moderator": int, "at": int|None, "until": int|None} }
DEFAULT_GUILD = {
    "roles": [],
    "use_auditlog": True,
    "mutes": {},
}

class MuteList(commands.Cog):
    """Configurable muted-roles list with reasons."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

    # ── Public API you can call from your moderation cog ─────────────────────────
    async def record_mute(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        reason: str | None,
        moderator: Optional[discord.abc.User],
        until: Optional[datetime] = None,
    ) -> None:
        """Call after you apply your mute role(s)."""
        async with self.config.guild(guild).mutes() as m:
            m[str(member.id)] = {
                "reason": (reason or "").strip(),
                "moderator": moderator.id if moderator else 0,
                "at": int(datetime.now(tz=timezone.utc).timestamp()),
                "until": int(until.replace(tzinfo=timezone.utc).timestamp()) if until else None,
            }

    async def clear_mute(self, guild: discord.Guild, member_id: int) -> None:
        async with self.config.guild(guild).mutes() as m:
            m.pop(str(member_id), None)

    async def set_reason(self, guild: discord.Guild, member_id: int, reason: str) -> None:
        async with self.config.guild(guild).mutes() as m:
            rec = m.get(str(member_id))
            if rec is None:
                rec = {"reason": reason, "moderator": 0, "at": int(datetime.now(tz=timezone.utc).timestamp()), "until": None}
                m[str(member_id)] = rec
            else:
                rec["reason"] = reason

    # ── Role-change listener keeps storage tidy if staff add/remove manually ─────
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        role_ids = set(await self.config.guild(guild).roles())
        if not role_ids:
            return

        had = member_has_any_role(before, role_ids)
        has = member_has_any_role(after, role_ids)

        if had and not has:
            await self.clear_mute(guild, after.id)
        elif not had and has:
            # placeholder record, no reason yet
            await self.record_mute(guild, after, reason="", moderator=None)

    # ── Config commands for which roles to check ─────────────────────────────────
    @commands.group(name="mutelist")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_group(self, ctx: commands.Context):
        """Manage and use the muted-member listing."""

    @mutelist_group.command(name="roles")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_roles_show(self, ctx: commands.Context):
        """Show the configured mute roles."""
        guild = ctx.guild
        ids = await self.config.guild(guild).roles()
        if not ids:
            await ctx.reply("No mute roles configured. Add some with `[p]mutelist addrole @Role`.", mention_author=False)
            return
        names = []
        for rid in ids:
            r = guild.get_role(rid)
            names.append(f"{r.name} (`{rid}`)" if r else f"(deleted role `{rid}`)")
        await ctx.reply("Configured mute roles:\n- " + "\n- ".join(names), mention_author=False)

    @mutelist_group.command(name="addrole")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def mutelist_addrole(self, ctx: commands.Context, role: discord.Role):
        """Add a role to the muted-roles list."""
        async with self.config.guild(ctx.guild).roles() as ids:
            if role.id in ids:
                await ctx.reply(f"{role.mention} is already configured.", mention_author=False)
                return
            ids.append(role.id)
        await ctx.reply(f"Added {role.mention} to mute roles.", mention_author=False)

    @mutelist_group.command(name="delrole")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def mutelist_delrole(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the muted-roles list."""
        async with self.config.guild(ctx.guild).roles() as ids:
            try:
                ids.remove(role.id)
            except ValueError:
                await ctx.reply("That role was not configured.", mention_author=False)
                return
        await ctx.reply(f"Removed {role.mention} from mute roles.", mention_author=False)

    @mutelist_group.command(name="clearroles")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def mutelist_clearroles(self, ctx: commands.Context):
        """Clear all configured mute roles."""
        await self.config.guild(ctx.guild).roles.clear()
        await ctx.reply("Cleared all configured mute roles.", mention_author=False)

    @mutelist_group.command(name="auditscan")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_auditscan_toggle(self, ctx: commands.Context, toggle: bool):
        """Enable/disable reading reasons from the Audit Log when unknown."""
        await self.config.guild(ctx.guild).use_auditlog.set(bool(toggle))
        await ctx.reply(f"Audit Log fallback is now {'enabled' if toggle else 'disabled'}.", mention_author=False)

    # ── List commands (prefix + slash) ───────────────────────────────────────────
    @commands.hybrid_command(name="mutedlist")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mutedlist(self, ctx: commands.Context):
        """Show all members holding any configured mute role, with reasons."""
        await self._send_list(ctx)

    @mutelist_group.command(name="list")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mutelist_list(self, ctx: commands.Context):
        """Same as `[p]mutedlist`."""
        await self._send_list(ctx)

    # ── internals ────────────────────────────────────────────────────────────────
    async def _send_list(self, ctx: commands.Context):
        guild = ctx.guild
        assert guild is not None

        role_ids = set(await self.config.guild(guild).roles())
        if not role_ids:
            await ctx.reply("No mute roles configured. Add with `[p]mutelist addrole @Role`.", mention_author=False)
            return

        members = [m for m in guild.members if member_has_any_role(m, role_ids)]
        store = await self.config.guild(guild).mutes()
        use_auditlog = await self.config.guild(guild).use_auditlog()

        lines: list[str] = []
        for m in sorted(members, key=lambda x: x.joined_at or datetime.now()):
            rec = store.get(str(m.id), {})
            reason = (rec.get("reason") or "").strip()
            moderator = rec.get("moderator") or 0
            at = rec.get("at")
            until = rec.get("until")

            if not reason and use_auditlog:
                reason, moderator, at = await self._audit_reason_for_mute(guild, m, role_ids, fallback=(reason, moderator, at))

            lines.append(
                format_user_line(
                    m,
                    reason=reason or None,
                    moderator_id=moderator or None,
                    at_ts=at,
                    until_ts=until,
                )
            )

        if not lines:
            # prefer ephemeral for slash usage to avoid channel spam
            if getattr(ctx, "interaction", None):
                await ctx.interaction.response.send_message("No members currently have a configured mute role.", ephemeral=True)
            else:
                await ctx.reply("No members currently have a configured mute role.", mention_author=False)
            return

        # chunk messages to stay under limits
        chunks: list[str] = []
        cur = ""
        for line in lines:
            if len(cur) + len(line) + 1 > 1800:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        if cur:
            chunks.append(cur)

        # create action view (selection + action buttons). view limits to first 25 members.
        action_view = _MemberActionViews.ActionView(self, ctx, guild, members)

        # send
        if getattr(ctx, "interaction", None):
            # first page via initial response (ephemeral), attach view to first message
            emb = discord.Embed(title=f"Muted members ({len(members)}) — page 1/{len(chunks)}", description=chunks[0], color=EMBED_COLOR)
            await ctx.interaction.response.send_message(embed=emb, view=action_view, ephemeral=True)
            for i, chunk in enumerate(chunks[1:], start=2):
                emb2 = discord.Embed(title=f"Muted members — page {i}/{len(chunks)}", description=chunk, color=EMBED_COLOR)
                await ctx.interaction.followup.send(embed=emb2, ephemeral=True)
        else:
            # attach view to first message; subsequent pages are plain
            for i, chunk in enumerate(chunks, start=1):
                emb = discord.Embed(title=f"Muted members ({len(members)}) — page {i}/{len(chunks)}", description=chunk, color=EMBED_COLOR)
                if i == 1:
                    await ctx.reply(embed=emb, view=action_view, mention_author=False)
                else:
                    await ctx.send(embed=emb)

    async def _audit_reason_for_mute(
        self,
        guild: discord.Guild,
        member: discord.Member,
        role_ids: set[int],
        *,
        fallback: tuple[str, int, int | None],
    ) -> tuple[str, int, int | None]:
        """
        Try to infer reason from the Audit Log for the most recent role add that
        includes any configured mute role. Returns (reason, moderator_id, at_ts).
        """
        try:
            # Needs View Audit Log
            async for entry in guild.audit_logs(limit=25, action=discord.AuditLogAction.member_role_update):
                if entry.target.id != member.id:
                    continue
                # check if a configured mute role was added in this change set
                added = set(getattr(entry.changes.after, "roles", []) or []) - set(getattr(entry.changes.before, "roles", []) or [])
                if any(r.id in role_ids for r in added):
                    reason = (entry.reason or "").strip()
                    mod_id = entry.user.id if entry.user else 0
                    at_ts = int(entry.created_at.replace(tzinfo=timezone.utc).timestamp()) if entry.created_at else None
                    return (reason, mod_id, at_ts)
        except discord.Forbidden:
            pass
        except Exception as e:
            log.debug("Audit log scan failed in %s: %r", guild.id, e)
        # fallback to original values
        return fallback
