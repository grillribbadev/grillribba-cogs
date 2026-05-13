from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


DEFAULT_GUILD: Dict[str, Any] = {
    "request_channel_id": None,
    "request_channel_ids": [],
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

    async def _get_request_channel_ids(self, guild: discord.Guild) -> List[int]:
        settings = await self.config.guild(guild).all()
        channel_ids = list(settings.get("request_channel_ids") or [])

        old_id = settings.get("request_channel_id")
        if old_id and old_id not in channel_ids:
            channel_ids.append(old_id)

        return channel_ids

    async def _is_request_channel(self, guild: discord.Guild, channel_id: int) -> bool:
        return channel_id in await self._get_request_channel_ids(guild)

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if not await self._is_request_channel(message.guild, message.channel.id):
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass

    async def cog_check(self, ctx: commands.Context) -> bool:
        if not ctx.guild or not ctx.command:
            return True

        if await self._is_request_channel(ctx.guild, ctx.channel.id):
            return ctx.command.name == "requestlevel"

        return True

    async def _get_channel(self, guild: discord.Guild, channel_id: Optional[int]):
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

    async def _send_request_info_message(self, channel: discord.TextChannel):
        embed = discord.Embed(
            title="Level Restoration Requests",
            description=(
                "Use `.requestlevel` to begin your level restoration request.\n\n"
                "The bot will DM you automatically so you can upload your proof screenshot/image."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Important",
            value=(
                "• Only use `.requestlevel` here\n"
                "• All other messages are deleted automatically\n"
                "• Staff will manually review your proof"
            ),
            inline=False,
        )
        embed.set_footer(text="All requests are manually reviewed by staff.")

        await channel.send(embed=embed)

    async def _log(self, guild: discord.Guild, message: str):
        settings = await self.config.guild(guild).all()
        channel = await self._get_channel(guild, settings.get("log_channel_id"))

        if channel:
            await channel.send(message)

    async def _is_request_admin(self, ctx: commands.Context) -> bool:
        if await self.bot.is_owner(ctx.author):
            return True

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

            if attachment.filename.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
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
    async def set_request_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Add a request channel and post the info message."""

        async with self.config.guild(ctx.guild).all() as data:
            channel_ids = list(data.get("request_channel_ids") or [])

            old_id = data.get("request_channel_id")
            if old_id and old_id not in channel_ids:
                channel_ids.append(old_id)

            if channel.id not in channel_ids:
                channel_ids.append(channel.id)

            data["request_channel_ids"] = channel_ids
            data["request_channel_id"] = channel.id

        deleted = 0

        try:
            async for message in channel.history(limit=500):
                try:
                    await message.delete()
                    deleted += 1
                except discord.HTTPException:
                    pass
        except discord.HTTPException:
            pass

        try:
            await self._send_request_info_message(channel)
        except discord.HTTPException:
            return await ctx.send(
                f"Request channel was saved, but I could not post the info message in {channel.mention}."
            )

        await ctx.send(
            f"Request channel added: {channel.mention}\n"
            f"Deleted `{deleted}` old messages and posted the request info message."
        )

    @levelreqset.command(
        name="removerequestchannel",
        aliases=["delrequestchannel", "rmrequestchannel"],
    )
    async def remove_request_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a request channel."""

        async with self.config.guild(ctx.guild).all() as data:
            channel_ids = list(data.get("request_channel_ids") or [])

            old_id = data.get("request_channel_id")
            if old_id and old_id not in channel_ids:
                channel_ids.append(old_id)

            if channel.id in channel_ids:
                channel_ids.remove(channel.id)

            data["request_channel_ids"] = channel_ids

            if data.get("request_channel_id") == channel.id:
                data["request_channel_id"] = channel_ids[0] if channel_ids else None

        await ctx.send(f"Removed request channel: {channel.mention}")

    @levelreqset.command(name="clearrequestchannels")
    async def clear_request_channels(self, ctx: commands.Context):
        """Clear all request channels."""

        await self.config.guild(ctx.guild).request_channel_ids.set([])
        await self.config.guild(ctx.guild).request_channel_id.set(None)

        await ctx.send("All request channels cleared.")

    @levelreqset.command(name="postmessage")
    async def post_request_message(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Post the request info message again."""

        if channel is None:
            channel = ctx.channel

        if not await self._is_request_channel(ctx.guild, channel.id):
            return await ctx.send("That channel is not configured as a request channel.")

        await self._send_request_info_message(channel)
        await ctx.send(f"Posted the request info message in {channel.mention}.")

    @levelreqset.command(name="proofchannel")
    async def set_proof_channel(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Set or clear the proof/staff channel where threads are created."""

        await self.config.guild(ctx.guild).proof_channel_id.set(channel.id if channel else None)

        if channel:
            await ctx.send(f"Proof thread parent channel set to {channel.mention}.")
        else:
            await ctx.send("Proof channel cleared.")

    @levelreqset.command(name="logchannel")
    async def set_log_channel(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Set or clear the log channel."""

        await self.config.guild(ctx.guild).log_channel_id.set(channel.id if channel else None)

        if channel:
            await ctx.send(f"Log channel set to {channel.mention}.")
        else:
            await ctx.send("Log channel cleared.")

    @levelreqset.command(name="adminrole")
    async def set_admin_role(
        self,
        ctx: commands.Context,
        role: Optional[discord.Role] = None,
    ):
        """Set or clear the admin role."""

        await self.config.guild(ctx.guild).admin_role_id.set(role.id if role else None)

        if role:
            await ctx.send(f"Admin role set to {role.mention}.")
        else:
            await ctx.send("Admin role cleared.")

    @levelreqset.command(name="deletedelay")
    async def set_delete_delay(self, ctx: commands.Context, seconds: int):
        """Set requestlevel delete delay."""

        if seconds < 0 or seconds > 60:
            return await ctx.send("Delete delay must be between 0 and 60 seconds.")

        await self.config.guild(ctx.guild).delete_delay.set(seconds)
        await ctx.send(f"Delete delay set to `{seconds}` seconds.")

    @levelreqset.command(name="dmtimeout")
    async def set_dm_timeout(self, ctx: commands.Context, seconds: int):
        """Set DM proof timeout."""

        if seconds < 30 or seconds > 3600:
            return await ctx.send("DM timeout must be between 30 and 3600 seconds.")

        await self.config.guild(ctx.guild).dm_timeout.set(seconds)
        await ctx.send(f"DM timeout set to `{seconds}` seconds.")

    @levelreqset.command(name="show")
    async def show_settings(self, ctx: commands.Context):
        """Show settings."""

        settings = await self.config.guild(ctx.guild).all()
        request_channel_ids = await self._get_request_channel_ids(ctx.guild)

        request_channels = []
        for channel_id in request_channel_ids:
            channel = await self._get_channel(ctx.guild, channel_id)
            request_channels.append(channel.mention if channel else f"`Missing channel: {channel_id}`")

        proof_channel = await self._get_channel(ctx.guild, settings["proof_channel_id"])
        log_channel = await self._get_channel(ctx.guild, settings["log_channel_id"])
        admin_role = ctx.guild.get_role(settings["admin_role_id"]) if settings["admin_role_id"] else None

        embed = discord.Embed(
            title="Level Request Settings",
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="Request channels",
            value="\n".join(request_channels) if request_channels else "Not set",
            inline=False,
        )
        embed.add_field(
            name="Proof thread parent channel",
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
        """Request level restoration."""

        settings = await self.config.guild(ctx.guild).all()
        request_channel_ids = await self._get_request_channel_ids(ctx.guild)

        if not request_channel_ids or not settings["proof_channel_id"]:
            return await ctx.send("Level requests are not configured yet.", delete_after=10)

        if ctx.channel.id not in request_channel_ids:
            channels = []
            for channel_id in request_channel_ids:
                channel = await self._get_channel(ctx.guild, channel_id)
                if channel:
                    channels.append(channel.mention)

            return await ctx.send(
                f"Use this command in {', '.join(channels) if channels else 'the configured request channel'}.",
                delete_after=10,
            )

        try:
            await ctx.message.delete(delay=settings["delete_delay"])
        except discord.HTTPException:
            pass

        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                "Please send your level proof image here.\n"
                "Upload a screenshot/photo directly in this DM."
            )
        except discord.Forbidden:
            return await ctx.send(
                f"{ctx.author.mention}, I could not DM you. Please enable DMs from server members.",
                delete_after=10,
            )

        def check(message: discord.Message) -> bool:
            return (
                message.author.id == ctx.author.id
                and isinstance(message.channel, discord.DMChannel)
            )

        try:
            proof_message = await self.bot.wait_for(
                "message",
                timeout=settings["dm_timeout"],
                check=check,
            )
        except asyncio.TimeoutError:
            return await dm.send("Level request cancelled because no proof image was received in time.")

        image_url = self._image_from_message(proof_message)

        if not image_url:
            return await dm.send(
                "I did not find an image. Please run `.requestlevel` again and send an image attachment."
            )

        proof_channel = await self._get_channel(ctx.guild, settings["proof_channel_id"])

        if not proof_channel:
            return await dm.send("The proof channel is missing or invalid. Please contact staff.")

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
                "proof_thread_id": None,
                "created_at": int(discord.utils.utcnow().timestamp()),
                "handled_by": None,
                "decision_comment": None,
            }

            data["requests"][str(request_id)] = request_data

        thread_name = f"level-request-{request_id}-{ctx.author.name}"[:100]

        try:
            thread = await proof_channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason=f"Level request #{request_id} by {ctx.author} ({ctx.author.id})",
            )
        except discord.Forbidden:
            return await dm.send(
                "I could not create a private thread in the staff channel. Please contact staff."
            )
        except discord.HTTPException:
            try:
                thread = await proof_channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.public_thread,
                    reason=f"Level request #{request_id} by {ctx.author} ({ctx.author.id})",
                )
            except discord.HTTPException:
                return await dm.send(
                    "I could not create a thread in the staff channel. Please contact staff."
                )

        embed = discord.Embed(
            title=f"Level Request #{request_id}",
            description=f"User: {ctx.author.mention}\nUser ID: `{ctx.author.id}`",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=image_url)
        embed.set_footer(text=f"Use reqaccept {request_id} or reqdeny {request_id} <reason>")

        staff_message = await thread.send(
            content=f"New level request from {ctx.author.mention}",
            embed=embed,
        )

        async with self.config.guild(ctx.guild).requests() as requests:
            requests[str(request_id)]["proof_channel_message_id"] = staff_message.id
            requests[str(request_id)]["proof_thread_id"] = thread.id

        await dm.send(
            f"Your proof was submitted properly.\nRequest ID: `#{request_id}`",
            embed=discord.Embed(description="Submitted proof:").set_image(url=image_url),
        )

        await self._log(
            ctx.guild,
            f"Level request `#{request_id}` submitted by {ctx.author.mention}. Thread: {thread.mention}",
        )

    async def _get_request(self, guild: discord.Guild, request_id: int):
        requests = await self.config.guild(guild).requests()
        return requests.get(str(request_id))

    async def _get_thread_or_channel(
        self,
        guild: discord.Guild,
        request_data: Dict[str, Any],
    ):
        thread_id = request_data.get("proof_thread_id")

        if thread_id:
            channel = guild.get_channel(thread_id)

            if channel:
                return channel

            try:
                fetched = await guild.fetch_channel(thread_id)
                return fetched
            except discord.HTTPException:
                pass

        settings = await self.config.guild(guild).all()
        return await self._get_channel(guild, settings["proof_channel_id"])

    async def _update_staff_embed(
        self,
        guild: discord.Guild,
        request_data: Dict[str, Any],
        status: str,
        moderator: discord.Member,
        comment: Optional[str] = None,
    ):
        proof_location = await self._get_thread_or_channel(guild, request_data)

        if not proof_location or not request_data.get("proof_channel_message_id"):
            return

        try:
            message = await proof_location.fetch_message(request_data["proof_channel_message_id"])
        except discord.HTTPException:
            return

        user = guild.get_member(request_data["user_id"])
        color = discord.Color.green() if status == "accepted" else discord.Color.red()

        embed = discord.Embed(
            title=f"Level Request #{request_data['id']} - {status.upper()}",
            description=(
                f"User: {user.mention if user else request_data['user_id']}\n"
                f"User ID: `{request_data['user_id']}`"
            ),
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_image(url=request_data["image_url"])
        embed.add_field(name="Handled by", value=moderator.mention, inline=False)

        if comment:
            embed.add_field(name="Reason/comment", value=comment, inline=False)

        await message.edit(embed=embed)

        if isinstance(proof_location, discord.Thread):
            try:
                await proof_location.send(
                    f"Request `#{request_data['id']}` marked as **{status}** by {moderator.mention}."
                )
            except discord.HTTPException:
                pass

    @commands.command(name="reqaccept")
    @commands.guild_only()
    async def request_accept(self, ctx: commands.Context, request_id: int):
        """Accept a request."""

        if not await self._is_request_admin(ctx):
            return await ctx.send("You do not have permission to accept level requests.")

        request_data = await self._get_request(ctx.guild, request_id)

        if not request_data:
            return await ctx.send(f"No request found with ID `{request_id}`.")

        if request_data["status"] != "pending":
            return await ctx.send(f"Request `#{request_id}` is already `{request_data['status']}`.")

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
    async def request_deny(self, ctx: commands.Context, request_id: int, *, reason: str):
        """Deny a request."""

        if not await self._is_request_admin(ctx):
            return await ctx.send("You do not have permission to deny level requests.")

        request_data = await self._get_request(ctx.guild, request_id)

        if not request_data:
            return await ctx.send(f"No request found with ID `{request_id}`.")

        if request_data["status"] != "pending":
            return await ctx.send(f"Request `#{request_id}` is already `{request_data['status']}`.")

        async with self.config.guild(ctx.guild).requests() as requests:
            requests[str(request_id)]["status"] = "denied"
            requests[str(request_id)]["handled_by"] = ctx.author.id
            requests[str(request_id)]["decision_comment"] = reason
            request_data = requests[str(request_id)]

        member = ctx.guild.get_member(request_data["user_id"])

        if member:
            try:
                await member.send(
                    f"Your level request `#{request_id}` was denied.\n\nReason: {reason}",
                    embed=discord.Embed(description="Denied proof:").set_image(
                        url=request_data["image_url"]
                    ),
                )
            except discord.HTTPException:
                pass

        await self._update_staff_embed(ctx.guild, request_data, "denied", ctx.author, reason)
        await ctx.send(f"Denied level request `#{request_id}`.")
        await self._log(
            ctx.guild,
            f"Level request `#{request_id}` denied by {ctx.author.mention}. Reason: {reason}",
        )

    @commands.command(name="reqstatus")
    @commands.guild_only()
    async def request_status(self, ctx: commands.Context, request_id: int):
        """Check a request."""

        request_data = await self._get_request(ctx.guild, request_id)

        if not request_data:
            return await ctx.send(f"No request found with ID `{request_id}`.")

        user = ctx.guild.get_member(request_data["user_id"])
        handled_by = (
            ctx.guild.get_member(request_data["handled_by"])
            if request_data.get("handled_by")
            else None
        )

        thread_id = request_data.get("proof_thread_id")
        thread = ctx.guild.get_channel(thread_id) if thread_id else None

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
        embed.add_field(
            name="Thread",
            value=thread.mention if thread else "Missing/old request",
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
