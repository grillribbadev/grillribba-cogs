from __future__ import annotations

import discord
from redbot.core import commands, checks
from redbot.core.bot import Red


SERVER_NAME = "The Vault"

ROLE_OWNER = "⚜️ Vault Overlord"   # Administrator
ROLE_ADMIN = "🛡️ Vault Shogun"    # Administrator
ROLE_MOD   = "🗡️ Vault Sentinels" # Mod powers

# category → [(name, kind, tag, topic)]
# kind: "text" | "voice"
# tag: None | "staff_write" | "staff_only"
STRUCTURE = {
    "🗝️ Entrance": [
        ("rules", "text", "staff_write", "Server rules (read-only to members)"),
        ("announcements", "text", "staff_write", "Official updates & news"),
        ("server-guide", "text", "staff_write", "How to navigate roles/bots/levels"),
        ("self-roles", "text", None, "Reaction/self-assign roles"),
        ("welcome", "text", None, "Bot welcomes"),
    ],
    "📢 The Vault Hub": [
        ("general-chat", "text", None, "Main chat"),
        ("introductions", "text", None, "Introduce yourself"),
        ("media-dump", "text", None, "Clips/memes/videos"),
        ("bot-commands", "text", None, "Bot spam lives here"),
    ],
    "📖 Anime & Manga": [
        ("anime-discussion", "text", None, None),
        ("manga-discussion", "text", None, None),
        ("seasonal-anime", "text", None, None),
        ("anime-battles", "text", None, None),
        ("fanart-gallery", "text", None, "Images/art/edits"),
        ("theories-and-lore", "text", None, None),
    ],
    "🎮 Entertainment": [
        ("gaming-corner", "text", None, None),
        ("music-room", "text", None, None),
        ("memes", "text", None, None),
        ("off-topic", "text", None, None),
    ],
    "🎉 Events": [
        ("vault-events", "text", "staff_write", "Event announcements"),
        ("contests", "text", None, "Art/meme comps & giveaways"),
        ("qotd", "text", "staff_write", "Question of the Day"),
    ],
    "🔐 Staff Zone": [
        ("staff-chat", "text", "staff_only", "Staff coordination"),
        ("staff-announcements", "text", "staff_only", "Owner/Admin posts"),
        ("mod-logs", "text", "staff_only", "Auto moderation logs"),
        ("user-reports", "text", "staff_only", "Reports/complaints"),
        ("ideas-and-planning", "text", "staff_only", "Future plans"),
    ],
    "⚙️ Logs": [
        ("join-leave-log", "text", "staff_only", None),
        ("message-log", "text", "staff_only", None),
        ("mod-actions", "text", "staff_only", None),
        ("voice-log", "text", "staff_only", None),
    ],
    "🔊 Voice Channels": [
        ("General VC", "voice", None, "General voice chat"),
        ("Anime Watch VC", "voice", None, "Watch parties"),
        ("Gaming VC", "voice", None, "Gaming voice"),
        ("Music VC", "voice", None, "Music listening"),
    ],
}


