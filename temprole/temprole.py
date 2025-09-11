import discord
from redbot.core import commands, Config, checks
from discord.ext import tasks
import re
import time

TIME_REGEX = re.compile(r"(\d+)([smhdy])")
SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "y": 31536000}


class AutoRoleManager(commands.Cog):
    """Assign roles temporarily and remove them after a set duration."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=879823456123, force_registration=True)
        # assignments: { user_id: { "role": int, "expires": int, "channel": int, "reason": str } }
        # expired: list of dicts { "user": int, "role": int, "expired": int, "reason": str }
        self.config.register_guild(assignments={}, log_channel=None, silent=False, expired=[])
        self.check_expired_roles.start()

    def cog_unload(self):
        self.check_expired_roles.cancel()

    # ----------------------- helpers -----------------------

    def parse_duration(self, time_str: str) -> int:
        """Parse a chained duration like '1d12h30m' into total seconds."""
        matches = TIME_REGEX.findall(time_str.lower())
        if not matches:
            return 0
        total_seconds = 0
        for value, unit in matches:
            total_seconds += int(value) * SECONDS[unit]
        return total_seconds

    def format_seconds(self, seconds: int) -> str:
        """Short human format (1d 2h 3m)."""
        seconds = max(0, int(seconds))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts)

    async def _log_expired_record(self, guild: discord.Guild, user_id: int, role_id: int, expired_ts: int, reason: str):
        """
        Persist an expired record and keep history tidy:
        - Trim entries older than 30 days
        - Cap to last 500 entries
        """
        history = await self.config.guild(guild).expired()
        history.append(
            {"user": int(user_id), "role": int(role_id), "expired": int(expired_ts), "reason": str(reason or "")}
        )
        # Trim by age (30 days) and by length (500)
        thirty_days_ago = int(time.time()) - (30 * 86400)
        history = [h for h in history if h.get("expired", 0) >= thirty_days_ago]
        if len(history) > 500:
            history = history[-500:]
        await self.config.guild(guild).expired.set(history)

    # ----------------------- commands -----------------------

    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    @commands.command(name="temprole")
    async def temprole(
        self,
        ctx: commands.Context,
        role: discord.Role,
        member: discord.Member,
        duration: str,
        *, reason: str = "No reason provided."
    ):
        """Assign a temporary role. Supports chained units like `30m`, `2h`, `1d12h`, etc."""
        seconds = self.parse_duration(duration)
        if seconds <= 0:
            return await ctx.send(embed=discord.Embed(
                title="❌ Invalid Duration",
                description="Use times like `30m`, `2h`, or `1d12h`.",
                color=discord.Color.red()
            ))

        if role >= ctx.guild.me.top_role:
            return await ctx.send(embed=discord.Embed(
                title="❌ Cannot Assign Role",
                description="That role is higher than my top role.",
                color=discord.Color.red()
            ))

        if role in member.roles:
            return await ctx.send(embed=discord.Embed(
                title="⚠️ Already Has Role",
                description=f"{member.mention} already has {role.mention}.",
                color=discord.Color.orange()
            ))

        try:
            await member.add_roles(role, reason=reason)
        except discord.Forbidden:
            return await ctx.send(embed=discord.Embed(
                title="❌ Permission Error",
                description="I don't have permission to assign that role.",
                color=discord.Color.red()
            ))

        expire_at = int(time.time()) + seconds
        guild_data = await self.config.guild(ctx.guild).assignments()
        guild_data[str(member.id)] = {
            "role": role.id,
            "expires": expire_at,
            "channel": ctx.channel.id,
            "reason": reason
        }
        await self.config.guild(ctx.guild).assignments.set(guild_data)

        embed = discord.Embed(
            title="✅ Temporary Role Assigned",
            description=(
                f"**User:** {member.mention}\n"
                f"**Role:** {role.mention}\n"
                f"**Duration:** `{self.format_seconds(seconds)}`\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.green()
        )
        embed.add_field(name="Expires", value=f"<t:{expire_at}:F> • <t:{expire_at}:R>", inline=False)

        silent = await self.config.guild(ctx.guild).silent()
        if not silent:
            await ctx.send(embed=embed)

        log_id = await self.config.guild(ctx.guild).log_channel()
        if log_id:
            log_channel = ctx.guild.get_channel(log_id)
            if log_channel and log_channel.permissions_for(ctx.guild.me).send_messages:
                await log_channel.send(embed=embed)

    @commands.guild_only()
    @checks.admin()
    @commands.command(name="temprolestatus")
    async def temprole_status(self, ctx: commands.Context, member: discord.Member):
        """Check remaining duration of a user's temporary role."""
        data = await self.config.guild(ctx.guild).assignments()
        entry = data.get(str(member.id))
        if not entry:
            return await ctx.send(embed=discord.Embed(
                title="ℹ️ No Temporary Role",
                description=f"{member.mention} does not have an active temporary role.",
                color=discord.Color.blurple()
            ))

        role = ctx.guild.get_role(entry["role"])
        expires = entry["expires"]
        time_left = expires - int(time.time())

        if time_left <= 0:
            return await ctx.send(embed=discord.Embed(
                title="⌛ Expired",
                description=f"{member.mention}'s temporary role has already expired.",
                color=discord.Color.red()
            ))

        embed = discord.Embed(
            title="⏳ Temporary Role Info",
            description=(
                f"**User:** {member.mention}\n"
                f"**Role:** {role.mention if role else 'Deleted Role'}\n"
                f"**Time Remaining:** `{self.format_seconds(time_left)}`\n"
                f"**Reason:** {entry.get('reason', 'No reason provided.')}"
            ),
            color=discord.Color.blue()
        )
        embed.add_field(name="Expires", value=f"<t:{expires}:F> • <t:{expires}:R>", inline=False)
        await ctx.send(embed=embed)

    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    @commands.command(name="temprolecancel")
    async def temprole_cancel(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        """Cancel a user's temporary role early."""
        data = await self.config.guild(ctx.guild).assignments()
        entry = data.get(str(member.id))

        if not entry:
            return await ctx.send(embed=discord.Embed(
                title="❌ No Active Temp Role",
                description=f"{member.mention} has no active temporary role.",
                color=discord.Color.red()
            ))

        role = ctx.guild.get_role(entry["role"])
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"Temp role manually canceled: {reason}")
            except discord.Forbidden:
                return await ctx.send(embed=discord.Embed(
                    title="❌ Permission Error",
                    description="I couldn't remove the role due to missing permissions.",
                    color=discord.Color.red()
                ))

        del data[str(member.id)]
        await self.config.guild(ctx.guild).assignments.set(data)

        embed = discord.Embed(
            title="🔓 Temporary Role Cancelled",
            description=(
                f"**User:** {member.mention}\n"
                f"**Role:** {role.mention if role else 'Unknown'}\n"
                f"**Cancelled By:** {ctx.author.mention}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

        log_id = await self.config.guild(ctx.guild).log_channel()
        if log_id:
            log_channel = ctx.guild.get_channel(log_id)
            if log_channel and log_channel.permissions_for(ctx.guild.me).send_messages:
                await log_channel.send(embed=embed)

    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    @commands.command(name="temprolelist")
    async def temprole_list(self, ctx: commands.Context):
        """List all currently active temporary roles in this server."""
        assignments = await self.config.guild(ctx.guild).assignments()
        if not assignments:
            return await ctx.send(embed=discord.Embed(
                title="📋 No Active Temp Roles",
                description="There are currently no active temporary role assignments.",
                color=discord.Color.blurple()
            ))

        embed = discord.Embed(
            title=f"📋 Active Temporary Roles ({len(assignments)})",
            color=discord.Color.blurple()
        )

        for i, (user_id, data) in enumerate(assignments.items(), start=1):
            member = ctx.guild.get_member(int(user_id))
            role = ctx.guild.get_role(data["role"])
            time_left = max(0, int(data["expires"]) - int(time.time()))
            reason = data.get("reason", "No reason provided.")
            if not member or not role:
                continue

            display = (
                f"**{i}.** 👤 {member.mention} | 🏷️ {role.mention}\n"
                f"⏰ Expires in: `{self.format_seconds(time_left)}`\n"
                f"📝 Reason: {reason}\n"
                f"📅 Expires: <t:{data['expires']}:R> (<t:{data['expires']}:F>)"
            )
            embed.add_field(name="\u200b", value=display, inline=False)

            if i >= 10:
                embed.set_footer(text="Only showing first 10 active assignments.")
                break

        await ctx.send(embed=embed)

    @commands.guild_only()
    @checks.admin()
    @commands.command(name="temprolelog")
    async def temprole_log(self, ctx: commands.Context, *, channel: str = None):
        """Set or clear the log channel for temprole logs. Use 'none' to disable."""
        if channel is None:
            return await ctx.send("Specify a channel or `none`.")

        if channel.lower() == "none":
            await self.config.guild(ctx.guild).log_channel.clear()
            return await ctx.send(embed=discord.Embed(
                title="📘 Log Channel Cleared",
                description="Logging has been disabled.",
                color=discord.Color.orange()
            ))

        converter = commands.TextChannelConverter()
        try:
            resolved = await converter.convert(ctx, channel)
        except commands.BadArgument:
            return await ctx.send(embed=discord.Embed(
                title="❌ Invalid Channel",
                description="Could not resolve the provided channel.",
                color=discord.Color.red()
            ))

        await self.config.guild(ctx.guild).log_channel.set(resolved.id)
        await ctx.send(embed=discord.Embed(
            title="📘 Log Channel Set",
            description=f"Logs will go to {resolved.mention}.",
            color=discord.Color.blue()
        ))

    @commands.guild_only()
    @checks.admin()
    @commands.group(name="temproleconfig", invoke_without_command=True)
    async def temprole_config(self, ctx):
        """View the current temprole config."""
        data = await self.config.guild(ctx.guild).all()
        log = ctx.guild.get_channel(data["log_channel"]) if data["log_channel"] else None
        embed = discord.Embed(
            title="⚙️ TempRole Configuration",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Log Channel", value=log.mention if log else "❌ Not set", inline=False)
        embed.add_field(name="Silent Mode", value="✅ Enabled" if data["silent"] else "❌ Disabled", inline=False)
        await ctx.send(embed=embed)

    @temprole_config.command(name="silent")
    async def temprole_silent_toggle(self, ctx, toggle: bool):
        """Enable or disable public embeds in the command channel."""
        await self.config.guild(ctx.guild).silent.set(toggle)
        await ctx.send(embed=discord.Embed(
            title="🔇 Silent Mode Updated",
            description="I will {} post embeds in the command channel.".format("no longer" if toggle else "now"),
            color=discord.Color.green()
        ))

    # ----------------------- NEW: expired listing -----------------------

    @commands.guild_only()
    @checks.admin()
    @commands.command(name="temproleexpired")
    async def temprole_expired(self, ctx: commands.Context, hours: int = 24):
        """
        List roles that expired in the last <hours> (default 24).
        Does not include manual cancels; only true expirations.
        """
        hours = max(1, min(168, hours))  # clamp 1..168 (1 hour..7 days)
        cutoff = int(time.time()) - (hours * 3600)
        history = await self.config.guild(ctx.guild).expired()
        recent = [h for h in history if h.get("expired", 0) >= cutoff]

        if not recent:
            return await ctx.send(embed=discord.Embed(
                title="🗂️ No Expired Roles",
                description=f"No roles expired in the last **{hours}h**.",
                color=discord.Color.blurple()
            ))

        # Build a compact embed (up to 20 rows)
        embed = discord.Embed(
            title=f"🗂️ Roles Expired in the Last {hours}h",
            color=discord.Color.orange()
        )

        # Sort newest first
        recent.sort(key=lambda x: x.get("expired", 0), reverse=True)

        lines = []
        shown = 0
        for rec in recent:
            user_id = rec.get("user")
            role_id = rec.get("role")
            when    = rec.get("expired", 0)
            reason  = rec.get("reason") or "No reason provided."

            user_text = f"<@{user_id}>"
            role_text = f"<@&{role_id}>"

            lines.append(f"• {user_text} — {role_text} • expired <t:{when}:R>  \n  └─ 📝 {reason}")
            shown += 1
            if shown >= 20:
                lines.append("*Showing latest 20…*")
                break

        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    # ----------------------- background expiration -----------------------

    @tasks.loop(seconds=60)
    async def check_expired_roles(self):
        """
        Runs every 60s. Removes expired roles.
        If a role expired a while ago (e.g., during downtime), we avoid spamming the
        original channel; we still log to the log channel for auditing and store history.
        """
        now = int(time.time())
        for guild in self.bot.guilds:
            try:
                assignments = await self.config.guild(guild).assignments()
                if not assignments:
                    continue

                log_id = await self.config.guild(guild).log_channel()
                silent_cfg = await self.config.guild(guild).silent()
                log_channel = guild.get_channel(log_id) if log_id else None

                to_remove = []
                for user_id, entry in list(assignments.items()):
                    expires = int(entry.get("expires", 0))
                    if now < expires:
                        continue

                    member = guild.get_member(int(user_id))
                    role = guild.get_role(int(entry.get("role", 0)))
                    origin_channel = guild.get_channel(entry.get("channel"))
                    reason = entry.get("reason", "No reason provided.")

                    # Remove the role if still present
                    if member and role and role in member.roles:
                        try:
                            await member.remove_roles(role, reason="Temporary role expired.")
                        except discord.Forbidden:
                            pass

                    # Build log embed
                    user_text = member.mention if member else f"<@{user_id}>"
                    role_text = role.mention if role else f"<@&{entry.get('role', 0)}>"
                    embed = discord.Embed(
                        title="⏰ Temporary Role Expired",
                        description=(
                            f"**User:** {user_text}\n"
                            f"**Role:** {role_text}\n"
                            f"**Reason:** {reason}\n"
                            f"**Expired:** <t:{expires}:F> • <t:{expires}:R>"
                        ),
                        color=discord.Color.orange()
                    )

                    # Consider outages: if overdue > 120s, assume offline catch-up, don't spam origin.
                    overdue = now - expires
                    expired_during_outage = overdue > 120

                    if origin_channel and not silent_cfg and not expired_during_outage:
                        if origin_channel.permissions_for(guild.me).send_messages:
                            await origin_channel.send(embed=embed)

                    if log_channel and log_channel.permissions_for(guild.me).send_messages:
                        await log_channel.send(embed=embed)

                    # Store in persistent expired history
                    await self._log_expired_record(
                        guild=guild,
                        user_id=int(user_id),
                        role_id=int(entry.get("role", 0)),
                        expired_ts=expires,
                        reason=reason
                    )

                    to_remove.append(user_id)

                for uid in to_remove:
                    assignments.pop(uid, None)
                if to_remove:
                    await self.config.guild(guild).assignments.set(assignments)

            except Exception:
                # Keep the loop alive on per-guild issues
                continue

    @check_expired_roles.before_loop
    async def before_checker(self):
        await self.bot.wait_until_ready()
