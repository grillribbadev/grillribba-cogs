from redbot.core import commands, Config
import discord
from datetime import datetime, timezone

class AntiAltNotifier(commands.Cog):
    """Notifies staff about new accounts created within a specified number of days."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210)
        default_guild = {
            "account_age_days": 2,  # Notify if account is <= X days old
            "log_channel": None
        }
        self.config.register_guild(**default_guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_settings = await self.config.guild(member.guild).all()

        account_age_days = guild_settings["account_age_days"]
        log_channel_id = guild_settings["log_channel"]

        if not log_channel_id:
            return  # No log channel set, exit

        log_channel = member.guild.get_channel(log_channel_id)
        if not log_channel:
            return  # Channel doesn't exist or bot lacks perms

        account_age = (datetime.now(timezone.utc) - member.created_at).days
        if account_age <= account_age_days:
            embed = discord.Embed(
                title="⚠️ Potential Alt Detected",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name="Account Age", value=f"**{account_age} days old**", inline=True)
            embed.add_field(name="Account Created", value=f"{member.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}", inline=True)
            embed.set_footer(text=f"Guild: {member.guild.name}")

            await log_channel.send(embed=embed)

    # --- Commands ---
    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def antialtnotify(self, ctx):
        """Anti-Alt Notifier settings."""

    @antialtnotify.command()
    async def days(self, ctx, days: int):
        """Set how many days old an account must be flagged (default 2)."""
        await self.config.guild(ctx.guild).account_age_days.set(days)
        await ctx.send(f"✅ Account age detection set to **{days} days or newer**.")

    @antialtnotify.command()
    async def logchannel(self, ctx, channel: discord.TextChannel = None):
        """Set log channel for alt notifications. Leave blank to clear."""
        if channel:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"✅ Log channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("✅ Log channel cleared.")
