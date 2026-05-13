# levelrequests/levelrequests.py

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


DEFAULT_GUILD: Dict[str, Any] = {
    "request_channel_id": None,
    "proof_channel_id": None,
    "log_channel_id": None,
    "admin_role_id": None,
    "delete_delay": 5,
    "dm_timeout": 600,
    "next_request_id": 1,
    "requests": {},
}


class LevelRequests(commands.Cog):
    """Configurable level request proof system."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=0x1199AA55CC33,
            force_registration=True,
        )
        self.config.register_guild(**DEFAULT_GUILD)

    async def red_delete_data_for_user(self, **kwargs):
        return

    async def _get_channel(
        self, guild: discord.Guild, channel_id: Optional[int]
    ) -> Optional[discord.TextChannel]:
        if not channel_id:
            return None

        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel

        try:
            fetched = await guild.fetch_channel(channel_id)
        except discord.HTTPException:
            return None

        return fetched if isinstance(fetched, discord.TextChannel) else None

    async def _get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        settings = await self.config.guild(guild).all()
        return await self._get_channel(guild, settings.get("log_channel_id"))

    async def _log(self, guild: discord.Guild, message: str):
        channel = await self._get_log_channel(guild)
        if channel:
            await channel.send(message)

    async def _is_request_admin(self, ctx: commands.Context) -> bool:
        if await self.bot.is_owner(ctx.author):
            return True

        if not ctx.guild:
            return False

        if ctx.author.guild_permissions.manage_guild:
            return True

        settings = await self.config.guild(ctx.guild).all()
        role_id = settings.get("admin_role_id")

        if role_id and isinstance(ctx.author, discord.Member):
            return any(role.id == role_id for role in ctx.author.roles)

        return False

    def _image_from_message(self, message: discord.Message) -> Optional[str]:
        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if content_type.startswith("image/"):
                return attachment.url

            lower_name = attachment.filename.lower()
            if lower_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                return attachment.url

        for embed in message.embeds:
            if embed.image and embed.image.url:
                return embed.image.url
            if embed.thumbnail and embed.thumbnail.url:
                return embed.thumbnail.url

        return None

    @commands.group(name="levelreqset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def levelreqset(self, ctx: commands.Context):
        """Configure level request settings."""

    @levelreqset.command(name="requestchannel")
    async def set_request_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """Set the channel where users run requestlevel."""
        await self.config.guild(ctx.guild).request_channel_id.set(channel.id)
        await ctx.send(f"Request channel set to {channel.mention}.")

    @levelreqset.command(name="proofchannel")
    async def set_proof_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """Set the staff channel where proof embeds are posted."""
        await self.config.guild(ctx.guild).proof_channel_id.set(channel.id)
        await ctx.send(f"Proof channel set to {channel.mention}.")

    @levelreqset.command(name="logchannel")
    async def set_log_channel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Set or clear the log channel."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id if channel else None)

        if channel:
            await ctx.send(f"Log channel set to {channel.mention}.")
        else:
            await ctx.send("Log channel cleared.")

    @levelreqset.command(name="adminrole")
    async def set_admin_role(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """Set or clear the staff role allowed to accept/deny requests."""
        await self.config.guild(ctx.guild).admin_role_id.set(role.id if role else None)

        if role:
            await ctx.send(f"Admin role set to {role.mention}.")
        else:
            await ctx.send("Admin role cleared. Manage Server/admins can still handle requests.")

    @levelreqset.command(name="deletedelay")
    async def set_delete_delay(self, ctx: commands.Context, seconds: int):
        """Set how fast the request command message is deleted."""
        if seconds < 0 or seconds > 60:
            return await ctx.send("Delete delay must be between 0 and 60 seconds.")

        await self.config.guild(ctx.guild).delete_delay.set(seconds)
        await ctx.send(f"Delete delay set to `{seconds}` seconds.")

    @levelreqset.command(name="dmtimeout")
    async def set_dm_timeout(self, ctx: commands.Context, seconds: int):
        """Set how long the bot waits for proof in DMs."""
        if seconds < 30 or seconds > 3600:
            return await ctx.send("DM timeout must be between 30 and 3600 seconds.")

        await self.config.guild(ctx.guild).dm_timeout.set(seconds)
        await ctx.send(f"DM timeout set to `{seconds}` seconds.")

    @levelreqset.command(name="show")
    async def show_settings(self, ctx: commands.Context):
        """Show current level request settings."""
        settings = await self.config.guild(ctx.guild).all()

        request_channel = await self._get_channel(ctx.guild, settings["request_channel_id"])
        proof_channel = await self._get_channel(ctx.guild, settings["proof_channel_id"])
        log_channel = await self._get_channel(ctx.guild, settings["log_channel_id"])

        admin_role = ctx.guild.get_role(settings["admin_role_id"]) if settings["admin_role_id"] else None

        embed = discord.Embed(
            title="Level Request Settings",
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="Request channel",
            value=request_channel.mention if request_channel else "Not set",
            inline=False,
        )
        embed.add_field(
            name="Proof channel",
            value=proof_channel.mention if proof_channel else "Not set",
            inline=False,
        )
        embed.add_field(
            name="Log channel",
            value=log_channel.mention if log_channel else "Not set",
            inline=False,
        )
        embed.add_field(
            name="Admin role",
            value=admin_role.mention if admin_role else "Not set",
            inline=False,
        )
        embed.add_field(name="Delete delay", value=f"{settings['delete_delay']}s")
        embed.add_field(name="DM timeout", value=f"{settings['dm_timeout']}s")
        embed.add_field(name="Next request ID", value=str(settings["next_request_id"]))

        await ctx.send(embed=embed)

    @commands.command(name="requestlevel")
    @commands.guild_only()
    async def request_level(self, ctx: commands.Context):
        """Request a level-up by DMing proof to the bot."""
        settings = await self.config.guild(ctx.guild).all()

        request_channel_id = settings["request_channel_id"]
        proof_channel_id = settings["proof_channel_id"]

        if not request_channel_id or not proof_channel_id:
            return await ctx.send("Level requests are not configured yet.")

        if ctx.channel.id != request_channel_id:
            request_channel = await self._get_channel(ctx.guild, request_channel_id)
            return await ctx.send(
                f"Use this command in {request_channel.mention if request_channel else 'the configured request channel'}."
            )

        delete_delay = settings["delete_delay"]

        try:
            await ctx.message.delete(delay=delete_delay)
        except discord.HTTPException:
            pass

        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                "Please send your level proof image here.\n"
                "You can upload a screenshot/photo directly in this DM."
            )
        except discord.Forbidden:
            return await ctx.send(
                f"{ctx.author.mention}, I could not DM you. Please enable DMs from server members.",
                delete_after=10,
            )

        def check(message: discord.Message) -> bool:
            return message.author.id == ctx.author.id and isinstance(message.channel, discord.DMChannel)

        try:
            proof_message = await self.bot.wait_for(
                "message",
                timeout=settings["dm_timeout"],
                check=check,
            )
        except asyncio.TimeoutError:
            await dm.send("Level request cancelled because no proof image was received in time.")
            return

        image_url = self._image_from_message(proof_message)

        if not image_url:
            await dm.send("I did not find an image. Please run the request command again and send an image attachment.")
            return

        proof_channel = await self._get_channel(ctx.guild, proof_channel_id)

        if not proof_channel:
            await dm.send("The proof channel is missing or invalid. Please contact staff.")
            return

        async with self.config.guild(ctx.guild).all() as data:
            request_id = data["next_request_id"]
            data["next_request_id"] += 1

            request_data = {
                "id": request_id,
                "user_id": ctx.author.id,
                "status": "pending",
                "image_url": image_url,
                "proof_message_id": proof_message.id,
                "proof_channel_message_id": None,
                "created_at": int(discord.utils.utcnow().timestamp()),
                "handled_by": None,
                "decision_comment": None,
            }

            data["requests"][str(request_id)] = request_data

        embed = discord.Embed(
            title=f"Level Request #{request_id}",
            description=f"User: {ctx.author.mention}\nUser ID: `{ctx.author.id}`",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=image_url)
        embed.set_footer(text=f"Use reqaccept {request_id} or reqdeny {request_id} <reason>")

        staff_message = await proof_channel.send(
            content=f"New level request from {ctx.author.mention}",
            embed=embed,
        )

        async with self.config.guild(ctx.guild).requests() as requests:
            if str(request_id) in requests:
                requests[str(request_id)]["proof_channel_message_id"] = staff_message.id

        await dm.send(
            f"Your proof was submitted properly.\n"
            f"Request ID: `#{request_id}`\n"
            f"Staff will review it soon.",
            embed=discord.Embed(description="Submitted proof:").set_image(url=image_url),
        )

        await self._log(ctx.guild, f"Level request `#{request_id}` submitted by {ctx.author.mention}.")

    async def _get_request(
        self, guild: discord.Guild, request_id: int
    ) -> Optional[Dict[str, Any]]:
        requests = await self.config.guild(guild).requests()
        return requests.get(str(request_id))

    async def _update_staff_embed(
        self,
        guild: discord.Guild,
        request_data: Dict[str, Any],
        status: str,
        moderator: discord.Member,
        comment: Optional[str] = None,
    ):
        settings = await self.config.guild(guild).all()
        proof_channel = await self._get_channel(guild, settings["proof_channel_id"])

        if not proof_channel:
            return

        message_id = request_data.get("proof_channel_message_id")
        if not message_id:
            return

        try:
            message = await proof_channel.fetch_message(message_id)
        except discord.HTTPException:
            return

        color = discord.Color.green() if status == "accepted" else discord.Color.red()

        user = guild.get_member(request_data["user_id"])
        user_text = user.mention if user else f"`{request_data['user_id']}`"

        embed = discord.Embed(
            title=f"Level Request #{request_data['id']} - {status.upper()}",
            description=f"User: {user_text}\nUser ID: `{request_data['user_id']}`",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=request_data["image_url"])
        embed.add_field(name="Handled by", value=moderator.mention, inline=False)

        if comment:
            embed.add_field(name="Reason/comment", value=comment, inline=False)

        await message.edit(embed=embed)

    @commands.command(name="reqaccept")
    @commands.guild_only()
    async def request_accept(self, ctx: commands.Context, request_id: int):
        """Accept a level request."""
        if not await self._is_request_admin(ctx):
            return await ctx.send("You do not have permission to accept level requests.")

        request_data = await self._get_request(ctx.guild, request_id)

        if not request_data:
            return await ctx.send(f"No request found with ID `{request_id}`.")

        if request_data["status"] != "pending":
            return await ctx.send(
                f"Request `#{request_id}` is already `{request_data['status']}`."
            )

        async with self.config.guild(ctx.guild).requests() as requests:
            requests[str(request_id)]["status"] = "accepted"
            requests[str(request_id)]["handled_by"] = ctx.author.id
            requests[str(request_id)]["decision_comment"] = None
            request_data = requests[str(request_id)]

        member = ctx.guild.get_member(request_data["user_id"])

        if member:
            try:
                await member.send(
                    f"Your level request `#{request_id}` was accepted.",
                    embed=discord.Embed(description="Accepted proof:").set_image(
                        url=request_data["image_url"]
                    ),
                )
            except discord.HTTPException:
                pass

        await self._update_staff_embed(ctx.guild, request_data, "accepted", ctx.author)
        await ctx.send(f"Accepted level request `#{request_id}`.")
        await self._log(ctx.guild, f"Level request `#{request_id}` accepted by {ctx.author.mention}.")

    @commands.command(name="reqdeny")
    @commands.guild_only()
    async def request_deny(
        self,
        ctx: commands.Context,
        request_id: int,
        *,
        reason: str,
    ):
        """Deny a level request with a reason sent to the user."""
        if not await self._is_request_admin(ctx):
            return await ctx.send("You do not have permission to deny level requests.")

        request_data = await self._get_request(ctx.guild, request_id)

        if not request_data:
            return await ctx.send(f"No request found with ID `{request_id}`.")

        if request_data["status"] != "pending":
            return await ctx.send(
                f"Request `#{request_id}` is already `{request_data['status']}`."
            )

        async with self.config.guild(ctx.guild).requests() as requests:
            requests[str(request_id)]["status"] = "denied"
            requests[str(request_id)]["handled_by"] = ctx.author.id
            requests[str(request_id)]["decision_comment"] = reason
            request_data = requests[str(request_id)]

        member = ctx.guild.get_member(request_data["user_id"])

        if member:
            try:
                await member.send(
                    f"Your level request `#{request_id}` was denied.\n\n"
                    f"Reason: {reason}",
                    embed=discord.Embed(description="Denied proof:").set_image(
                        url=request_data["image_url"]
                    ),
                )
            except discord.HTTPException:
                pass

        await self._update_staff_embed(ctx.guild, request_data, "denied", ctx.author, reason)
        await ctx.send(f"Denied level request `#{request_id}`.")
        await self._log(ctx.guild, f"Level request `#{request_id}` denied by {ctx.author.mention}. Reason: {reason}")

    @commands.command(name="reqstatus")
    @commands.guild_only()
    async def request_status(self, ctx: commands.Context, request_id: int):
        """Check the status of a request."""
        request_data = await self._get_request(ctx.guild, request_id)

        if not request_data:
            return await ctx.send(f"No request found with ID `{request_id}`.")

        user = ctx.guild.get_member(request_data["user_id"])
        handled_by = ctx.guild.get_member(request_data["handled_by"]) if request_data.get("handled_by") else None

        embed = discord.Embed(
            title=f"Level Request #{request_id}",
            color=await ctx.embed_color(),
        )
        embed.add_field(name="Status", value=request_data["status"], inline=False)
        embed.add_field(
            name="User",
            value=user.mention if user else f"`{request_data['user_id']}`",
            inline=False,
        )
        embed.add_field(
            name="Handled by",
            value=handled_by.mention if handled_by else "Not handled yet",
            inline=False,
        )

        if request_data.get("decision_comment"):
            embed.add_field(
                name="Comment",
                value=request_data["decision_comment"],
                inline=False,
            )

        embed.set_image(url=request_data["image_url"])
        await ctx.send(embed=embed)