import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_timedelta
from datetime import datetime, timedelta
import asyncio

class Moderation(commands.Cog):
    """Moderation cog with mute, ban, kick, and warning features."""

    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847326529, force_registration=True)

        default_guild = {
            "mute_role": None,
            "warnings": {}  # {user_id: [timestamp, reason]}
        }

        self.config.register_guild(**default_guild)

    ### 🔍 Utility: Get or create mute role
    async def get_or_create_mute_role(self, guild: discord.Guild):
        mute_role_id = await self.config.guild(guild).mute_role()
        mute_role = guild.get_role(mute_role_id) if mute_role_id else None

        if not mute_role:
            mute_role = await guild.create_role(name="Muted", reason="Used for muting users via moderation cog.")
            for channel in guild.channels:
                await channel.set_permissions(mute_role, speak=False, send_messages=False, add_reactions=False)
            await self.config.guild(guild).mute_role.set(mute_role.id)

        return mute_role

    ### 🛠️ Configuration: Set mute role manually
    @commands.admin_or_permissions(manage_roles=True)
    @commands.command(name="setmuterole")
    async def set_mute_role(self, ctx: commands.Context, role: discord.Role):
        """Set a custom mute role."""
        await self.config.guild(ctx.guild).mute_role.set(role.id)
        await ctx.send(f"✅ Mute role set to: {role.name}")

    ### 🔇 Command: Mute
    @commands.mod_or_permissions(manage_roles=True)
    @commands.command(name="mute")
    async def mute_user(self, ctx: commands.Context, member: discord.Member, duration: str = None, *, reason: str = "No reason provided"):
        """
        Mute a member with an optional duration (e.g. 10m, 2h, 1d).
        """
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
                await member.remove_roles(mute_role, reason="Mute duration expired.")
                await ctx.send(f"⏰ {member.mention} has been automatically unmuted after `{duration}`.")

    ### 🔊 Command: Unmute
    @commands.mod_or_permissions(manage_roles=True)
    @commands.command(name="unmute")
    async def unmute_user(self, ctx: commands.Context, member: discord.Member):
        """Unmute a member."""
        mute_role = await self.get_or_create_mute_role(ctx.guild)

        if mute_role in member.roles:
            await member.remove_roles(mute_role)
            await ctx.send(f"🔊 {member.mention} has been unmuted.")
        else:
            await ctx.send("❗ This user is not muted.")

    ### 🔨 Command: Ban
    @commands.mod_or_permissions(ban_members=True)
    @commands.command(name="ban")
    async def ban_user(self, ctx: commands.Context, member: discord.Member, *, reason="No reason provided"):
        """Ban a member."""
        await member.ban(reason=reason)
        await ctx.send(f"🔨 {member.mention} has been banned. Reason: {reason}")

    ### 🚪 Command: Kick
    @commands.mod_or_permissions(kick_members=True)
    @commands.command(name="kick")
    async def kick_user(self, ctx: commands.Context, member: discord.Member, *, reason="No reason provided"):
        """Kick a member."""
        await member.kick(reason=reason)
        await ctx.send(f"🚪 {member.mention} has been kicked. Reason: {reason}")

    ### ⚠️ Command: Warn
    @commands.mod_or_permissions(manage_messages=True)
    @commands.command(name="warn")
    async def warn_user(self, ctx: commands.Context, member: discord.Member, *, reason: str):
        """Warn a member."""
        warnings = await self.config.guild(ctx.guild).warnings()
        user_id = str(member.id)

        if user_id not in warnings:
            warnings[user_id] = []

        warnings[user_id].append({"timestamp": datetime.utcnow().isoformat(), "reason": reason})
        await self.config.guild(ctx.guild).warnings.set(warnings)

        await ctx.send(f"⚠️ {member.mention} has been warned. Reason: {reason}")

    ### 📜 Command: Show Warnings
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
            time = entry["timestamp"]
            reason = entry["reason"]
            msg += f"**{idx}.** {reason} *(at {time})*\n"

        await ctx.send(msg)

    ### ⏱️ Duration Parser
    def parse_duration(self, duration: str):
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        try:
            time_unit = duration[-1]
            time_value = int(duration[:-1])
            return time_value * units[time_unit]
        except (ValueError, KeyError):
            return None