class VaultSetup(commands.Cog):
    """Wipe & rebuild (or create-only) the anime-themed **The Vault** layout and ranks."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    # ---------- roles ----------

    async def _ensure_role(
        self, guild: discord.Guild, name: str, *, perms: discord.Permissions, color: discord.Color
    ) -> discord.Role:
        role = discord.utils.get(guild.roles, name=name)
        if role:
            try:
                await role.edit(permissions=perms, colour=color, reason="VaultSetup: ensure role")
            except Exception:
                pass
            return role
        return await guild.create_role(name=name, permissions=perms, colour=color, reason="VaultSetup: create role")

    async def _ensure_ranks(self, guild: discord.Guild) -> tuple[discord.Role, discord.Role, discord.Role]:
        admin_perms = discord.Permissions(administrator=True)
        mod_perms = discord.Permissions(
            manage_messages=True,
            kick_members=True,
            ban_members=True,
            manage_channels=True,
            move_members=True,
            mute_members=True,        # legacy flag (ignored by Discord if unused)
            moderate_members=True,    # correct flag for timeouts
        )
        owner = await self._ensure_role(guild, ROLE_OWNER, perms=admin_perms, color=discord.Color.gold())
        admin = await self._ensure_role(guild, ROLE_ADMIN, perms=admin_perms, color=discord.Color.purple())
        mod = await self._ensure_role(guild, ROLE_MOD, perms=mod_perms, color=discord.Color.teal())
        # Try to keep them under the bot’s top role (best effort)
        try:
            me_top = guild.me.top_role.position
            await owner.edit(position=max(1, me_top - 1))
            await admin.edit(position=max(1, me_top - 2))
            await mod.edit(position=max(1, me_top - 3))
        except Exception:
            pass
        return owner, admin, mod

    # ---------- perms ----------

    def _base_overwrites(self, guild: discord.Guild, owner: discord.Role, admin: discord.Role, mod: discord.Role):
        everyone = guild.default_role
        staff_write = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True, manage_messages=True
        )
        base_public = {
            everyone: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
            owner: staff_write,
            admin: staff_write,
            mod: staff_write,
        }
        base_hidden = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            owner: staff_write,
            admin: staff_write,
            mod: staff_write,
        }
        return base_public, base_hidden

    def _apply_tag(
        self,
        guild: discord.Guild,
        base: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
        tag: str | None,
    ) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
        ow = dict(base)
        everyone = guild.default_role
        if tag == "staff_write":
            ow[everyone] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
        elif tag == "staff_only":
            ow[everyone] = discord.PermissionOverwrite(view_channel=False)
        return ow

    # ---------- delete helpers ----------

    async def _wipe_all_channels(self, guild: discord.Guild, *, keep: set[int] = set()):
        """Delete every channel and category not in keep. Ignores 404/permission issues."""
        # delete text/voice first
        for ch in list(guild.channels):
            if isinstance(ch, discord.CategoryChannel):
                continue
            if ch.id in keep:
                continue
            try:
                await ch.delete(reason="VaultSetup: wipe")
            except Exception:
                # Community/system channels can error with 404/permissions; just skip
                pass
        # then categories
        for cat in list(guild.categories):
            if cat.id in keep:
                continue
            try:
                await cat.delete(reason="VaultSetup: wipe")
            except Exception:
                pass

    # ---------- build helpers ----------

    async def _create_category(self, guild: discord.Guild, name: str, overwrites: dict) -> discord.CategoryChannel:
        return await guild.create_category(name=name, overwrites=overwrites, reason="VaultSetup: category")

    async def _create_text(
        self, guild: discord.Guild, category: discord.CategoryChannel, name: str, topic: str | None, overwrites: dict
    ) -> discord.TextChannel:
        return await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites, topic=topic or discord.utils.MISSING, reason="VaultSetup: text"
        )

    async def _create_voice(
        self, guild: discord.Guild, category: discord.CategoryChannel, name: str, overwrites: dict, topic: str | None
    ) -> discord.VoiceChannel:
        # correct parameter order: name, category, overwrites (dict)
        return await guild.create_voice_channel(
            name=name, category=category, overwrites=overwrites, reason="VaultSetup: voice"
        )

    # ---------- command ----------

    @commands.hybrid_command(name="vaultsetup")
    @checks.is_owner()
    @commands.guild_only()
    async def vaultsetup(self, ctx: commands.Context, *, mode: str = "wipe"):
        """
        Build **The Vault**.

        mode:
          • wipe (default) – delete all channels/categories first, then rebuild + ranks.
          • create         – create layout + ranks alongside existing channels (no deletions).
        """
        guild = ctx.guild
        assert guild is not None

        me = guild.me
        if not (me.guild_permissions.manage_channels and me.guild_permissions.manage_roles):
            return await ctx.reply("I need **Manage Channels** and **Manage Roles**.", mention_author=False)

        # Ensure ranks first
        owner_r, admin_r, mod_r = await self._ensure_ranks(guild)
        base_public, base_hidden = self._base_overwrites(guild, owner_r, admin_r, mod_r)

        destructive = str(mode).lower() == "wipe"

        created_ids: list[int] = []
        created_list: list[str] = []

        # Build desired structure to new objects (optionally wipe first)
        if destructive:
            # Keep the invoking channel alive until we can post a summary elsewhere
            current_id = ctx.channel.id if isinstance(ctx.channel, discord.abc.GuildChannel) else None
            keep = {current_id} if current_id else set()
            await self._wipe_all_channels(guild, keep=keep)

        # Create categories + channels
        for cat_name, channels in STRUCTURE.items():
            is_staff = cat_name in ("🔐 Staff Zone", "⚙️ Logs")
            cat_ow = base_hidden if is_staff else base_public
            cat = await self._create_category(guild, cat_name, cat_ow)
            created_ids.append(cat.id)
            created_list.append(f"Category: {cat_name}")

            for ch_name, kind, tag, topic in channels:
                overwrites = self._apply_tag(guild, cat_ow, tag)
                if kind == "text":
                    ch = await self._create_text(guild, cat, ch_name, topic, overwrites)
                    prefix = "#"
                else:
                    # FIX: correct call order + proper overwrites dict
                    ch = await self._create_voice(guild, cat, ch_name, overwrites, topic)
                    prefix = "🔊"
                created_ids.append(ch.id)
                created_list.append(f"{cat_name} / {prefix} {ch_name}")

        # If we kept the invoking channel during wipe and it wasn't recreated, try to delete it now
        if destructive:
            try:
                if ctx.channel.id not in created_ids:
                    await ctx.channel.delete(reason="VaultSetup: removed invoking channel after rebuild")
            except Exception:
                pass

        # Announce results
        target = discord.utils.get(guild.text_channels, name="announcements") or \
                 next((c for c in guild.text_channels if c.category and "🗝️ Entrance" in c.category.name), None) or \
                 guild.system_channel

        msg = (
            f"🏗️ **{SERVER_NAME}** setup — **{'WIPE & REBUILD' if destructive else 'CREATE-ONLY'}**\n"
            f"Created **{len(created_list)}** items.\n"
            + "• " + "\n• ".join(created_list[:35])
            + (f"\n…and {len(created_list) - 35} more" if len(created_list) > 35 else "")
            + f"\n\nRanks: {owner_r.mention} • {admin_r.mention} • {mod_r.mention}"
        )

        try:
            await ctx.reply(msg, mention_author=False)
        except Exception:
            if target:
                try:
                    await target.send(msg)
                except Exception:
                    pass
