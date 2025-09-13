from __future__ import annotations

import logging
from typing import Optional

import discord
from redbot.core import commands, checks
from redbot.core.bot import Red

log = logging.getLogger(__name__)

# ====== Themed server + roles ======
SERVER_NAME = "The Vault"

ROLE_OWNER = "⚜️ Vault Overlord"   # Administrator
ROLE_ADMIN = "🛡️ Vault Shogun"    # Administrator
ROLE_MOD   = "🗡️ Vault Sentinels" # Powerful moderation

# Category → list[(name, kind, topic, tag)]
# kind: "text" | "voice"
# tag:
#   None            = inherit category perms
#   "staff_write"   = everyone can read, only staff writes
#   "staff_only"    = only staff can view
STRUCTURE = {
    "🗝️ Entrance": [
        ("rules", "text", "Server rules. Read-only; staff can post.", "staff_write"),
        ("announcements", "text", "Official updates & news.", "staff_write"),
        ("server-guide", "text", "How to navigate The Vault (roles, bots, leveling).", "staff_write"),
        ("self-roles", "text", "Reaction/self-assign roles.", None),
        ("welcome", "text", "Bot welcomes new members.", None),
    ],
    "📢 The Vault Hub": [
        ("general-chat", "text", "Main chat for everyone.", None),
        ("introductions", "text", "Introduce yourself!", None),
        ("media-dump", "text", "Share memes, clips, videos.", None),
        ("bot-commands", "text", "Keep bot spam here.", None),
    ],
    "📖 Anime & Manga": [
        ("anime-discussion", "text", "General anime chat.", None),
        ("manga-discussion", "text", "Talk about manga chapters.", None),
        ("seasonal-anime", "text", "Trending / new releases.", None),
        ("anime-battles", "text", "Who-would-win debates.", None),
        ("fanart-gallery", "text", "Images/art/edits.", None),
        ("theories-and-lore", "text", "Deep theories & predictions.", None),
    ],
    "🎮 Entertainment": [
        ("gaming-corner", "text", "Games chat (anime & general).", None),
        ("music-room", "text", "OP/EDs & playlists.", None),
        ("memes", "text", "Memes only.", None),
        ("off-topic", "text", "Non-anime topics.", None),
    ],
    "🎉 Events": [
        ("vault-events", "text", "Event announcements (staff posts, all react).", "staff_write"),
        ("contests", "text", "Art/meme competitions & giveaways.", None),
        ("qotd", "text", "Question of the day.", "staff_write"),
    ],
    "🔐 Staff Zone": [
        ("staff-chat", "text", "Staff coordination.", "staff_only"),
        ("staff-announcements", "text", "Owner/Admin posts only.", "staff_only"),
        ("mod-logs", "text", "Automated moderation logs.", "staff_only"),
        ("user-reports", "text", "Reports & complaints.", "staff_only"),
        ("ideas-and-planning", "text", "Future ideas/events.", "staff_only"),
    ],
    "⚙️ Logs": [
        ("join-leave-log", "text", "Member join/leave tracking.", "staff_only"),
        ("message-log", "text", "Deleted/edited messages.", "staff_only"),
        ("mod-actions", "text", "Mute/kick/ban actions.", "staff_only"),
        ("voice-log", "text", "Voice join/leave events.", "staff_only"),
    ],
    "🔊 Voice Channels": [
        ("General VC", "voice", "General voice chat.", None),
        ("Anime Watch VC", "voice", "Synced watch nights.", None),
        ("Gaming VC", "voice", "Gaming voice.", None),
        ("Music VC", "voice", "Music listening.", None),
    ],
}


