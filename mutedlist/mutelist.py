from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

from .constants import CONFIG_IDENTIFIER, EMBED_COLOR
from .utils import member_has_any_role, format_user_line

log = logging.getLogger(__name__)


DEFAULT_GUILD = {"roles": [], "use_auditlog": True, "mutes": {}}


class MemberReasonModal(discord.ui.Modal, title="Set mute reason"):
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.long, required=True, max_length=400)

    def __init__(self, cog: "MuteList", guild: discord.Guild, member_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild = guild
        self.member_id = member_id

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        try:
            await self.cog.set_reason(self.guild, self.member_id, self.reason.value)
            await interaction.response.send_message("Reason saved.", ephemeral=False)
        except Exception:
            log.exception("Failed to save reason")
            try:
                await interaction.response.send_message("Failed to save reason.", ephemeral=True)
            except Exception:
                pass


class MemberSelect(discord.ui.Select):
    def __init__(self, parent_view: "MutedActionView", options: List[discord.SelectOption]):
        super().__init__(placeholder="Select a muted member...", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        try:
            if interaction.user.id != self.parent_view.invoker.id:
                await interaction.response.send_message("Only the command invoker may use this menu.", ephemeral=True)
                return
            member_id = int(self.values[0])
            self.parent_view.selected_member_id = member_id
            guild = interaction.guild
            member = guild.get_member(member_id) if guild else None
            name = str(member) if member else f"ID {member_id}"
            # send ephemeral confirmation so original message remains with the view
            try:
                await interaction.response.send_message(f"Selected {name}. Now choose an action.", ephemeral=True)
            except Exception:
                # fallback to followup
                try:
                    await interaction.followup.send(f"Selected {name}. Now choose an action.", ephemeral=True)
                except Exception:
                    pass
        except Exception:
            log.exception("MemberSelect callback failed")
            try:
                await interaction.response.send_message("Selection failed.", ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send("Selection failed.", ephemeral=True)
                except Exception:
                    pass


class MutedActionView(discord.ui.View):
    def __init__(self, cog: "MuteList", ctx: commands.Context, members: List[discord.Member]):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.invoker = ctx.author
        self.guild = ctx.guild
        self.members = members
        self.selected_member_id: Optional[int] = None

        options: List[discord.SelectOption] = []
        for m in members[:25]:
            label = (m.display_name or str(m))[:100]
            desc = f"{m} — id:{m.id}"
            options.append(discord.SelectOption(label=label, description=desc, value=str(m.id)))
        if options:
            self.add_item(MemberSelect(self, options))

    async def _resolve_member(self, member_id: int) -> Optional[discord.Member]:
        guild = self.guild
        if guild is None:
            return None
        member = guild.get_member(member_id)
        if member is not None:
            return member
        try:
            member = await guild.fetch_member(member_id)
            return member
        except Exception:
            return None

    async def _safe_defer(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            return

    async def _send_response(self, interaction: discord.Interaction, content: str, *, ephemeral: bool = False) -> None:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(content, ephemeral=ephemeral)
            else:
                await interaction.followup.send(content, ephemeral=ephemeral)
        except Exception:
            try:
                await interaction.followup.send(content, ephemeral=ephemeral)
            except Exception:
                log.exception("Failed to send interaction message")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            try:
                await interaction.response.send_message("Only the command invoker may use this menu.", ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send("Only the command invoker may use this menu.", ephemeral=True)
                except Exception:
                    pass
            return False
        return True

    @discord.ui.button(label="Unmute", style=discord.ButtonStyle.green)
    async def unmute(self, interaction: discord.Interaction) -> None:
        try:
            if not self.selected_member_id:
                await self._send_response(interaction, "Select a member first.", ephemeral=True)
                return
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "Guild context missing.", ephemeral=True)
                return
            await self._safe_defer(interaction)
            member = await self._resolve_member(self.selected_member_id)
            if member is None:
                await self._send_response(interaction, "Member not found.", ephemeral=False)
                return
            role_ids = set(await self.cog.config.guild(guild).roles())
            to_remove = [r for r in member.roles if r.id in role_ids]
            if not to_remove:
                await self._send_response(interaction, f"{member} has no configured mute roles.", ephemeral=False)
                return
            try:
                await member.remove_roles(*to_remove, reason=f"Unmuted by {interaction.user}")
                await self.cog.clear_mute(guild, member.id)
                await self._send_response(interaction, f"Removed mute roles from {member}.", ephemeral=False)
            except discord.Forbidden:
                await self._send_response(interaction, "I don't have permission to remove roles.", ephemeral=True)
            except Exception:
                log.exception("Unmute failed")
                await self._send_response(interaction, "Failed to unmute.", ephemeral=True)
        except Exception:
            log.exception("Unhandled error in unmute button")
            await self._send_response(interaction, "An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.blurple)
    async def kick(self, interaction: discord.Interaction) -> None:
        try:
            if not self.selected_member_id:
                await self._send_response(interaction, "Select a member first.", ephemeral=True)
                return
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "Guild context missing.", ephemeral=True)
                return
            await self._safe_defer(interaction)
            if not interaction.user.guild_permissions.kick_members:
                await self._send_response(interaction, "You lack `Kick Members` permission.", ephemeral=True)
                return
            if guild.me is None or not guild.me.guild_permissions.kick_members:
                await self._send_response(interaction, "I lack `Kick Members` permission or Members intent.", ephemeral=True)
                return
            member = await self._resolve_member(self.selected_member_id)
            if member is None:
                await self._send_response(interaction, "Member not found.", ephemeral=False)
                return
            try:
                await guild.kick(member, reason=f"Kicked by {interaction.user}")
                await self.cog.clear_mute(guild, member.id)
                await self._send_response(interaction, f"Kicked {member}.", ephemeral=False)
            except discord.Forbidden:
                await self._send_response(interaction, "I don't have permission to kick that member.", ephemeral=True)
            except Exception:
                log.exception("Kick failed")
                await self._send_response(interaction, "Failed to kick.", ephemeral=True)
        except Exception:
            log.exception("Unhandled error in kick button")
            await self._send_response(interaction, "An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger)
    async def ban(self, interaction: discord.Interaction) -> None:
        try:
            if not self.selected_member_id:
                await self._send_response(interaction, "Select a member first.", ephemeral=True)
                return
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "Guild context missing.", ephemeral=True)
                return
            await self._safe_defer(interaction)
            if not interaction.user.guild_permissions.ban_members:
                await self._send_response(interaction, "You lack `Ban Members` permission.", ephemeral=True)
                return
            if guild.me is None or not guild.me.guild_permissions.ban_members:
                await self._send_response(interaction, "I lack `Ban Members` permission or Members intent.", ephemeral=True)
                return
            member = await self._resolve_member(self.selected_member_id)
            if member is None:
                await self._send_response(interaction, "Member not found.", ephemeral=False)
                return
            try:
                await guild.ban(member, reason=f"Banned by {interaction.user}")
                await self.cog.clear_mute(guild, member.id)
                await self._send_response(interaction, f"Banned {member}.", ephemeral=False)
            except discord.Forbidden:
                await self._send_response(interaction, "I don't have permission to ban that member.", ephemeral=True)
            except Exception:
                log.exception("Ban failed")
                await self._send_response(interaction, "Failed to ban.", ephemeral=True)
        except Exception:
            log.exception("Unhandled error in ban button")
            await self._send_response(interaction, "An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Set Reason", style=discord.ButtonStyle.secondary)
    async def setreason(self, interaction: discord.Interaction) -> None:
        try:
            if not self.selected_member_id:
                await self._send_response(interaction, "Select a member first.", ephemeral=True)
                return
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "Guild context missing.", ephemeral=True)
                return
            modal = MemberReasonModal(self.cog, guild, self.selected_member_id)
            try:
                await interaction.response.send_modal(modal)
            except Exception:
                await self._send_response(interaction, "Could not open modal.", ephemeral=True)
        except Exception:
            log.exception("Unhandled error in setreason button")
            await self._send_response(interaction, "An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction) -> None:
        try:
            try:
                await interaction.response.send_message("Menu closed.", ephemeral=False)
            except Exception:
                try:
                    await interaction.followup.send("Menu closed.", ephemeral=False)
                except Exception:
                    pass
            self.stop()
            try:
                if interaction.message:
                    await interaction.message.edit(view=self)
            except Exception:
                pass
        except Exception:
            log.exception("Unhandled error in cancel button")

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        # best-effort edit to show disabled view
        try:
            # interaction.message may be None; attempt to edit via ctx message if available
            if self.ctx and getattr(self.ctx, "message", None):
                await self.ctx.message.edit(view=self)
        except Exception:
            try:
                # if view was sent via interaction, edit that message
                if getattr(self.ctx, "interaction", None) and getattr(self.ctx.interaction, "message", None):
                    await self.ctx.interaction.message.edit(view=self)
            except Exception:
                pass


class MuteList(commands.Cog):
    """Configurable muted-roles list with reasons and simple UI actions."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

    async def record_mute(self, guild: discord.Guild, member: discord.Member, *, reason: str | None, moderator: Optional[discord.abc.User], until: Optional[datetime] = None) -> None:
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
            await self.record_mute(guild, after, reason="", moderator=None)

    @commands.group(name="mutelist")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_group(self, ctx: commands.Context):
        """Manage the mute-role configuration."""

    @mutelist_group.command(name="roles")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_roles_show(self, ctx: commands.Context):
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
        await self.config.guild(ctx.guild).roles.clear()
        await ctx.reply("Cleared all configured mute roles.", mention_author=False)

    @mutelist_group.command(name="auditscan")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_auditscan_toggle(self, ctx: commands.Context, toggle: bool):
        await self.config.guild(ctx.guild).use_auditlog.set(bool(toggle))
        await ctx.reply(f"Audit Log fallback is now {'enabled' if toggle else 'disabled'}.", mention_author=False)

    @commands.hybrid_command(name="mutedlist")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mutedlist(self, ctx: commands.Context):
        guild = ctx.guild
        assert guild is not None
        role_ids = set(await self.config.guild(guild).roles())
        if not role_ids:
            await ctx.reply("No mute roles configured. Add with `[p]mutelist addrole @Role`.", mention_author=False)
            return
        members = [m for m in guild.members if member_has_any_role(m, role_ids)]
        store = await self.config.guild(guild).mutes()
        use_auditlog = await self.config.guild(guild).use_auditlog()
        if not members:
            await ctx.reply("No members currently have a configured mute role.", mention_author=False)
            return
        lines: List[str] = []
        for m in sorted(members, key=lambda x: x.joined_at or datetime.now()):
            rec = store.get(str(m.id), {})
            reason = (rec.get("reason") or "").strip()
            moderator = rec.get("moderator") or 0
            at = rec.get("at")
            until = rec.get("until")
            if not reason and use_auditlog:
                reason, moderator, at = await self._audit_reason_for_mute(guild, m, role_ids, fallback=(reason, moderator, at))
            lines.append(format_user_line(m, reason=reason or None, moderator_id=moderator or None, at_ts=at, until_ts=until))
        chunks: List[str] = []
        cur = ""
        for line in lines:
            if len(cur) + len(line) + 1 > 1800:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        if cur:
            chunks.append(cur)
        emb = discord.Embed(title=f"Muted members ({len(members)})", description=chunks[0], color=EMBED_COLOR)
        view = MutedActionView(self, ctx, members)
        if getattr(ctx, "interaction", None):
            await ctx.interaction.response.send_message(embed=emb, view=view, ephemeral=False)
        else:
            await ctx.reply(embed=emb, view=view, mention_author=False)

    async def _audit_reason_for_mute(self, guild: discord.Guild, member: discord.Member, role_ids: set[int], *, fallback: Tuple[str, int, int | None]) -> Tuple[str, int, int | None]:
        try:
            async for entry in guild.audit_logs(limit=25, action=discord.AuditLogAction.member_role_update):
                if entry.target.id != member.id:
                    continue
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
        return fallback


