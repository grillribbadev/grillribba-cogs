import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import json
import os

CONFIG_PATH = "accountwatch_config.json"

def load_config():
    if not os.path.isfile(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

class AccountWatch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = load_config()

    def get_guild_config(self, guild_id):
        return self.config.get(str(guild_id), {"log_channel": None, "threshold_days": 7})

    def set_guild_config(self, guild_id, key, value):
        guild_id = str(guild_id)
        if guild_id not in self.config:
            self.config[guild_id] = {"log_channel": None, "threshold_days": 7}
        self.config[guild_id][key] = value
        save_config(self.config)

    @commands.command(name="setaltdays")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def set_alt_threshold(self, ctx, days: int):
        """Set the minimum account age (in days) to trigger alt alert logging."""
        if days < 0:
            return await ctx.send("❌ Threshold must be a positive number.")

        self.set_guild_config(ctx.guild.id, "threshold_days", days)
        await ctx.send(f"🛡️ Alt alert threshold set to `{days}` day(s).")

    @commands.command(name="setaltlog")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def set_alt_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where new alt account alerts are sent."""
        self.set_guild_config(ctx.guild.id, "log_channel", channel.id)
        await ctx.send(f"📋 Alt log channel set to {channel.mention}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_config = self.get_guild_config(member.guild.id)
        threshold_days = guild_config.get("threshold_days", 7)
        log_channel_id = guild_config.get("log_channel")

        if not log_channel_id:
            return  # No log channel set

        log_channel = member.guild.get_channel(log_channel_id)
        if not log_channel:
            return  # Channel not found (might’ve been deleted)

        account_age = datetime.now(timezone.utc) - member.created_at
        if account_age < timedelta(days=threshold_days):
            embed = discord.Embed(
                title="⚠️ New Account Alert",
                description=f"{member.mention} joined with a very new account.",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M UTC"))
            embed.add_field(name="Account Age", value=f"{account_age.days} day(s)")
            embed.set_footer(text=f"Threshold: {threshold_days} day(s)")

            try:
                await log_channel.send(embed=embed)
            except discord.Forbidden:
                print(f"[ERROR] Missing permissions to send in {log_channel.name}")

async def setup(bot):
    await bot.add_cog(AccountWatch(bot))
