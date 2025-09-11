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
        self.config.register_guild(assignments={}, log_channel=None, silent=False)
        self.check_expired_roles.start()

    def cog_unload(self):
        self.check_expired_roles.cancel()

    def parse_duration(self, time_str: str) -> int:
        matches = TIME_REGEX.findall(time_str.lower())
        if not matches:
            return 0
        total_seconds = 0
        for value, unit in matches:
            total_seconds += int(value) * SECONDS[unit]
        return total_seconds

    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    @commands.command(name="temprole")
    async def temprole(
        self, ctx: commands.Context,
        role: discord.Role,
        member: discord.Member,
        duration: str,
        *, reason: str = "No reason provided."
    ):
        """Assign a temporary role to someone."""
        seconds = self.parse_duration(duration)
        if seconds <= 0:
            return await ctx.send(embed=discord.Embed(
                title="❌ Invalid Duration",
                description="Use valid time like `30m`, `2h`, or `1d12h`.",
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
                f"**Duration:** `{duration}`\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Will expire in {seconds} seconds.")

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
    @commands.command(name="temprolelog")
    async def temprolelog(self, ctx: commands.Context, *, channel: str = None):
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
    async def temproleconfig(self, ctx):
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

    @temproleconfig.command(name="silent")
    async def temprole_silent_toggle(self, ctx, toggle: bool):
        """Enable or disable public embeds in the command channel."""
        await self.config.guild(ctx.guild).silent.set(toggle)
        await ctx.send(embed=discord.Embed(
            title="🔇 Silent Mode Updated",
            description="I will {} post embeds in the command channel.".format("no longer" if toggle else "now"),
            color=discord.Color.green()
        ))

    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    @commands.command(name="temprolecancel")
    async def temprolecancel(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
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
        log_id = await self.config.guild(ctx.guild).log_channel()
        log_channel = ctx.guild.get_channel(log_id) if log_id else None

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

        if log_channel and log_channel.permissions_for(ctx.guild.me).send_messages:
            await log_channel.send(embed=embed)

    @commands.guild_only()
    @checks.admin_or_permissions(manage_roles=True)
    @commands.command(name="temprolelist")
    async def temprolelist(self, ctx: commands.Context):
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

        count = 0
        for user_id, data in assignments.items():
            member = ctx.guild.get_member(int(user_id))
            role = ctx.guild.get_role(data["role"])
            time_left = max(0, int(data["expires"]) - int(time.time()))
            reason = data.get("reason", "No reason provided.")

            if not member or not role:
                continue

            days, remainder = divmod(time_left, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)

            time_str = []
            if days:
                time_str.append(f"{days}d")
            if hours:
                time_str.append(f"{hours}h")
            if minutes:
                time_str.append(f"{minutes}m")
            if not time_str:
                time_str.append(f"{seconds}s")

            display = (
                f"**{count + 1}.** 👤 {member.mention} | 🏷️ {role.mention}\n"
                f"⏰ Expires in: `{', '.join(time_str)}`\n"
                f"📝 Reason: {reason}"
            )
            embed.add_field(name="\u200b", value=display, inline=False)
            count += 1

            if count >= 10:
                embed.set_footer(text="Only showing first 10 active assignments.")
                break

        await ctx.send(embed=embed)

    @tasks.loop(seconds=60)
    async def check_expired_roles(self):
        for guild in self.bot.guilds:
            try:
                assignments = await self.config.guild(guild).assignments()
                to_remove = []
                log_id = await self.config.guild(guild).log_channel()
                silent = await self.config.guild(guild).silent()
                log_channel = guild.get_channel(log_id) if log_id else None

                for user_id, data in assignments.items():
                    if time.time() >= data["expires"]:
                        member = guild.get_member(int(user_id))
                        role = guild.get_role(data["role"])
                        channel = guild.get_channel(data.get("channel"))
                        reason = data.get("reason", "No reason provided.")

                        if member and role and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="Temporary role expired.")
                            except discord.Forbidden:
                                pass

                            embed = discord.Embed(
                                title="⏰ Temporary Role Expired",
                                description=(
                                    f"**User:** {member.mention}\n"
                                    f"**Role:** {role.mention}\n"
                                    f"**Reason:** {reason}"
                                ),
                                color=discord.Color.orange()
                            )

                            if channel and not silent and channel.permissions_for(guild.me).send_messages:
                                await channel.send(embed=embed)

                            if log_channel and log_channel.permissions_for(guild.me).send_messages:
                                await log_channel.send(embed=embed)

                        to_remove.append(user_id)

                for uid in to_remove:
                    del assignments[uid]

                if to_remove:
                    await self.config.guild(guild).assignments.set(assignments)

            except Exception:
                continue

    @check_expired_roles.before_loop
    async def before_checker(self):
        await self.bot.wait_until_ready()
