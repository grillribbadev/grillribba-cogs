from __future__ import annotations

import logging
from typing import Iterable, Optional

import discord
from redbot.core import commands, checks
from redbot.core.bot import Red

log = logging.getLogger(__name__)


# ---------- Helpers to declare the structure you provided ----------
# Names and topics can be tweaked safely.
SERVER_NAME = "The Vault"
STAFF_ROLE_NAME = "Vault Staff"  # created if missing

# Category → list[(channel_name, kind, topic, extra_perms_tag)]
# kind: "text" | "voice"
# extra_perms_tag (optional): simple hints we handle in code
STRUCTURE = {
    "🗝️ Entrance": [
        ("rules", "text", "Server rules. Read-only for everyone; staff can post.", "staff_write_only"),
        ("announcements", "text", "Official updates. Read-only for everyone; staff can post.", "staff_write_only"),
        ("server-guide", "text", "How to navigate roles, leveling, bots.", "staff_write_only"),
        ("self-roles", "text", "Reaction/self-assign roles.", None),
        ("welcome", "text", "Bot welcome messages.", None),
    ],
    "📢 The Vault Hub": [
        ("general-chat", "text", "Main chat for everyone.", None),
        ("introductions", "text", "Introduce yourself!", None),
        ("media-dump", "text", "Share memes, clips, videos.", None),
        ("bot-commands", "text", "Keep bot spam here.", None),
    ],
    "📖 Anime & Manga": [
        ("anime-discussion", "text", "General anime chat.", None),
        ("manga-discussion", "text", "Talk manga chapters.", None),
        ("seasonal-anime", "text", "Trending / new releases.", None),
        ("anime-battles", "text", "Who-would-win debates.", None),
        ("fanart-gallery", "text", "Share art/drawings/edits (images only).", None),
        ("theories-and-lore", "text", "Deep theories & predictions.", None),
    ],
    "🎮 Entertainment": [
        ("gaming-corner", "text", "Talk games (anime & general).", None),
        ("music-room", "text", "Share OP/EDs & playlists.", None),
        ("memes", "text", "Memes only.", None),
        ("off-topic", "text", "Non-anime topics.", None),
    ],
    "🎉 Events": [
        ("vault-events", "text", "Event announcements (staff posts, all can react).", "staff_write_only"),
        ("contests", "text", "Art/meme competitions & giveaways.", None),
        ("qotd", "text", "Question of the Day.", "staff_write_only"),
    ],
    "🔐 Staff Zone": [
        ("staff-chat", "text", "Staff coordination.", "staff_only"),
        ("staff-announcements", "text", "Owner/Admin posts only.", "staff_only"),
        ("mod-logs", "text", "Automated moderation logs.", "staff_only"),
        ("user-reports", "text", "User reports/complaints.", "staff_only"),
        ("ideas-and-planning", "text", "Future server ideas/events.", "staff_only"),
    ],
    "⚙️ Logs": [
        ("join-leave-log", "text", "Member join/leave tracking.", "staff_only"),
        ("message-log", "text", "Deleted/edited messages.", "staff_only"),
        ("mod-actions", "text", "Mute/kick/ban actions.", "staff_only"),
        ("voice-log", "text", "Voice join/leave events.", "staff_only"),
    ],
    "🔊 Voice Channels": [
        ("General VC", "voice", "General voice chat.", None),
        ("Anime Watch VC", "voice", "For synced watch nights.", None),
        ("Gaming VC", "voice", "Gaming voice chat.", None),
        ("Music VC", "voice", "Music listening.", None),
    ],
}


