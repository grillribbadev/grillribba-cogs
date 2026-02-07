from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple, Literal

import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

from .constants import CONFIG_IDENTIFIER, EMBED_COLOR
from .utils import (
    member_has_any_role,
    format_user_line,
    parse_time,
    humanize_timedelta,
    get_audit_reason,
)

log = logging.getLogger("red.mutelist")

# Config schema:
# guild:
#   roles: list[int]        # role IDs that count as 'muted'
#   use_auditlog: bool      # attempt to read reason from Audit Log
#   log_channel: int|None   # channel to log mute/unmute events
#   mutes: { str(member_id): {"reason": str, "moderator": int, "at": int|None, "until": int|None, "cautions": str|None} }
DEFAULT_GUILD = {
    "roles": [],
    "use_auditlog": True,
    "log_channel": None,
    "mutes": {},
}


# ══════════════════════════════════════════════════════════════════════════════
# Discord UI Components
# ══════════════════════════════════════════════════════════════════════════════


class MemberReasonModal(discord.ui.Modal, title="Set mute reason"):
    """Modal for setting/updating a mute reason."""
    
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.long,
        required=True,
        max_length=400,
        placeholder="Enter the reason for this mute..."
    )

    def __init__(self, cog: "MuteList", guild: discord.Guild, member_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild = guild
        self.member_id = member_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.cog.set_reason(self.guild, self.member_id, self.reason.value)
            await interaction.response.send_message(
                f"✅ Reason saved for <@{self.member_id}>.",
                ephemeral=True
            )
        except Exception as e:
            log.exception("Failed to save reason")
            try:
                await interaction.response.send_message(
                    "❌ Failed to save reason.",
                    ephemeral=True
                )
            except Exception:
                pass


class MemberCautionsModal(discord.ui.Modal, title="Set cautions/warnings"):
    """Modal for setting/updating cautions for a muted member."""
    
    cautions = discord.ui.TextInput(
        label="Cautions",
        style=discord.TextStyle.long,
        required=False,
        max_length=400,
        placeholder="Enter any cautions, warnings, or notes about this member..."
    )

    def __init__(self, cog: "MuteList", guild: discord.Guild, member_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild = guild
        self.member_id = member_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.cog.set_cautions(self.guild, self.member_id, self.cautions.value)
            await interaction.response.send_message(
                f"✅ Cautions saved for <@{self.member_id}>.",
                ephemeral=True
            )
        except Exception as e:
            log.exception("Failed to save cautions")
            try:
                await interaction.response.send_message(
                    "❌ Failed to save cautions.",
                    ephemeral=True
                )
            except Exception:
                pass


class MemberSelect(discord.ui.Select):
    """Dropdown menu for selecting a muted member."""
    
    def __init__(self, parent_view: "MutedActionView", options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Select a muted member...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.user.id != self.parent_view.invoker.id:
                await interaction.response.send_message(
                    "❌ Only the command invoker may use this menu.",
                    ephemeral=True
                )
                return
            
            member_id = int(self.values[0])
            self.parent_view.selected_member_id = member_id
            guild = interaction.guild
            member = guild.get_member(member_id) if guild else None
            name = str(member) if member else f"ID {member_id}"
            
            try:
                await interaction.response.send_message(
                    f"✅ Selected **{name}**. Now choose an action below.",
                    ephemeral=True
                )
            except Exception:
                try:
                    await interaction.followup.send(
                        f"✅ Selected **{name}**. Now choose an action below.",
                        ephemeral=True
                    )
                except Exception:
                    pass
        except Exception:
            log.exception("MemberSelect callback failed")
            try:
                await interaction.response.send_message(
                    "❌ Selection failed.",
                    ephemeral=True
                )
            except Exception:
                try:
                    await interaction.followup.send("❌ Selection failed.", ephemeral=True)
                except Exception:
                    pass


class MutedActionView(discord.ui.View):
    """Interactive view with buttons for managing muted members."""
    
    def __init__(self, cog: "MuteList", ctx: commands.Context, members: List[discord.Member]):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.invoker = ctx.author
        self.guild = ctx.guild
        self.members = members
        self.selected_member_id: Optional[int] = None

        # Add member selection dropdown (max 25 options per Discord limit)
        options: List[discord.SelectOption] = []
        for m in members[:25]:
            label = (m.display_name or str(m))[:100]
            desc = f"{m} — ID: {m.id}"[:100]
            options.append(discord.SelectOption(label=label, description=desc, value=str(m.id)))
        
        if options:
            self.add_item(MemberSelect(self, options))

    async def _resolve_member(self, member_id: int) -> Optional[discord.Member]:
        """Try to get a member object from ID."""
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
        """Safely defer an interaction if not already responded."""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            return

    async def _send_response(
        self,
        interaction: discord.Interaction,
        content: str = None,
        *,
        embed: discord.Embed = None,
        ephemeral: bool = False
    ) -> None:
        """Send a response, handling both initial and followup messages."""
        try:
            if not interaction.response.is_done():
                if embed:
                    await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
                else:
                    await interaction.response.send_message(content, ephemeral=ephemeral)
            else:
                if embed:
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await interaction.followup.send(content, ephemeral=ephemeral)
        except Exception:
            try:
                if embed:
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await interaction.followup.send(content, ephemeral=ephemeral)
            except Exception:
                log.exception("Failed to send interaction message")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the command invoker can use the buttons."""
        if interaction.user.id != self.invoker.id:
            try:
                await interaction.response.send_message(
                    "❌ Only the command invoker may use this menu.",
                    ephemeral=True
                )
            except Exception:
                try:
                    await interaction.followup.send(
                        "❌ Only the command invoker may use this menu.",
                        ephemeral=True
                    )
                except Exception:
                    pass
            return False
        return True

    @discord.ui.button(label="User Info", style=discord.ButtonStyle.blurple, emoji="📊", row=0)
    async def user_info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Show detailed user information including messages, level, join date, etc."""
        try:
            if not self.selected_member_id:
                await self._send_response(
                    interaction,
                    "❌ Select a member from the dropdown first.",
                    ephemeral=True
                )
                return
            
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "❌ Guild context missing.", ephemeral=True)
                return
            
            await self._safe_defer(interaction)
            member = await self._resolve_member(self.selected_member_id)
            
            if member is None:
                await self._send_response(interaction, "❌ Member not found.", ephemeral=False)
                return
            
            # Create comprehensive user info embed
            embed = discord.Embed(
                title=f"📊 User Information: {member}",
                color=EMBED_COLOR,
                timestamp=datetime.now(timezone.utc),
            )
            
            # Set thumbnail to user's avatar
            embed.set_thumbnail(url=member.display_avatar.url)
            
            # Basic Info
            embed.add_field(
                name="👤 User",
                value=f"{member.mention}\n`{member.id}`",
                inline=True
            )
            
            # Account Creation
            created_delta = datetime.now(timezone.utc) - member.created_at
            embed.add_field(
                name="📅 Account Created",
                value=f"{discord.utils.format_dt(member.created_at, 'D')}\n({discord.utils.format_dt(member.created_at, 'R')})",
                inline=True
            )
            
            # Server Join Date
            if member.joined_at:
                join_delta = datetime.now(timezone.utc) - member.joined_at
                embed.add_field(
                    name="📥 Joined Server",
                    value=f"{discord.utils.format_dt(member.joined_at, 'D')}\n({discord.utils.format_dt(member.joined_at, 'R')})",
                    inline=True
                )
            
            # Roles (excluding @everyone)
            roles = [r.mention for r in sorted(member.roles[1:], key=lambda r: r.position, reverse=True)]
            if roles:
                roles_text = ", ".join(roles[:10])  # Limit to first 10 roles
                if len(member.roles) > 11:
                    roles_text += f" *+{len(member.roles) - 11} more*"
                embed.add_field(
                    name=f"🎭 Roles ({len(member.roles) - 1})",
                    value=roles_text,
                    inline=False
                )
            
            # Try to get Leveler/LevelUp data if available
            leveler_cog = self.cog.bot.get_cog("Leveler") or self.cog.bot.get_cog("LevelUp")
            if leveler_cog:
                try:
                    # Try Leveler format
                    if hasattr(leveler_cog, "config"):
                        user_data = await leveler_cog.config.user(member).all()
                        if user_data:
                            level = user_data.get("level", 0)
                            xp = user_data.get("xp", 0)
                            embed.add_field(
                                name="📈 Level",
                                value=f"Level: **{level}**\nXP: **{xp:,}**",
                                inline=True
                            )
                except Exception:
                    pass
            
            # Try to get message count from various message tracking cogs
            # MessageCounter, ActivityTracker, etc.
            message_count = None
            
            # Try MessageCounter
            msg_counter = self.cog.bot.get_cog("MessageCounter")
            if msg_counter and hasattr(msg_counter, "config"):
                try:
                    count = await msg_counter.config.member(member).messages()
                    if count:
                        message_count = count
                except Exception:
                    pass
            
            # Try checking if there's a messages attribute directly
            if not message_count:
                for cog_name in ["ActivityTracker", "Stats", "ServerStats", "MemberStats"]:
                    cog = self.cog.bot.get_cog(cog_name)
                    if cog and hasattr(cog, "config"):
                        try:
                            data = await cog.config.member(member).all()
                            if "messages" in data:
                                message_count = data["messages"]
                                break
                            elif "message_count" in data:
                                message_count = data["message_count"]
                                break
                        except Exception:
                            continue
            
            if message_count:
                embed.add_field(
                    name="💬 Messages",
                    value=f"**{message_count:,}** messages",
                    inline=True
                )
            
            # Permissions/Status
            perms = []
            if member.guild_permissions.administrator:
                perms.append("👑 Administrator")
            if member.guild_permissions.manage_guild:
                perms.append("⚙️ Manage Server")
            if member.guild_permissions.manage_messages:
                perms.append("🗑️ Manage Messages")
            if member.guild_permissions.ban_members:
                perms.append("🔨 Ban Members")
            if member.guild_permissions.kick_members:
                perms.append("👢 Kick Members")
            
            if perms:
                embed.add_field(
                    name="🔑 Key Permissions",
                    value="\n".join(perms[:5]),
                    inline=True
                )
            
            # Status
            status_emojis = {
                discord.Status.online: "🟢 Online",
                discord.Status.idle: "🟡 Idle",
                discord.Status.dnd: "🔴 Do Not Disturb",
                discord.Status.offline: "⚫ Offline",
            }
            embed.add_field(
                name="📡 Status",
                value=status_emojis.get(member.status, "❓ Unknown"),
                inline=True
            )
            
            # Mute info
            mute_record = await self.cog.get_mute_record(guild, member.id)
            if mute_record:
                reason = mute_record.get("reason") or "No reason provided"
                at_ts = mute_record.get("at")
                until_ts = mute_record.get("until")
                cautions = mute_record.get("cautions") or ""
                
                mute_info = f"**Reason:** {reason[:100]}\n"
                if at_ts:
                    muted_at = datetime.fromtimestamp(at_ts, tz=timezone.utc)
                    mute_info += f"**Muted:** {discord.utils.format_dt(muted_at, 'R')}\n"
                
                # Fix: Check if until_ts exists and is in the future
                if until_ts:
                    expires_at = datetime.fromtimestamp(until_ts, tz=timezone.utc)
                    now = datetime.now(timezone.utc)
                    if expires_at > now:
                        # Still temporary
                        mute_info += f"**Expires:** {discord.utils.format_dt(expires_at, 'R')}"
                    else:
                        # Expired but not cleaned up
                        mute_info += f"**Expired:** {discord.utils.format_dt(expires_at, 'R')}"
                else:
                    mute_info += "**Duration:** Permanent"
                
                if cautions:
                    mute_info += f"\n**⚠️ Cautions:** {cautions[:200]}"
                
                embed.add_field(
                    name="🔇 Mute Info",
                    value=mute_info,
                    inline=False
                )
            
            # Footer
            embed.set_footer(
                text=f"ID: {member.id} • Requested by {interaction.user}",
                icon_url=interaction.user.display_avatar.url
            )
            
            await self._send_response(interaction, embed=embed, ephemeral=True)
            
        except Exception:
            log.exception("Unhandled error in user_info button")
            await self._send_response(interaction, "❌ An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Unmute", style=discord.ButtonStyle.green, emoji="🔊", row=1)
    async def unmute(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Remove mute role(s) from selected member."""
        try:
            if not self.selected_member_id:
                await self._send_response(
                    interaction,
                    "❌ Select a member from the dropdown first.",
                    ephemeral=True
                )
                return
            
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "❌ Guild context missing.", ephemeral=True)
                return
            
            await self._safe_defer(interaction)
            member = await self._resolve_member(self.selected_member_id)
            
            if member is None:
                await self._send_response(interaction, "❌ Member not found.", ephemeral=False)
                return
            
            role_ids = set(await self.cog.config.guild(guild).roles())
            to_remove = [r for r in member.roles if r.id in role_ids]
            
            if not to_remove:
                await self._send_response(
                    interaction,
                    f"ℹ️ {member.mention} has no configured mute roles.",
                    ephemeral=False
                )
                return
            
            try:
                await member.remove_roles(*to_remove, reason=f"Unmuted by {interaction.user}")
                await self.cog.clear_mute(guild, member.id)
                await self._send_response(
                    interaction,
                    f"✅ Removed mute roles from {member.mention}.",
                    ephemeral=False
                )
            except discord.Forbidden:
                await self._send_response(
                    interaction,
                    "❌ I don't have permission to remove roles.",
                    ephemeral=True
                )
            except Exception:
                log.exception("Unmute failed")
                await self._send_response(interaction, "❌ Failed to unmute.", ephemeral=True)
        except Exception:
            log.exception("Unhandled error in unmute button")
            await self._send_response(interaction, "❌ An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.blurple, emoji="👢", row=1)
    async def kick(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Kick the selected member."""
        try:
            if not self.selected_member_id:
                await self._send_response(
                    interaction,
                    "❌ Select a member from the dropdown first.",
                    ephemeral=True
                )
                return
            
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "❌ Guild context missing.", ephemeral=True)
                return
            
            await self._safe_defer(interaction)
            member = await self._resolve_member(self.selected_member_id)
            
            if member is None:
                await self._send_response(interaction, "❌ Member not found.", ephemeral=False)
                return
            
            try:
                await member.kick(reason=f"Kicked by {interaction.user} via mutelist")
                await self.cog.clear_mute(guild, member.id)
                await self._send_response(
                    interaction,
                    f"✅ Kicked {member.mention} from the server.",
                    ephemeral=False
                )
            except discord.Forbidden:
                await self._send_response(
                    interaction,
                    "❌ I don't have permission to kick members.",
                    ephemeral=True
                )
            except Exception:
                log.exception("Kick failed")
                await self._send_response(interaction, "❌ Failed to kick member.", ephemeral=True)
        except Exception:
            log.exception("Unhandled error in kick button")
            await self._send_response(interaction, "❌ An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.red, emoji="🔨", row=1)
    async def ban(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Ban the selected member."""
        try:
            if not self.selected_member_id:
                await self._send_response(
                    interaction,
                    "❌ Select a member from the dropdown first.",
                    ephemeral=True
                )
                return
            
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "❌ Guild context missing.", ephemeral=True)
                return
            
            await self._safe_defer(interaction)
            member = await self._resolve_member(self.selected_member_id)
            
            if member is None:
                await self._send_response(interaction, "❌ Member not found.", ephemeral=False)
                return
            
            try:
                await member.ban(
                    reason=f"Banned by {interaction.user} via mutelist",
                    delete_message_days=0
                )
                await self.cog.clear_mute(guild, member.id)
                await self._send_response(
                    interaction,
                    f"✅ Banned {member.mention} from the server.",
                    ephemeral=False
                )
            except discord.Forbidden:
                await self._send_response(
                    interaction,
                    "❌ I don't have permission to ban members.",
                    ephemeral=True
                )
            except Exception:
                log.exception("Ban failed")
                await self._send_response(interaction, "❌ Failed to ban member.", ephemeral=True)
        except Exception:
            log.exception("Unhandled error in ban button")
            await self._send_response(interaction, "❌ An internal error occurred.", ephemeral=True)

    @discord.ui.button(label="Set Reason", style=discord.ButtonStyle.gray, emoji="📝", row=1)
    async def set_reason(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open modal to set/update mute reason."""
        try:
            if not self.selected_member_id:
                await self._send_response(
                    interaction,
                    "❌ Select a member from the dropdown first.",
                    ephemeral=True
                )
                return
            
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "❌ Guild context missing.", ephemeral=True)
                return
            
            modal = MemberReasonModal(self.cog, guild, self.selected_member_id)
            await interaction.response.send_modal(modal)
        except Exception:
            log.exception("Unhandled error in set_reason button")
            try:
                await self._send_response(
                    interaction,
                    "❌ Failed to open reason modal.",
                    ephemeral=True
                )
            except Exception:
                pass

    @discord.ui.button(label="Set Cautions", style=discord.ButtonStyle.gray, emoji="⚠️", row=2)
    async def set_cautions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open modal to set/update cautions."""
        try:
            if not self.selected_member_id:
                await self._send_response(
                    interaction,
                    "❌ Select a member from the dropdown first.",
                    ephemeral=True
                )
                return
            
            guild = interaction.guild
            if guild is None:
                await self._send_response(interaction, "❌ Guild context missing.", ephemeral=True)
                return
            
            modal = MemberCautionsModal(self.cog, guild, self.selected_member_id)
            await interaction.response.send_modal(modal)
        except Exception:
            log.exception("Unhandled error in set_cautions button")
            try:
                await self._send_response(
                    interaction,
                    "❌ Failed to open cautions modal.",
                    ephemeral=True
                )
            except Exception:
                pass

    async def on_timeout(self) -> None:
        """Disable all buttons when the view times out."""
        try:
            for item in self.children:
                if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                    item.disabled = True
            
            # Try to edit the message to show buttons are disabled
            if self.ctx and getattr(self.ctx, "message", None):
                await self.ctx.message.edit(view=self)
        except Exception:
            try:
                # If view was sent via interaction, edit that message
                if (
                    getattr(self.ctx, "interaction", None)
                    and getattr(self.ctx.interaction, "message", None)
                ):
                    await self.ctx.interaction.message.edit(view=self)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# Main Cog
# ══════════════════════════════════════════════════════════════════════════════


class MuteList(commands.Cog):
    """Enhanced muted-member tracker with reasons, timestamps, and interactive UI.
    
    This cog tracks members with configured mute roles and maintains
    a persistent record of mute reasons, moderators, and timestamps.
    Includes interactive buttons for unmuting, kicking, banning, viewing user info,
    and updating reasons.
    """

    __version__ = "2.2.0"
    __author__ = "AfterWorld (Enhanced with UI & User Info)"

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

    def format_help_for_context(self, ctx: commands.Context) -> str:
        """Show version in help."""
        pre = super().format_help_for_context(ctx)
        return f"{pre}\n\nCog Version: {self.__version__}"

    # ── Public API you can call from your moderation cog ─────────────────────────
    async def record_mute(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        reason: str | None = None,
        moderator: Optional[discord.abc.User] = None,
        until: Optional[datetime] = None,
        cautions: str | None = None,
    ) -> None:
        """Call after you apply your mute role(s).
        
        Args:
            guild: The guild where the mute occurred
            member: The member being muted
            reason: Reason for the mute
            moderator: The moderator who applied the mute
            until: When the mute expires (None for permanent)
            cautions: Optional cautions/warnings about the member
        """
        async with self.config.guild(guild).mutes() as m:
            m[str(member.id)] = {
                "reason": (reason or "").strip(),
                "moderator": moderator.id if moderator else 0,
                "at": int(datetime.now(tz=timezone.utc).timestamp()),
                "until": int(until.replace(tzinfo=timezone.utc).timestamp()) if until else None,
                "cautions": (cautions or "").strip() if cautions else None,
            }
        
        # Log to configured channel
        await self._log_action(guild, member, "mute", reason, moderator, until)

    async def clear_mute(self, guild: discord.Guild, member_id: int) -> None:
        """Remove a mute record.
        
        Args:
            guild: The guild where the unmute occurred
            member_id: The member being unmuted
        """
        async with self.config.guild(guild).mutes() as m:
            record = m.pop(str(member_id), None)
        
        if record:
            member = guild.get_member(member_id)
            if member:
                await self._log_action(guild, member, "unmute", None, None, None)

    async def set_reason(self, guild: discord.Guild, member_id: int, reason: str) -> None:
        """Update the reason for a mute.
        
        Args:
            guild: The guild where the mute exists
            member_id: The muted member
            reason: New reason for the mute
        """
        async with self.config.guild(guild).mutes() as m:
            rec = m.get(str(member_id))
            if rec is None:
                rec = {
                    "reason": reason,
                    "moderator": 0,
                    "at": int(datetime.now(tz=timezone.utc).timestamp()),
                    "until": None,
                    "cautions": None,
                }
                m[str(member_id)] = rec
            else:
                rec["reason"] = reason

    async def set_cautions(self, guild: discord.Guild, member_id: int, cautions: str) -> None:
        """Update the cautions for a muted member.
        
        Args:
            guild: The guild where the mute exists
            member_id: The muted member
            cautions: Cautions/warnings about the member
        """
        async with self.config.guild(guild).mutes() as m:
            rec = m.get(str(member_id))
            if rec is None:
                rec = {
                    "reason": "",
                    "moderator": 0,
                    "at": int(datetime.now(tz=timezone.utc).timestamp()),
                    "until": None,
                    "cautions": cautions,
                }
                m[str(member_id)] = rec
            else:
                rec["cautions"] = cautions

    async def get_mute_record(self, guild: discord.Guild, member_id: int) -> dict | None:
        """Get the mute record for a member.
        
        Args:
            guild: The guild to check
            member_id: The member to look up
            
        Returns:
            Mute record dict or None if not found
        """
        mutes = await self.config.guild(guild).mutes()
        return mutes.get(str(member_id))

    # ── Role-change listener keeps storage tidy ────────────────────────────────
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Track role changes to maintain mute records."""
        guild = after.guild
        role_ids = set(await self.config.guild(guild).roles())
        if not role_ids:
            return

        had = member_has_any_role(before, role_ids)
        has = member_has_any_role(after, role_ids)

        if had and not has:
            # Role removed - clear mute
            await self.clear_mute(guild, after.id)
        elif not had and has:
            # Role added - create placeholder if no record exists
            existing = await self.get_mute_record(guild, after.id)
            if not existing:
                await self.record_mute(guild, after, reason="", moderator=None)

    # ── Config commands ────────────────────────────────────────────────────────
    @commands.group(name="mutelist", aliases=["ml"])
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
            await ctx.send(
                "❌ No mute roles configured. Add some with `[p]mutelist addrole @Role`.",
                reference=ctx.message.to_reference(fail_if_not_exists=False),
            )
            return

        embed = discord.Embed(
            title="📋 Configured Mute Roles",
            color=EMBED_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        
        valid_roles = []
        invalid_roles = []
        
        for rid in ids:
            role = guild.get_role(rid)
            if role:
                valid_roles.append(f"• {role.mention} — `{rid}`")
            else:
                invalid_roles.append(f"• ~~Deleted role~~ — `{rid}`")
        
        if valid_roles:
            embed.add_field(name="Active Roles", value="\n".join(valid_roles), inline=False)
        if invalid_roles:
            embed.add_field(name="⚠️ Invalid Roles", value="\n".join(invalid_roles), inline=False)
            embed.set_footer(text="Use [p]mutelist cleanroles to remove invalid roles")
        
        await ctx.send(embed=embed)

    @mutelist_group.command(name="addrole")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def mutelist_addrole(self, ctx: commands.Context, role: discord.Role):
        """Add a role to the muted-roles list."""
        async with self.config.guild(ctx.guild).roles() as ids:
            if role.id in ids:
                await ctx.send(
                    f"ℹ️ {role.mention} is already configured as a mute role.",
                    reference=ctx.message.to_reference(fail_if_not_exists=False),
                )
                return
            ids.append(role.id)
        
        await ctx.send(
            f"✅ Added {role.mention} to mute roles.",
            reference=ctx.message.to_reference(fail_if_not_exists=False),
        )

    @mutelist_group.command(name="delrole", aliases=["removerole"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def mutelist_delrole(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the muted-roles list."""
        async with self.config.guild(ctx.guild).roles() as ids:
            try:
                ids.remove(role.id)
            except ValueError:
                await ctx.send(
                    f"❌ {role.mention} was not configured as a mute role.",
                    reference=ctx.message.to_reference(fail_if_not_exists=False),
                )
                return
        
        await ctx.send(
            f"✅ Removed {role.mention} from mute roles.",
            reference=ctx.message.to_reference(fail_if_not_exists=False),
        )

    @mutelist_group.command(name="clearroles")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def mutelist_clearroles(self, ctx: commands.Context):
        """Clear all configured mute roles."""
        await self.config.guild(ctx.guild).roles.clear()
        await ctx.send(
            "✅ Cleared all configured mute roles.",
            reference=ctx.message.to_reference(fail_if_not_exists=False),
        )

    @mutelist_group.command(name="cleanroles")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def mutelist_cleanroles(self, ctx: commands.Context):
        """Remove deleted/invalid roles from the configuration."""
        guild = ctx.guild
        async with self.config.guild(guild).roles() as ids:
            before_count = len(ids)
            ids[:] = [rid for rid in ids if guild.get_role(rid) is not None]
            removed_count = before_count - len(ids)
        
        if removed_count:
            await ctx.send(
                f"✅ Removed {removed_count} invalid role(s) from configuration.",
                reference=ctx.message.to_reference(fail_if_not_exists=False),
            )
        else:
            await ctx.send(
                "ℹ️ No invalid roles found.",
                reference=ctx.message.to_reference(fail_if_not_exists=False),
            )

    @mutelist_group.command(name="auditscan")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_auditscan_toggle(self, ctx: commands.Context, toggle: bool):
        """Enable/disable reading reasons from the Audit Log when unknown.
        
        When enabled, if a mute has no stored reason, the bot will attempt
        to read it from Discord's Audit Log.
        """
        await self.config.guild(ctx.guild).use_auditlog.set(bool(toggle))
        status = "✅ enabled" if toggle else "❌ disabled"
        await ctx.send(
            f"Audit Log fallback is now {status}.",
            reference=ctx.message.to_reference(fail_if_not_exists=False),
        )

    @mutelist_group.command(name="logchannel")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mutelist_logchannel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Set a channel for mute/unmute logging.
        
        Use without a channel argument to disable logging.
        """
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.clear()
            await ctx.send(
                "✅ Disabled mute logging.",
                reference=ctx.message.to_reference(fail_if_not_exists=False),
            )
        else:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(
                f"✅ Mute logs will be sent to {channel.mention}.",
                reference=ctx.message.to_reference(fail_if_not_exists=False),
            )

    @mutelist_group.command(name="info")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mutelist_info(self, ctx: commands.Context, member: discord.Member):
        """Show detailed mute information for a specific member."""
        guild = ctx.guild
        record = await self.get_mute_record(guild, member.id)
        
        if not record:
            await ctx.send(
                f"ℹ️ {member.mention} has no mute record.",
                reference=ctx.message.to_reference(fail_if_not_exists=False),
            )
            return
        
        embed = discord.Embed(
            title=f"Mute Information: {member}",
            color=EMBED_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        reason = record.get("reason") or "No reason provided"
        mod_id = record.get("moderator")
        at_ts = record.get("at")
        until_ts = record.get("until")
        
        embed.add_field(name="Member", value=f"{member.mention}\n`{member.id}`", inline=True)
        
        if mod_id:
            moderator = guild.get_member(mod_id) or await self.bot.fetch_user(mod_id)
            embed.add_field(name="Moderator", value=f"{moderator.mention}\n`{mod_id}`", inline=True)
        else:
            embed.add_field(name="Moderator", value="Unknown", inline=True)
        
        if at_ts:
            muted_at = datetime.fromtimestamp(at_ts, tz=timezone.utc)
            embed.add_field(
                name="Muted At",
                value=f"{discord.utils.format_dt(muted_at, 'F')}\n({discord.utils.format_dt(muted_at, 'R')})",
                inline=False,
            )
        
        if until_ts:
            expires_at = datetime.fromtimestamp(until_ts, tz=timezone.utc)
            embed.add_field(
                name="Expires At",
                value=f"{discord.utils.format_dt(expires_at, 'F')}\n({discord.utils.format_dt(expires_at, 'R')})",
                inline=False,
            )
            
            # Calculate duration
            if at_ts:
                duration_seconds = until_ts - at_ts
                duration = humanize_timedelta(seconds=duration_seconds)
                embed.add_field(name="Duration", value=duration, inline=True)
        else:
            embed.add_field(name="Duration", value="Permanent", inline=True)
        
        embed.add_field(name="Reason", value=reason, inline=False)
        
        await ctx.send(embed=embed)

    @mutelist_group.command(name="stats")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mutelist_stats(self, ctx: commands.Context):
        """Show mute statistics for this server."""
        guild = ctx.guild
        role_ids = set(await self.config.guild(guild).roles())
        
        if not role_ids:
            await ctx.send(
                "❌ No mute roles configured.",
                reference=ctx.message.to_reference(fail_if_not_exists=False),
            )
            return
        
        # Get currently muted members
        currently_muted = [m for m in guild.members if member_has_any_role(m, role_ids)]
        
        # Get all records
        all_records = await self.config.guild(guild).mutes()
        
        # Count temporary vs permanent
        temporary = 0
        permanent = 0
        expired = 0
        now = datetime.now(timezone.utc).timestamp()
        
        for record in all_records.values():
            until = record.get("until")
            if until:
                if until > now:
                    temporary += 1
                else:
                    expired += 1
            else:
                permanent += 1
        
        embed = discord.Embed(
            title="📊 Mute Statistics",
            color=EMBED_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        
        embed.add_field(name="Currently Muted", value=str(len(currently_muted)), inline=True)
        embed.add_field(name="Total Records", value=str(len(all_records)), inline=True)
        embed.add_field(name="Configured Roles", value=str(len(role_ids)), inline=True)
        embed.add_field(name="Temporary Mutes", value=str(temporary), inline=True)
        embed.add_field(name="Permanent Mutes", value=str(permanent), inline=True)
        embed.add_field(name="Expired (Needs Cleanup)", value=str(expired), inline=True)
        
        await ctx.send(embed=embed)

    # ── List commands (prefix + slash) with Interactive UI ─────────────────────
    @commands.hybrid_command(name="mutedlist")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mutedlist(self, ctx: commands.Context):
        """Show all members holding any configured mute role, with interactive buttons."""
        await self._send_list(ctx)

    @mutelist_group.command(name="list")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def mutelist_list(self, ctx: commands.Context):
        """Same as `[p]mutedlist`."""
        await self._send_list(ctx)

    # ── Internals ──────────────────────────────────────────────────────────────
    async def _send_list(self, ctx: commands.Context):
        """Internal method to send the muted members list with UI."""
        guild = ctx.guild
        assert guild is not None

        role_ids = set(await self.config.guild(guild).roles())
        if not role_ids:
            msg = "❌ No mute roles configured. Add with `[p]mutelist addrole @Role`."
            await ctx.send(msg, reference=ctx.message.to_reference(fail_if_not_exists=False))
            return

        # Get currently muted members
        members = [m for m in guild.members if member_has_any_role(m, role_ids)]
        
        if not members:
            msg = "ℹ️ No members currently have a configured mute role."
            await ctx.send(msg, reference=ctx.message.to_reference(fail_if_not_exists=False))
            return

        store = await self.config.guild(guild).mutes()
        use_auditlog = await self.config.guild(guild).use_auditlog()

        # Sort by mute time (most recent first)
        def sort_key(m):
            rec = store.get(str(m.id), {})
            return rec.get("at") or 0
        
        members.sort(key=sort_key, reverse=True)

        # Build the list
        lines: List[str] = []
        for m in members:
            rec = store.get(str(m.id), {})
            reason = (rec.get("reason") or "").strip()
            moderator = rec.get("moderator") or 0
            at = rec.get("at")
            until = rec.get("until")
            cautions = (rec.get("cautions") or "").strip()

            # Try audit log if no reason and feature enabled
            if not reason and use_auditlog:
                audit_data = await get_audit_reason(guild, m, role_ids)
                if audit_data:
                    reason = audit_data[0]
                    moderator = audit_data[1] or moderator
                    at = audit_data[2] or at

            user_line = format_user_line(
                m,
                reason=reason or None,
                moderator_id=moderator or None,
                at_ts=at,
                until_ts=until,
            )
            
            # Add cautions if present
            if cautions:
                user_line += f"\n   ⚠️ **Cautions:** {cautions[:150]}"
            
            lines.append(user_line)

        # Chunk for Discord's limits
        chunks: List[str] = []
        cur = ""
        for line in lines:
            if len(cur) + len(line) + 1 > 1800:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        if cur:
            chunks.append(cur)

        # Create embed with first chunk
        embed = discord.Embed(
            title=f"🔇 Muted Members ({len(members)})" + (
                f" — Page 1/{len(chunks)}" if len(chunks) > 1 else ""
            ),
            description=chunks[0],
            color=EMBED_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(
            text=f"{guild.name} • Click 'User Info' to see member details",
            icon_url=guild.icon.url if guild.icon else None
        )

        # Create interactive view
        view = MutedActionView(self, ctx, members)

        # Send with appropriate method
        if getattr(ctx, "interaction", None):
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        else:
            await ctx.send(embed=embed, view=view)

        # Send additional pages if needed (without view to avoid confusion)
        for i, chunk in enumerate(chunks[1:], start=2):
            embed_extra = discord.Embed(
                title=f"🔇 Muted Members — Page {i}/{len(chunks)}",
                description=chunk,
                color=EMBED_COLOR,
                timestamp=datetime.now(timezone.utc),
            )
            await ctx.send(embed=embed_extra)

    async def _log_action(
        self,
        guild: discord.Guild,
        member: discord.Member,
        action: Literal["mute", "unmute"],
        reason: str | None,
        moderator: discord.abc.User | None,
        until: datetime | None,
    ):
        """Log mute/unmute actions to configured channel."""
        channel_id = await self.config.guild(guild).log_channel()
        if not channel_id:
            return
        
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return
        
        # Check permissions
        if not channel.permissions_for(guild.me).send_messages:
            return
        
        embed = discord.Embed(
            color=discord.Color.orange() if action == "mute" else discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        
        if action == "mute":
            embed.title = "🔇 Member Muted"
            embed.add_field(name="Member", value=f"{member.mention}\n`{member.id}`", inline=True)
            if moderator:
                embed.add_field(name="Moderator", value=f"{moderator.mention}\n`{moderator.id}`", inline=True)
            if until:
                embed.add_field(
                    name="Duration",
                    value=f"Until {discord.utils.format_dt(until, 'F')}\n({discord.utils.format_dt(until, 'R')})",
                    inline=False,
                )
            else:
                embed.add_field(name="Duration", value="Permanent", inline=False)
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)
        else:
            embed.title = "🔊 Member Unmuted"
            embed.add_field(name="Member", value=f"{member.mention}\n`{member.id}`", inline=True)
        
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.debug(f"Failed to send log message to channel {channel_id} in guild {guild.id}")
