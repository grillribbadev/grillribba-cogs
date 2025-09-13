import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime
import asyncio

class Moderation(commands.Cog):
    """Moderation cog with mute, ban, kick, and warning features."""

    __version__ = "1.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847326529, force_registration=True)
        default_guild = {
            "mute_role": None,
            "warnings": {}  # {user_id: [{"timestamp":..., "reason":...}, ...]}
        }
        self.config.register_guild(**default_guild)

    # ---------- Role & overwrite helpers ----------

    def _text_overwrite_spec(self) -> dict[str, bool]:
        # Thread perms included so they can't bypass via threads
        return dict(
            send_messages=False,
            add_reactions=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
        )

    def _voice_overwrite_spec(self) -> dict[str, bool]:
        # Leave connect alone so staff can move them if needed
        return dict(
            speak=False,
            stream=False,
            request_to_speak=False,
        )

    async def _apply_mute_overwrites(self, guild: discord.Guild, role: discord.Role) -> int:
        """
        Ensure basic mute overwrites are set everywhere for the given role.
        Returns number of channels updated (attempted writes).
        """
        updated = 0
        text_spec = self._text_overwrite_spec()
        voice_spec = self._voice_overwrite_spec()

        for ch in guild.channels:
            try:
                if isinstance(ch, (discord.TextChannel, discord.ForumChannel, discord.CategoryChannel)):
                    await ch.set_permissions(role, reason="muteconfig: text perms", **text_spec)
                    updated += 1
                elif isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    await ch.set_permissions(role, reason="muteconfig: voice perms", **voice_spec)
                    updated += 1
                else:
                    # Threads and other derivations: try text-style perms
                    await ch.set_permissions(role, reason="muteconfig: misc perms", **text_spec)
                    updated += 1
            except discord.Forbidden:
                # Missing perms on some channel; skip gracefully
                continue
            except Exception:
                # Ignore odd channel types or transient errors
                continue
        return updated

    def _has_required_overwrites(self, ch: discord.abc.GuildChannel, role: discord.Role) -> bool:
        """
        Check if a channel/category has the expected explicit denies for this role.
        """
        ow = ch.overwrites_for(role)
        needed = self._text_overwrite_spec() if isinstance(
            ch, (discord.TextChannel, discord.ForumChannel, discord.CategoryChannel)
        ) else self._voice_overwrite_spec()

        for attr, expected in needed.items():
            # Only count it "correct" if explicitly set to the expected value
            if getattr(ow, attr, None) is not expected:
                return False
        return True

    async def _scan_overwrites(self, guild: discord.Guild, role: discord.Role) -> tuple[int, int]:
        """
        Return (ok_count, total_considered) for channels/categories with correct explicit denies.
        """
        total = 0
        ok = 0
        for ch in guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.ForumChannel, discord.CategoryChannel, discord.VoiceChannel, discord.StageChannel)):
                total += 1
                try:
                    if self._has_required_overwrites(ch, role):
                        ok += 1
                except Exception:
                    pass
        return ok, total

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

    # ---------- Configuration group ----------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    @commands.hybrid_group(name="muteconfig", invoke_without_command=True)
    async def muteconfig(self, ctx: commands.Context):
        """
        Create/repair the muted role and apply proper permissions everywhere.
        Run with no subcommand to (re)configure automatically.
        """
        try:
            mute_role = await self._ensure_mute_role(ctx.guild)
        except discord.Forbidden:
            return await ctx.reply("🚫 I need **Manage Roles** and permission to manage channel overwrites.")
        count = await self._apply_mute_overwrites(ctx.guild, mute_role)
        ok, total = await self._scan_overwrites(ctx.guild, mute_role)
        await ctx.reply(
            f"✅ Mute role is **{mute_role.name}** (`{mute_role.id}`). "
            f"Permissions refreshed on **{count}** channels. "
            f"Status: **{ok}/{total}** channels have the required explicit denies."
        )

    @muteconfig.command(name="show")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def muteconfig_show(self, ctx: commands.Context):
        """Show the current mute configuration (role + overwrite coverage)."""
        rid = await self.config.guild(ctx.guild).mute_role()
        role = ctx.guild.get_role(rid) if rid else None
        if not role:
            return await ctx.reply("ℹ️ No mute role configured yet. Run `[p]muteconfig` to set it up.")
        ok, total = await self._scan_overwrites(ctx.guild, role)
        await ctx.reply(
            f"🔧 **Mute role:** {role.mention} (`{role.id}`)\n"
            f"🗂 **Overwrite coverage:** {ok}/{total} channels/categories have the expected denies.\n"
            f"▶️ Tip: Run `[p]muteconfig repair` to reapply everywhere."
        )

    @muteconfig.command(name="set")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def muteconfig_set(self, ctx: commands.Context, role: discord.Role):
        """Use a specific role as the mute role and apply the required overwrites."""
        try:
            mute_role = await self._ensure_mute_role(ctx.guild, role=role)
        except discord.Forbidden:
            return await ctx.reply("🚫 I need **Manage Roles** and permission to manage channel overwrites.")
        ok, total = await self._scan_overwrites(ctx.guild, mute_role)
        await ctx.reply(
            f"✅ Mute role set to **{mute_role.name}** (`{mute_role.id}`). "
            f"Overwrite status: **{ok}/{total}** channels covered."
        )

    @muteconfig.command(name="repair")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def muteconfig_repair(self, ctx: commands.Context):
        """Reapply mute overwrites everywhere without changing which role is used."""
        rid = await self.config.guild(ctx.guild).mute_role()
        role = ctx.guild.get_role(rid) if rid else None
        if not role:
            return await ctx.reply("ℹ️ No mute role configured. Run `[p]muteconfig` first.")
        count = await self._apply_mute_overwrites(ctx.guild, role)
        ok, total = await self._scan_overwrites(ctx.guild, role)
        await ctx.reply(
            f"🔁 Reapplied overwrites on **{count}** channels. Coverage now **{ok}/{total}**."
        )

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
        rid = await self.config.guild(ctx.guild).mute_role()
        role = ctx.guild.get_role(rid) if rid else None
        if not role:
            role = await self.get_or_create_mute_role(ctx.guild)
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="manual unmute")
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