class VaultSetup(commands.Cog):
    """Wipe & rebuild **The Vault** anime-themed server. Also creates themed ranks."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    # ---------- role helpers ----------

    async def _ensure_role(
        self, guild: discord.Guild, name: str, *, perms: discord.Permissions, color: Optional[discord.Color] = None
    ) -> discord.Role:
        role = discord.utils.get(guild.roles, name=name)
        if role:
            # keep/upgrade perms & color if we can
            try:
                await role.edit(permissions=perms, colour=color or role.colour, reason="VaultSetup: ensure role")
            except Exception:
                pass
            return role
        return await guild.create_role(name=name, permissions=perms, colour=color, reason="VaultSetup: create role")

    async def _bump_under_bot(self, guild: discord.Guild, *roles: discord.Role):
        """Place roles just under the bot's top role (best effort)."""
        me_top = guild.me.top_role.position
        # clamp to avoid > top
        # Newest in list ends up lowest of the group
        target_positions = [max(1, me_top - i - 1) for i in range(len(roles))]
        for role, pos in zip(roles, target_positions):
            try:
                await role.edit(position=pos, reason="VaultSetup: position ranks under bot")
            except Exception:
                pass

    async def _ensure_ranks(self, guild: discord.Guild) -> tuple[discord.Role, discord.Role, discord.Role]:
        admin_perms = discord.Permissions(administrator=True)
        mod_perms = discord.Permissions(
            manage_messages=True,
            kick_members=True,
            ban_members=True,
            moderate_members=True,   # timeouts
            manage_channels=True,
            move_members=True,
            mute_members=True,       # legacy flag still present in API
        )
        color_owner = discord.Color.gold()
        color_admin = discord.Color.purple()
        color_mod = discord.Color.teal()

        owner = await self._ensure_role(guild, ROLE_OWNER, perms=admin_perms, color=color_owner)
        admin = await self._ensure_role(guild, ROLE_ADMIN, perms=admin_perms, color=color_admin)
        mod = await self._ensure_role(guild, ROLE_MOD, perms=mod_perms, color=color_mod)

        await self._bump_under_bot(guild, owner, admin, mod)
        return owner, admin, mod

    # ---------- permission builders ----------

    def _base_overwrites(self, guild: discord.Guild, owner: discord.Role, admin: discord.Role, mod: discord.Role):
        everyone = guild.default_role
        staff_view = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True, manage_messages=True
        )
        base_public = {
            everyone: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
            owner: staff_view,
            admin: staff_view,
            mod: staff_view,
        }
        base_staff_hidden = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            owner: staff_view,
            admin: staff_view,
            mod: staff_view,
        }
        return base_public, base_staff_hidden

    def _channel_overwrites_for_tag(
        self,
        guild: discord.Guild,
        owner: discord.Role,
        admin: discord.Role,
        mod: discord.Role,
        base: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
        tag: Optional[str],
    ):
        ow = dict(base)
        everyone = guild.default_role
        if tag == "staff_write":
            ow[everyone] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
        elif tag == "staff_only":
            ow[everyone] = discord.PermissionOverwrite(view_channel=False)
        return ow

    # ---------- channel helpers ----------

    async def _create_category(
        self,
        guild: discord.Guild,
        name: str,
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    ) -> discord.CategoryChannel:
        return await guild.create_category(name=name, overwrites=overwrites, reason="VaultSetup: build category")

    async def _create_text(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        name: str,
        topic: Optional[str],
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    ) -> discord.TextChannel:
        return await guild.create_text_channel(
            name=name,
            topic=topic or discord.utils.MISSING,
            category=category,
            overwrites=overwrites or None,
            reason="VaultSetup: build text",
        )

    async def _create_voice(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        name: str,
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    ) -> discord.VoiceChannel:
        return await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=overwrites or None,
            reason="VaultSetup: build voice",
        )

    # ---------- destructive wipe helpers ----------

    async def _wipe_all_channels(self, guild: discord.Guild, *, keep_ids: set[int]):
        """Delete every channel & category not in keep_ids. Skip current text channel until end."""
        # delete non-category channels first
        for ch in list(guild.channels):
            if isinstance(ch, discord.CategoryChannel):
                continue
            if ch.id in keep_ids:
                continue
            try:
                await ch.delete(reason="VaultSetup: wipe")
            except Exception:
                pass

        # then categories (should be empty now)
        for cat in list(guild.categories):
            if cat.id in keep_ids:
                continue
            try:
                await cat.delete(reason="VaultSetup: wipe")
            except Exception:
                pass

    # ---------- command ----------

    @commands.hybrid_command(name="vaultsetup")
    @checks.is_owner()  # bot owner only
    @commands.guild_only()
    async def vaultsetup(self, ctx: commands.Context, *, mode: str = "wipe"):
        """
        Build **The Vault** (owner only).

        mode:
          • wipe (default) – **Delete EVERYTHING**, then rebuild the anime layout + ranks.
          • create         – Create layout + ranks alongside existing channels (non-destructive).
        """
        guild = ctx.guild
        assert guild

        # basic permission sanity
        me = guild.me
        if not (me.guild_permissions.manage_channels and me.guild_permissions.manage_roles):
            return await ctx.reply(
                "I need **Manage Channels** and **Manage Roles** to do this.",
                mention_author=False,
            )

        # Create ranks first (and push near top)
        owner_r, admin_r, mod_r = await self._ensure_ranks(guild)

        # Build the new structure *first* into a keep-list (so we can safely wipe afterward).
        base_public, base_hidden = self._base_overwrites(guild, owner_r, admin_r, mod_r)

        created_ids: set[int] = set()
        created_names: list[str] = []

        for cat_name, channels in STRUCTURE.items():
            is_staff_only = cat_name in ("🔐 Staff Zone", "⚙️ Logs")
            cat_ow = base_hidden if is_staff_only else base_public
            cat = await self._create_category(guild, cat_name, cat_ow)
            created_ids.add(cat.id)
            created_names.append(f"Category: {cat_name}")

            for ch_name, kind, topic, tag in channels:
                ch_ow = self._channel_overwrites_for_tag(guild, owner_r, admin_r, mod_r, cat_ow, tag)
                if kind == "text":
                    ch = await self._create_text(guild, cat, ch_name, topic, ch_ow)
                else:
                    ch = await self._create_voice(guild, cat, ch_ow, ch_name)  # type: ignore[arg-type]
                created_ids.add(ch.id)
                prefix = "#" if kind == "text" else "🔊"
                created_names.append(f"{cat_name} / {prefix} {ch_name}")

        # Wipe if requested
        destructive = str(mode).lower() == "wipe"
        if destructive:
            # avoid deleting the channel we’re currently in until the very end
            current_id = getattr(ctx.channel, "id", None)
            safe_keep = set(created_ids)
            # keep current until last (if it still exists)
            if current_id is not None:
                safe_keep.add(current_id)
            await self._wipe_all_channels(guild, keep_ids=safe_keep)

            # finally remove the current channel if it wasn't part of the new layout
            if current_id and current_id not in created_ids:
                try:
                    ch = guild.get_channel(current_id)
                    if ch:
                        await ch.delete(reason="VaultSetup: final wipe of invoking channel")
                except Exception:
                    pass

        # Pick a place to report results (prefer #announcements or first new channel)
        announce = discord.utils.get(guild.text_channels, name="announcements")
        target_chan = announce or next((guild.get_channel(cid) for cid in created_ids if isinstance(guild.get_channel(cid), discord.TextChannel)), None)

        # Summary embed
        title = f"🏗️ {SERVER_NAME} Setup — {'WIPE & REBUILD' if destructive else 'CREATE-ONLY'}"
        desc_lines = [f"**Created {len(created_names)} items**", "• " + "\n• ".join(created_names[:35])]
        if len(created_names) > 35:
            desc_lines.append(f"…and {len(created_names)-35} more")

        embed = discord.Embed(title=title, description="\n".join(desc_lines), color=discord.Color.blurple())
        embed.add_field(name="Ranks", value=f"{owner_r.mention} • {admin_r.mention} • {mod_r.mention}", inline=False)
        embed.set_footer(text="Owner-only • Hybrid command")

        # If we just deleted the invoking channel, try to message in a new one.
        try:
            await ctx.reply(embed=embed, mention_author=False)
        except Exception:
            if isinstance(target_chan, discord.TextChannel):
                try:
                    await target_chan.send(embed=embed)
                except Exception:
                    pass
