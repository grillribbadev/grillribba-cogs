import discord
from redbot.core import commands, Config, checks
from discord.ext import tasks
import re
import time

TIME_REGEX = re.compile(r"(\d+)([smhdy])")
SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "y": 31536000}


class AutoRoleManager(commands.Cog):
    """Assign roles temporarily; remove them on time; optional linked-role removal on apply; keep expired history."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=879823456123, force_registration=True)
        # assignments: { user_id: { "role": int, "expires": int, "channel": int, "reason": str } }
        # expired:     [ { "user": int, "role": int, "expired": int, "reason": str } ]
        # rolelinks:   { "trigger_role_id": remove/reapply role_id }  (when trigger is applied -> remove; when ends -> reapply)
        self.config.register_guild(assignments={}, log_channel=None, silent=False, expired=[], rolelinks={})
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

    async def _send_log_embed(self, guild: discord.Guild, embed: discord.Embed):
        log_id = await self.config.guild(guild).log_channel()
        if not log_id:
            return
        ch = guild.get_channel(log_id)
        if ch and ch.permissions_for(guild.me).send_messages:
            await ch.send(embed=embed)

    async def _log_expired_record(self, guild: discord.Guild, user_id: int, role_id: int, expired_ts: int, reason: str):
        """Persist an expired record and keep history tidy (<=30 days; <=500 entries)."""
        history = await self.config.guild(guild).expired()
        history.append(
            {"user": int(user_id), "role": int(role_id), "expired": int(expired_ts), "reason": str(reason or "")}
        )
        thirty_days_ago = int(time.time()) - (30 * 86400)
        history = [h for h in history if h.get("expired", 0) >= thirty_days_ago]
        if len(history) > 500:
            history = history[-500:]
        await self.config.guild(guild).expired.set(history)

    async def _maybe_apply_rolelink(self, guild: discord.Guild, member: discord.Member, trigger_role_id: int):
        """If trigger role is configured, remove the linked role from member (if present)."""
        links = await self.config.guild(guild).rolelinks()
        target_id = links.get(str(trigger_role_id))
        if not target_id:
            return
        remove_role = guild.get_role(int(target_id))
        if not remove_role:
            return
        if remove_role in member.roles:
            try:
                await member.remove_roles(remove_role, reason=f"Linked removal: trigger role {trigger_role_id} applied")
            except discord.Forbidden:
                # Silent fail; insufficient perms
                return

            # Log linked removal
            trig = guild.get_role(trigger_role_id)
            embed = discord.Embed(
                title="🔗 Linked Role Removed",
                description=(
                    f"**User:** {member.mention}\n"
                    f"**Trigger:** {trig.mention if trig else f'<@&{trigger_role_id}>'}\n"
                    f"**Removed:** {remove_role.mention}"
                ),
                color=discord.Color.orange()
            )
            await self._send_log_embed(guild, embed)

    async def _maybe_reapply_rolelink(self, guild: discord.Guild, member: discord.Member, trigger_role_id: int):
        """If trigger role is configured, reapply the linked role when trigger ends (expire/cancel)."""
        if not member:
            return
        links = await self.config.guild(guild).rolelinks()
        target_id = links.get(str(trigger_role_id))
        if not target_id:
            return
        add_role = guild.get_role(int(target_id))
        if not add_role:
            return
        if add_role not in member.roles:
            try:
                await member.add_roles(add_role, reason=f"Linked reapply: trigger role {trigger_role_id} ended")
            except discord.Forbidden:
                return

            # Log linked reapply
            trig = guild.get_role(trigger_role_id)
            embed = discord.Embed(
                title="🔗 Linked Role Reapplied",
                description=(
                    f"**User:** {member.mention}\n"
                    f"**Trigger Ended:** {trig.mention if trig else f'<@&{trigger_role_id}>'}\n"
                    f"**Reapplied:** {add_role.mention}"
                ),
                color=discord.Color.green()
            )
            await self._send_log_embed(guild, embed)

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

        # Apply linked-role removal if configured
        await self._maybe_apply_rolelink(ctx.guild, member, role.id)

        # Persist absolute expiration timestamp
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

        # Also log to the configured log channel
        await self._send_log_embed(ctx.guild, embed)

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

            # Reapply linked role if configured
            await self._maybe_reapply_rolelink(ctx.guild, member, role.id)

        # Remove assignment record
        del data[str(member.id)]
        await self.config.guild(ctx.guild).assignments.set(data)

        # Notify channel + log
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
        await self._send_log_embed(ctx.guild, embed)

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

            lines.append(f"• {user_text} — {role_text} • expired <t:{when}:R>\n  └─ 📝 {reason}")
            shown += 1
            if shown >= 20:
                lines.append("*Showing latest 20…*")
                break

        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    # ----------------------- NEW: linked-role config -----------------------

    @commands.guild_only()
    @checks.admin()
    @commands.group(name="temprolelink", invoke_without_command=True)
    async def temprole_link(self, ctx):
        """Manage linked role removals (when TRIGGER role is applied, REMOVE role is removed; on end, reapply)."""
        await ctx.send_help(ctx.command)

    @temprole_link.command(name="set")
    async def temprole_link_set(self, ctx, trigger: discord.Role, remove_or_reapply: discord.Role):
        """Set a link: when TRIGGER is applied, REMOVE_OR_REAPPLY is removed; when TRIGGER ends, it is reapplied."""
        links = await self.config.guild(ctx.guild).rolelinks()
        links[str(trigger.id)] = remove_or_reapply.id
        await self.config.guild(ctx.guild).rolelinks.set(links)
        await ctx.send(f"✅ Configured: when {trigger.mention} is applied → remove {remove_or_reapply.mention}; when it ends → reapply it.")

    @temprole_link.command(name="clear")
    async def temprole_link_clear(self, ctx, trigger: discord.Role):
        """Clear a linked role mapping."""
        links = await self.config.guild(ctx.guild).rolelinks()
        if str(trigger.id) in links:
            removed = links.pop(str(trigger.id))
            await self.config.guild(ctx.guild).rolelinks.set(links)
            await ctx.send(f"✅ Cleared link for {trigger.mention} (was <@&{removed}>).")
        else:
            await ctx.send(f"❌ No link found for {trigger.mention}.")

    @temprole_link.command(name="list")
    async def temprole_link_list(self, ctx):
        """List all current linked role mappings."""
        links = await self.config.guild(ctx.guild).rolelinks()
        if not links:
            return await ctx.send("ℹ️ No linked role mappings configured.")
        lines = []
        for trig, rem in links.items():
            trig_role = ctx.guild.get_role(int(trig))
            rem_role = ctx.guild.get_role(int(rem))
            if trig_role and rem_role:
                lines.append(f"When {trig_role.mention} → remove {rem_role.mention}; when it ends → reapply {rem_role.mention}")
        embed = discord.Embed(title="🔗 Linked Role Mappings", description="\n".join(lines), color=discord.Color.blurple())
        await ctx.send(embed=embed)

    # ----------------------- background expiration -----------------------

    @tasks.loop(seconds=60)
    async def check_expired_roles(self):
        """
        Runs every 60s. Removes expired roles.
        If a role expired during downtime, avoid spamming the origin channel (still log and record).
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

                        # Reapply linked role (on trigger end)
                        await self._maybe_reapply_rolelink(guild, member, role.id)

                    # Build embed (log; origin channel only if not outage catch-up)
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

                    # Consider outages: if overdue > 120s, assume offline catch-up, don't spam origin channel.
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

    # ----------------------- event hook (manual role adds) -----------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """
        If a role is added manually (not via command) and it is configured as a trigger,
        remove the linked role automatically.
        """
        if before.guild != after.guild:
            return
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        if not added:
            return
        links = await self.config.guild(after.guild).rolelinks()
        if not links:
            return
        for rid in added:
            if str(rid) in links:
                await self._maybe_apply_rolelink(after.guild, after, rid)
