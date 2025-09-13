from __future__ import annotations

import logging
from typing import Optional

import discord
from redbot.core import commands, checks
from redbot.core.bot import Red

log = logging.getLogger(__name__)

# ====== Themed names ======
SERVER_NAME = "The Vault"

# Themed rank roles (created if missing)
ROLE_OWNER = "⚜️ Vault Overlord"     # full admin
ROLE_ADMIN = "🛡️ Vault Shogun"      # full admin
ROLE_MOD   = "🗡️ Vault Sentinels"   # moderator set

# Category → list[(name, kind, topic, tag)]
# kind: "text" | "voice"
# tag controls extra perms:
#   None            = inherit category
#   "staff_write"   = everyone can read, only staff can send
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
    """One-click server scaffold for **The Vault** (non-destructive, themed ranks)."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    # ---------- role helpers ----------

    async def _ensure_role(
        self,
        guild: discord.Guild,
        name: str,
        perms: discord.Permissions,
    ) -> discord.Role:
        role = discord.utils.get(guild.roles, name=name)
        if role:
            return role
        # Create role (no position juggling to avoid permission errors)
        return await guild.create_role(name=name, permissions=perms, reason="VaultSetup: create rank")

    async def _ensure_ranks(self, guild: discord.Guild) -> tuple[discord.Role, discord.Role, discord.Role]:
        # Admin roles (Owner/Admin) with administrator=True
        admin_perms = discord.Permissions(administrator=True)
        mod_perms = discord.Permissions(
            manage_messages=True,
            kick_members=True,
            mute_members=True,      # legacy flag (still present); see also moderate_members for timeouts
            moderate_members=True,  # timeout
            manage_channels=True,
            move_members=True,
            ban_members=False,
        )
        owner = await self._ensure_role(guild, ROLE_OWNER, admin_perms)
        admin = await self._ensure_role(guild, ROLE_ADMIN, admin_perms)
        mod = await self._ensure_role(guild, ROLE_MOD, mod_perms)
        return owner, admin, mod

    # ---------- channel helpers (create-only by default) ----------

    async def _create_category_if_missing(
        self,
        guild: discord.Guild,
        name: str,
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    ) -> discord.CategoryChannel:
        cat = discord.utils.get(guild.categories, name=name)
        if cat:
            return cat
        return await guild.create_category(name=name, overwrites=overwrites, reason="VaultSetup: create category")

    async def _ensure_text(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        name: str,
        topic: Optional[str],
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
        *,
        sync_existing: bool = False,
    ) -> discord.TextChannel:
        chan = discord.utils.get(category.text_channels, name=name) or discord.utils.get(guild.text_channels, name=name, category=category)
        if chan:
            if sync_existing:
                # merge overwrites (do NOT replace) to avoid nuking custom perms
                try:
                    chan_ow = dict(chan.overwrites)
                    chan_ow.update(overwrites)
                    await chan.edit(topic=topic or discord.utils.MISSING, overwrites=chan_ow, reason="VaultSetup: sync perms/topic")
                except Exception:
                    pass
            return chan
        return await guild.create_text_channel(
            name=name, topic=topic or discord.utils.MISSING, category=category, overwrites=overwrites or None, reason="VaultSetup: create text"
        )

    async def _ensure_voice(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        name: str,
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
        *,
        sync_existing: bool = False,
    ) -> discord.VoiceChannel:
        chan = discord.utils.get(category.voice_channels, name=name) or discord.utils.get(guild.voice_channels, name=name, category=category)
        if chan:
            if sync_existing:
                try:
                    ow = dict(chan.overwrites)
                    ow.update(overwrites)
                    await chan.edit(overwrites=ow, reason="VaultSetup: sync voice perms")
                except Exception:
                    pass
            return chan
        return await guild.create_voice_channel(name=name, category=category, overwrites=overwrites or None, reason="VaultSetup: create voice")

    # ---------- permission builders ----------

    def _base_overwrites(self, guild: discord.Guild, owner: discord.Role, admin: discord.Role, mod: discord.Role):
        everyone = guild.default_role
        staff_view = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True)
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
            # everyone reads, staff writes
            ow[everyone] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
        elif tag == "staff_only":
            ow[everyone] = discord.PermissionOverwrite(view_channel=False)
        return ow

    # ---------- command ----------

    @commands.hybrid_command(name="vaultsetup")
    @checks.is_owner()  # bot owner only
    @commands.guild_only()
    async def vaultsetup_build(self, ctx: commands.Context, *, mode: str = "create"):
        """
        Build **The Vault** structure (owner only).

        mode:
          • create (default) – **create-only**, never edits existing channels/categories.
          • sync            – create missing, and lightly **merge** overwrites on channels created by/inside these categories.
        """
        guild = ctx.guild
        assert guild

        sync_existing = str(mode).lower() == "sync"

        # 1) Ensure ranks (non-destructive)
        owner_r, admin_r, mod_r = await self._ensure_ranks(guild)

        # 2) Base category overwrites
        base_public, base_hidden = self._base_overwrites(guild, owner_r, admin_r, mod_r)

        created: list[str] = []
        touched: list[str] = []

        # 3) Walk and create categories/channels
        for cat_name, channels in STRUCTURE.items():
            is_staff_only = cat_name in ("🔐 Staff Zone", "⚙️ Logs")
            cat_ow = base_hidden if is_staff_only else base_public

            # Non-destructive: if category exists, we don't edit overwrites unless sync_existing=True
            cat = discord.utils.get(guild.categories, name=cat_name)
            if not cat:
                cat = await self._create_category_if_missing(guild, cat_name, cat_ow)
                created.append(f"Category: {cat_name}")
            elif sync_existing:
                try:
                    ow = dict(cat.overwrites)
                    ow.update(cat_ow)  # merge only for our rank targets/default role
                    await cat.edit(overwrites=ow, reason="VaultSetup: sync category perms")
                    touched.append(f"{cat_name} (perms synced)")
                except Exception:
                    pass

            # Channels
            for ch_name, kind, topic, tag in channels:
                ch_ow = self._channel_overwrites_for_tag(guild, owner_r, admin_r, mod_r, cat_ow, tag)
                if kind == "text":
                    ch = await self._ensure_text(guild, cat, ch_name, topic, ch_ow, sync_existing=sync_existing)
                    if ch.category == cat and ch.name == ch_name and ch.created_at:
                        # best-effort log
                        if ch.created_at.timestamp() > (ctx.created_at.timestamp() if hasattr(ctx, "created_at") else 0):
                            created.append(f"{cat_name} / #{ch_name}")
                        else:
                            touched.append(f"{cat_name} / #{ch_name}")
                else:
                    v = await self._ensure_voice(guild, cat, ch_name, ch_ow, sync_existing=sync_existing)
                    if v.category == cat and v.name == ch_name and v.created_at:
                        if v.created_at.timestamp() > (ctx.created_at.timestamp() if hasattr(ctx, "created_at") else 0):
                            created.append(f"{cat_name} / 🔊 {ch_name}")
                        else:
                            touched.append(f"{cat_name} / 🔊 {ch_name}")

        # 4) Summary
        desc = []
        if created:
            desc.append(f"**Created ({len(created)}):**\n• " + "\n• ".join(created[:30]))
            if len(created) > 30:
                desc.append(f"…and {len(created)-30} more")
        if touched:
            desc.append(f"\n**Synced ({len(touched)}):**\n• " + "\n• ".join(touched[:30]))
            if len(touched) > 30:
                desc.append(f"…and {len(touched)-30} more")
        if not desc:
            desc = ["Everything already matches the target layout ✅"]

        embed = discord.Embed(
            title=f"🏗️ {SERVER_NAME} Setup — {'SYNC' if sync_existing else 'CREATE-ONLY'}",
            description="\n".join(desc),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Non-destructive • Owner-only • Hybrid command")
        await ctx.reply(embed=embed, mention_author=False)
