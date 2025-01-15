import discord
from redbot.core import commands, Config

class MangaUpdates(commands.Cog):
    """
    A cog for managing manga update notification roles.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567892)
        self.config.register_guild(
            reaction_roles={
                "op-updates": "🍖",  # One Piece emoji
                "opm-updates": "👊",  # One Punch Man emoji
            },
            message_id=None,
        )

    @commands.command(name="mangaupdates")
    @commands.admin_or_permissions(manage_roles=True)
    async def manga_updates(self, ctx):
        """
        Create an embed for manga update roles.
        """
        guild = ctx.guild
        reaction_roles = await self.config.guild(guild).reaction_roles()

        embed = discord.Embed(
            title="Manga Update Notifications",
            description="React to the emojis below to subscribe to manga updates!",
            color=discord.Color.orange(),
        )

        for role_name, emoji in reaction_roles.items():
            embed.add_field(
                name=role_name.replace("-", " ").title(),
                value=f"React with {emoji} to get `{role_name}` updates.",
                inline=False,
            )

        message = await ctx.send(embed=embed)

        # Add reactions to the message
        for emoji in reaction_roles.values():
            await message.add_reaction(emoji)

        # Save the message ID for tracking reactions
        await self.config.guild(guild).message_id.set(message.id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """
        Assign a manga update role when a user reacts and send a DM notification.
        """
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        message_id = await self.config.guild(guild).message_id()

        # Check if the reaction is on the tracked message
        if payload.message_id != message_id:
            return

        # Assign the corresponding role
        for role_name, emoji in reaction_roles.items():
            if str(payload.emoji) == emoji:
                role = discord.utils.get(guild.roles, name=role_name)
                if not role:
                    role = await self.create_role(guild, role_name)

                if role:
                    try:
                        await user.add_roles(role)
                        try:
                            # Send a DM notification
                            await user.send(f"You have been subscribed to `{role_name}` updates!")
                        except discord.Forbidden:
                            pass  # User has DMs disabled
                    except discord.Forbidden:
                        pass  # Bot lacks permissions
                    except discord.HTTPException:
                        pass  # Something went wrong
                break

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """
        Remove a manga update role when a user removes their reaction and send a DM notification.
        """
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return

        reaction_roles = await self.config.guild(guild).reaction_roles()
        message_id = await self.config.guild(guild).message_id()

        # Check if the reaction is on the tracked message
        if payload.message_id != message_id:
            return

        # Remove the corresponding role
        for role_name, emoji in reaction_roles.items():
            if str(payload.emoji) == emoji:
                role = discord.utils.get(guild.roles, name=role_name)
                if role:
                    try:
                        await user.remove_roles(role)
                        try:
                            # Send a DM notification
                            await user.send(f"You have been unsubscribed from `{role_name}` updates.")
                        except discord.Forbidden:
                            pass  # User has DMs disabled
                    except discord.Forbidden:
                        pass  # Bot lacks permissions
                    except discord.HTTPException:
                        pass  # Something went wrong
                break

    async def create_role(self, guild, role_name):
        """
        Create a role if it doesn't exist.
        """
        try:
            role = await guild.create_role(name=role_name, mentionable=True)
            return role
        except discord.Forbidden:
            pass  # Bot lacks permissions
        except discord.HTTPException:
            pass  # Something went wrong
        return None

# Setup function for Redbot
async def setup(bot):
    await bot.add_cog(MangaUpdates(bot))
