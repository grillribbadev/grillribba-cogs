from __future__ import annotations
import discord
from redbot.core import commands, checks
from redbot.core.bot import Red

class VaultSetup(commands.Cog):
    """One-click setup for The Vault (Anime Hub)."""

    def __init__(self, bot: Red):
        self.bot = bot

    @commands.guild_only()
    @checks.is_owner()
    @commands.command(name="vaultsetup")
    async def vaultsetup(self, ctx: commands.Context):
        """Owner-only: Nuke current server structure and build The Vault layout."""

        guild: discord.Guild = ctx.guild

        # Step 1: Delete all existing channels/categories
        for channel in guild.channels:
            try:
                await channel.delete(reason="Vault setup reset")
            except Exception:
                pass

        # Step 2: Create roles (Owner, Admin, Moderator, Member)
        role_defs = [
            {"name": "Vault Owner", "color": discord.Color.gold(), "perms": discord.Permissions.all()},
            {"name": "Vault Admin", "color": discord.Color.red(), "perms": discord.Permissions(administrator=True)},
            {"name": "Vault Moderator", "color": discord.Color.blue(), "perms": discord.Permissions(kick_members=True, ban_members=True, manage_messages=True, mute_members=True, timeout_members=True)},
            {"name": "Vault Member", "color": discord.Color.green(), "perms": discord.Permissions(send_messages=True, read_messages=True)},
        ]

        created_roles = {}
        for rd in role_defs:
            role = discord.utils.get(guild.roles, name=rd["name"])
            if not role:
                role = await guild.create_role(name=rd["name"], colour=rd["color"], permissions=rd["perms"], reason="Vault setup roles")
            created_roles[rd["name"]] = role

        # Step 3: Category + channels definition
        layout = {
            "🗝️ Entrance": [
                ("rules", True), ("announcements", True), ("server-guide", True),
                ("self-roles", False), ("welcome", False)
            ],
            "📢 The Vault Hub": [
                ("general-chat", False), ("introductions", False),
                ("media-dump", False), ("bot-commands", False)
            ],
            "📖 Anime & Manga": [
                ("anime-discussion", False), ("manga-discussion", False),
                ("seasonal-anime", False), ("anime-battles", False),
                ("fanart-gallery", False), ("theories-and-lore", False)
            ],
            "🎮 Entertainment": [
                ("gaming-corner", False), ("music-room", False),
                ("memes", False), ("off-topic", False)
            ],
            "🎉 Events": [
                ("vault-events", True), ("contests", False), ("qotd", True)
            ],
            "🔐 Staff Zone": [
                ("staff-chat", False), ("staff-announcements", True),
                ("mod-logs", False), ("user-reports", False), ("ideas-and-planning", False)
            ],
            "⚙️ Logs": [
                ("join-leave-log", False), ("message-log", False),
                ("mod-actions", False), ("voice-log", False)
            ],
            "🔊 Voice Channels": [
                ("General VC", False, "voice"), ("Anime Watch VC", False, "voice"),
                ("Gaming VC", False, "voice"), ("Music VC", False, "voice")
            ]
        }

        # Step 4: Create categories & channels with perms
        for cat_name, chans in layout.items():
            category = await guild.create_category(cat_name, reason="Vault setup")
            for ch in chans:
                is_readonly = ch[1]
                is_voice = len(ch) > 2 and ch[2] == "voice"
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=not is_readonly),
                    created_roles["Vault Moderator"]: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True),
                    created_roles["Vault Admin"]: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True),
                    created_roles["Vault Owner"]: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True),
                }

                if cat_name in ["🔐 Staff Zone", "⚙️ Logs"]:
                    overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=False)

                if is_voice:
                    await guild.create_voice_channel(ch[0], category=category, overwrites=overwrites, reason="Vault setup")
                else:
                    await guild.create_text_channel(ch[0], category=category, overwrites=overwrites, reason="Vault setup")

        await ctx.send("✅ **The Vault has been set up!** Roles, channels, and categories are ready.")
