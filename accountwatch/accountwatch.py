import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import json
import os

CONFIG_PATH = "accountwatch_config.json"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

class AccountWatch(commands.Cog):
    """
    Alerts for new accounts joining the server under a certain age.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = load_config()

    # ✅ Dev or Owner check
    async def is_dev_or_owner(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            return True

        dev_cog = self.bot.get_cog("Dev")
        if dev_cog and hasattr(dev_cog, "is_dev"):
            return dev_cog.is_dev(ctx.author.id)
        return False

    def get_guild_config(self, guild_id):
        return self.config.get(str(guild_id), {"log_channel": None, "threshold_days": 7})

    def set_guild_config(self, guild_id, key, value):
        guild_id = str(guild_id)
        if guild_id not in self.config:
            self.config[guild_id] = {"log_channel": None, "threshold_days": 7}
        self.config[guild_id][key] = value
        save_config(self.config)

    @commands.command(name="setaltdays", help="Set the minimum account age (in days) to flag as alt.")
    @commands.guild_only()
    async def set_alt_threshold(self, ctx, days: int):
        if not await self.is_dev_or_owner(ctx):
            return await ctx.send("⛔ You don't have permission to use this command.")

        if days < 0:
            return await ctx.send("❌ Threshold must be a positive number.")

        self.set_guild_config(ctx.guild.id, "threshold_days", days)
        await ctx.send(f"🛡️ Alt threshold updated to `{days}` day(s).")

    @commands.command(name="setaltlog", help="Set the log channel for new alt account alerts.")
    @commands.guild_only()
    async def set_alt_log_channel(self, ctx, channel: discord.TextChannel):
        if not await self.is_dev_or_owner(ctx):
            return await ctx.send("⛔ You don't have permission to use this command.")

        self.set_guild_config(ctx.guild.id, "log_channel", channel.id)
        await ctx.send(f"📋 Alt log channel set to {channel.mention}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = self.get_guild_config(member.guild.id)
        threshold_days = config.get("threshold_days", 7)
        log_channel_id = config.get("log_channel")

        if not log_channel_id:
            return

        log_channel = member.guild.get_channel(log_channel_id)
        if not log_channel:
            return

        account_age = datetime.now(timezone.utc) - member.created_at
        if account_age < timedelta(days=threshold_days):
            embed = discord.Embed(
                title="⚠️ New Account Alert",
                description=f"{member.mention} joined with a very new account.",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:F>", inline=True)
            embed.add_field(name="Account Age", value=f"{account_age.days} day(s)", inline=True)
            embed.set_footer(text=f"Threshold: {threshold_days} day(s)")

            try:
                await log_channel.send(embed=embed)
            except discord.Forbidden:
                print(f"[ERROR] Cannot send to {log_channel.name} in {member.guild.name}.")

async def setup(bot):
    await bot.add_cog(AccountWatch(bot))
