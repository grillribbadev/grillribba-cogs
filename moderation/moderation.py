import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime
import asyncio

class Moderation(commands.Cog):
    """Moderation cog with mute, ban, kick, and warning features."""

    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847326529, force_registration=True)
        default_guild = {
            "mute_role": None,
            "warnings": {}  # {user_id: [timestamp, reason]}
        }
        self.config.register_guild(**default_guild)

    # ---------- Role helpers ----------

    async def _apply_mute_overwrites(self, guild: discord.Guild, role: discord.Role) -> int:
        """
        Ensure basic mute overwrites are set everywhere for the given role.
        Returns number of channels updated.
        """
        updated = 0
        # Text-like: block sending & reactions (also threads)
        text_kwargs = dict(
            send_messages=False,
            add_reactions=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
        )
        # Voice-like: block speaking (leave connect alone so mods can move users, etc.)
        voice_kwargs = dict(
            speak=False,
            stream=False,
            request_to_speak=False,
        )

        for ch in guild.channels:
            try:
                if isinstance(ch, (discord.TextChannel, discord.ForumChannel, discord.CategoryChannel)):
                    # For categories, these act as defaults for children
                    await ch.set_permissions(role, reason="muteconfig: text perms", **text_kwargs)
                    updated += 1
                elif isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    await ch.set_permissions(role, reason="muteconfig: voice perms", **voice_kwargs)
                    updated += 1
                else:
                    # Threads inherit from parents; still try text perms just in case
                    await ch.set_permissions(role, reason="muteconfig: misc perms", **text_kwargs)
                    updated += 1
            except discord.Forbidden:
                # Missing perms on some channel; skip gracefully
                continue
            except Exception:
                # Any other channel-type specific oddity; keep going
                continue
        return updated

    async def _ensure_mute_role(self, guild: discord.Guild, *, role: discord.Role | None = None) -> discord.Role:
        """
        Ensure a mute role exists, is stored in Config, and has proper overwrites.
        If `role` is provided, that role is used; otherwise we find/create "Muted".
        """
        # If explicit role supplied, use it.
        if role:
            await self.config.guild(guild).mute_role.set(role.id)
            await self._apply_mute_overwrites(guild, role)
            return role

        # Else resolve from config or by name
        rid = await self.config.guild(guild).mute_role()
        mute_role = guild.get_role(rid) if rid else None

        if not mute_role:
            # Try to find an existing role named "Muted"
            mute_role = discord.utils.get(guild.roles, name="Muted")

        if not mute_role:
            # Create new
            mute_role = await guild.create_role(
                name="Muted",
                reason="muteconfig: create mute role",
                colour=discord.Color.dark_grey(),
                hoist=False,
                mentionable=False,
            )
            await self.config.guild(guild).mute_role.set(mute_role.id)
        else:
            # Store what we found
            await self.config.guild(guild).mute_role.set(mute_role.id)

        # Make sure overwrites are applied everywhere
        await self._apply_mute_overwrites(guild, mute_role)
        return mute_role

    async def get_or_create_mute_role(self, guild: discord.Guild) -> discord.Role:
        """Public helper used by mute/unmute; ensures role & overwrites exist."""
        return await self._ensure_mute_role(guild)

    # ---------- Configuration command ----------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    @commands.hybrid_command(name="muteconfig")
    async def muteconfig(self, ctx: commands.Context, role: discord.Role | None = None):
        """
        Create/repair the muted role and apply proper permissions everywhere.
        - With no argument: creates/uses a role named **Muted**.
        - With a role: uses that role as the mute role.
        """
        try:
            mute_role = await self._ensure_mute_role(ctx.guild, role=role)
        except discord.Forbidden:
            return await ctx.reply("🚫 I need **Manage Roles** and permission to manage channel overwrites.")
        count = await self._apply_mute_overwrites(ctx.guild, mute_role)
        await ctx.reply(f"✅ Mute role is **{mute_role.name}**. Permissions refreshed on **{count}** channels.")

    # ---------- Moderation commands ----------

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.command(name="mute")
    async def mute_user(self, ctx: commands.Context, member: discord.Member, duration: str = None, *, reason: str = "No reason provided"):
        """Mute a member with an optional duration (e.g. 10m, 2h, 1d)."""
        mute_role = await self.get_or_create_mute_role(ctx.guild)
        try:
            await member.add_roles(mute_role, reason=reason)
        except discord.Forbidden:
            return await ctx.send("🚫 I don't have permission to assign the mute role.")
        await ctx.send(f"🔇 {member.mention} has been muted. Reason: {reason}")

        if duration:
            seconds = self.parse_duration(duration)
            if seconds is None:
                return await ctx.send("❌ Invalid duration format. Use `10m`, `1h`, `2d`, etc.")
            await asyncio.sleep(seconds)
            if mute_role in member.roles:
                try:
                    await member.remove_roles(mute_role, reason="Mute duration expired.")
                except discord.Forbidden:
                    pass
                await ctx.send(f"⏰ {member.mention} has been automatically unmuted after `{duration}`.")

    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    @commands.command(name="unmute")
    async def unmute_user(self, ctx: commands.Context, member: discord.Member):
        """Unmute a member."""
        mute_role = await self.get_or_create_mute_role(ctx.guild)
        if mute_role in member.roles:
            try:
                await member.remove_roles(mute_role, reason="manual unmute")
            except discord.Forbidden:
                return await ctx.send("🚫 I can't remove the mute role (check role position/permissions).")
            await ctx.send(f"🔊 {member.mention} has been unmuted.")
        else:
            await ctx.send("❗ This user is not muted.")

    @commands.guild_only()
    @commands.mod_or_permissions(ban_members=True)
    @commands.command(name="ban")
    async def ban_user(self, ctx: commands.Context, member: discord.Member, *, reason="No reason provided"):
        """Ban a member."""
        await member.ban(reason=reason)
        await ctx.send(f"🔨 {member.mention} has been banned. Reason: {reason}")

    @commands.guild_only()
    @commands.mod_or_permissions(kick_members=True)
    @commands.command(name="kick")
    async def kick_user(self, ctx: commands.Context, member: discord.Member, *, reason="No reason provided"):
        """Kick a member."""
        await member.kick(reason=reason)
        await ctx.send(f"🚪 {member.mention} has been kicked. Reason: {reason}")

    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    @commands.command(name="warn")
    async def warn_user(self, ctx: commands.Context, member: discord.Member, *, reason: str):
        """Warn a member."""
        warnings = await self.config.guild(ctx.guild).warnings()
        user_id = str(member.id)
        warnings.setdefault(user_id, [])
        warnings[user_id].append({"timestamp": datetime.utcnow().isoformat(), "reason": reason})
        await self.config.guild(ctx.guild).warnings.set(warnings)
        await ctx.send(f"⚠️ {member.mention} has been warned. Reason: {reason}")

    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    @commands.command(name="warnings")
    async def show_warnings(self, ctx: commands.Context, member: discord.Member):
        """Show warnings for a user."""
        warnings = await self.config.guild(ctx.guild).warnings()
        user_id = str(member.id)
        if user_id not in warnings or not warnings[user_id]:
            return await ctx.send("✅ This user has no warnings.")
        msg = f"📄 Warnings for {member.mention}:\n"
        for idx, entry in enumerate(warnings[user_id], 1):
            ts = entry["timestamp"]; rsn = entry["reason"]
            msg += f"**{idx}.** {rsn} *(at {ts})*\n"
        await ctx.send(msg)

    # ---------- Duration parser ----------
    def parse_duration(self, duration: str):
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        try:
            return int(duration[:-1]) * units[duration[-1]]
        except (ValueError, KeyError):
            return None