class VaultSetup(commands.Cog):
    """One-click server scaffold for **The Vault** (anime/manga theme)."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    # ---------- internal utilities ----------

    @staticmethod
    def _has_overwrite(ow: discord.PermissionOverwrite, **kwargs) -> bool:
        for k, v in kwargs.items():
            if getattr(ow, k, None) != v:
                return False
        return True

    async def _ensure_role(self, guild: discord.Guild, name: str, *, perms: discord.Permissions | None = None) -> discord.Role:
        role = discord.utils.get(guild.roles, name=name)
        if role:
            return role
        reason = "VaultSetup: create staff role"
        role = await guild.create_role(name=name, permissions=perms or discord.Permissions.none(), reason=reason)
        return role

    async def _ensure_category(self, guild: discord.Guild, name: str, *, overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite]) -> discord.CategoryChannel:
        cat = discord.utils.get(guild.categories, name=name)
        if cat:
            # Update existing overwrites minimally (doesn't nuke custom ones)
            try:
                await cat.edit(overwrites={**cat.overwrites, **overwrites}, reason="VaultSetup: sync perms")
            except Exception:
                pass
            return cat
        return await guild.create_category(name=name, overwrites=overwrites, reason="VaultSetup: create category")

    async def _ensure_text(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        name: str,
        topic: Optional[str],
        *,
        base_overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
        tag: Optional[str],
        staff_role: discord.Role,
    ) -> discord.TextChannel:
        chan = discord.utils.get(category.text_channels, name=name) or discord.utils.get(guild.text_channels, name=name, category=category)
        ow = dict(base_overwrites)
        if tag == "staff_write_only":
            # Everyone can view & read, but cannot send; staff can send.
            ow[guild.default_role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
            ow[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        elif tag == "staff_only":
            ow[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            ow[staff_role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True)
        # else: inherit category/base

        if chan:
            try:
                await chan.edit(topic=topic or discord.utils.MISSING, overwrites={**chan.overwrites, **ow}, reason="VaultSetup: sync perms/topic")
            except Exception:
                pass
            return chan

        return await guild.create_text_channel(
            name=name, topic=topic or discord.utils.MISSING, overwrites=ow or None, category=category, reason="VaultSetup: create channel"
        )

    async def _ensure_voice(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        name: str,
        topic: Optional[str],
        *,
        base_overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
        tag: Optional[str],
        staff_role: discord.Role,
    ) -> discord.VoiceChannel:
        chan = discord.utils.get(category.voice_channels, name=name) or discord.utils.get(guild.voice_channels, name=name, category=category)
        ow = dict(base_overwrites)
        if tag == "staff_only":
            ow[guild.default_role] = discord.PermissionOverwrite(view_channel=False, connect=False)
            ow[staff_role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, mute_members=True, move_members=True)

        if chan:
            try:
                await chan.edit(overwrites={**chan.overwrites, **ow}, reason="VaultSetup: sync voice perms")
            except Exception:
                pass
            return chan

        return await guild.create_voice_channel(name=name, overwrites=ow or None, category=category, reason="VaultSetup: create voice")

    # ---------- command ----------

    @commands.hybrid_command(name="vaultsetup")
    @checks.is_owner()  # Red owner only (works for prefix & slash)
    @commands.guild_only()
    async def vaultsetup_build(self, ctx: commands.Context, *, dryrun: Optional[bool] = False):
        """
        Build the full **The Vault** server structure (owner only).

        Parameters
        ----------
        dryrun: bool
            If True, shows what would be created/updated without making changes.
        """
        guild = ctx.guild
        assert guild is not None

        # Ensure staff role first
        staff_role = await self._ensure_role(guild, STAFF_ROLE_NAME)

        # Base overwrites per category type
        everyone = guild.default_role
        base_public = {
            everyone: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, manage_messages=True),
        }
        base_staff_hidden = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            staff_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True),
        }

        created: list[str] = []
        updated: list[str] = []

        # Walk the structure and create/sync
        for cat_name, channels in STRUCTURE.items():
            is_staff_only = cat_name in ("🔐 Staff Zone", "⚙️ Logs")
            cat_overwrites = base_staff_hidden if is_staff_only else base_public

            if dryrun:
                # Lookups only
                cat = discord.utils.get(guild.categories, name=cat_name)
            else:
                cat = await self._ensure_category(guild, cat_name, overwrites=cat_overwrites)

            if not cat:
                created.append(f"Category: {cat_name}")
                # create minimal in dry-run? skip
                continue

            for ch_name, kind, topic, tag in channels:
                if kind == "text":
                    if dryrun:
                        chan = discord.utils.get(cat.text_channels, name=ch_name) or discord.utils.get(guild.text_channels, name=ch_name, category=cat)
                        if chan:
                            updated.append(f"{cat_name} / #{ch_name} (sync perms/topic)")
                        else:
                            created.append(f"{cat_name} / #{ch_name}")
                        continue
                    chan = await self._ensure_text(
                        guild, cat, ch_name, topic, base_overwrites=cat_overwrites, tag=tag, staff_role=staff_role
                    )
                    if chan.created_at.timestamp() > (ctx.message.created_at.timestamp() if ctx.message else 0):
                        created.append(f"{cat_name} / #{ch_name}")
                    else:
                        updated.append(f"{cat_name} / #{ch_name}")
                else:
                    if dryrun:
                        chan = discord.utils.get(cat.voice_channels, name=ch_name) or discord.utils.get(guild.voice_channels, name=ch_name, category=cat)
                        if chan:
                            updated.append(f"{cat_name} / 🔊 {ch_name} (sync perms)")
                        else:
                            created.append(f"{cat_name} / 🔊 {ch_name}")
                        continue
                    v = await self._ensure_voice(
                        guild, cat, ch_name, topic, base_overwrites=cat_overwrites, tag=tag, staff_role=staff_role
                    )
                    if v.created_at.timestamp() > (ctx.message.created_at.timestamp() if ctx.message else 0):
                        created.append(f"{cat_name} / 🔊 {ch_name}")
                    else:
                        updated.append(f"{cat_name} / 🔊 {ch_name}")

        # Final summary
        title = f"🏗️ {SERVER_NAME} Setup " + ("(Dry Run)" if dryrun else "")
        desc_lines: list[str] = []
        if created:
            desc_lines.append(f"**Created ({len(created)}):**\n• " + "\n• ".join(created[:40]))
            if len(created) > 40:
                desc_lines.append(f"…and {len(created)-40} more")
        if updated:
            desc_lines.append(f"\n**Synced ({len(updated)}):**\n• " + "\n• ".join(updated[:40]))
            if len(updated) > 40:
                desc_lines.append(f"…and {len(updated)-40} more")
        if not created and not updated:
            desc_lines.append("Everything already matches the target structure ✅")

        embed = discord.Embed(title=title, description="\n".join(desc_lines) or "No changes.", color=discord.Color.blurple())
        embed.set_footer(text="Owner-only | Uses hybrid command (prefix & slash)")
        await (ctx.reply if getattr(ctx, "reply", None) else ctx.send)(embed=embed, mention_author=False)
