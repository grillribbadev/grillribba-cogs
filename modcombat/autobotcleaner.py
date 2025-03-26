import discord
from redbot.core import commands
from typing import Union


class AutoBotCleaner(commands.Cog):
    """Delete all messages from a user and mute them permanently with the 'Prisoner' role."""

    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.admin_or_permissions(manage_messages=True, manage_roles=True)
    @commands.command(name="clean")
    async def clean_user(self, ctx: commands.Context, target: Union[discord.Member, int]):
        """
        Delete all messages from a user in the server and permanently mute them with the 'Prisoner' role.

        Usage:
        .clean @user
        .clean <user_id>
        """
        guild = ctx.guild

        # Convert user ID to Member object if needed
        if isinstance(target, int):
            target = guild.get_member(target)
            if not target:
                return await ctx.send("❌ That user is not currently in the server.")

        await ctx.send(f"🔍 Searching and deleting messages from {target.mention}...")

        deleted_count = 0
        for channel in guild.text_channels:
            try:
                async for message in channel.history(limit=None):
                    if message.author.id == target.id:
                        await message.delete()
                        deleted_count += 1
            except (discord.Forbidden, discord.HTTPException):
                # Skip channels the bot doesn't have access to
                continue

        await ctx.send(f"✅ Deleted {deleted_count} messages from {target.mention}.")

        # Look for or create the 'Prisoner' mute role
        prisoner_role = discord.utils.get(guild.roles, name="Prisoner")
        if not prisoner_role:
            prisoner_role = await guild.create_role(name="Prisoner", reason="AutoBotCleaner mute role")
            for channel in guild.channels:
                try:
                    await channel.set_permissions(prisoner_role, send_messages=False, speak=False)
                except discord.Forbidden:
                    pass  # Ignore if we can't set perms on a channel

        # Apply the role
        try:
            await target.add_roles(prisoner_role, reason="Permanently muted by AutoBotCleaner")
            await ctx.send(f"🔇 {target.mention} has been permanently muted with the 'Prisoner' role.")
        except discord.Forbidden:
            await ctx.send("⚠️ I don't have permission to add roles to this user.")
