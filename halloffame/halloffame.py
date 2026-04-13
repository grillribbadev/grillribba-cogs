from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


DEFAULT_GUILD: Dict[str, Any] = {
    "target_channel_id": None,
    "emoji": "⭐",
    "threshold": 5,
    "posts": {},
}


class HallOfFame(commands.Cog):
    """Starboard-style hall of fame with configurable channel, emoji, and threshold."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x48A11F4D, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)

    async def red_delete_data_for_user(self, **kwargs):
        return

    @commands.group(name="halloffame", aliases=["hof"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def halloffame(self, ctx: commands.Context):
        """Configure hall of fame settings."""
        if ctx.invoked_subcommand is None:
            await self.hof_settings(ctx)

    @halloffame.command(name="setchannel")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def hof_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the target channel where hall of fame posts are sent."""
        await self.config.guild(ctx.guild).target_channel_id.set(channel.id)
        await ctx.send(f"Hall of Fame target channel set to {channel.mention}.")

    @halloffame.command(name="setemoji")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def hof_setemoji(self, ctx: commands.Context, *, emoji: str):
        """Set the trigger emoji (unicode or custom server emoji)."""
        key, _, error = self._resolve_emoji(ctx.guild, emoji)
        if error:
            await ctx.send(error)
            return

        await self.config.guild(ctx.guild).emoji.set(key)
        await ctx.send(f"Hall of Fame emoji set to {key}")

    @halloffame.command(name="setthreshold")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def hof_setthreshold(self, ctx: commands.Context, threshold: int):
        """Set how many valid reactions are required to post."""
        if threshold < 1:
            await ctx.send("Threshold must be at least 1.")
            return

        await self.config.guild(ctx.guild).threshold.set(int(threshold))
        await ctx.send(f"Hall of Fame threshold set to {int(threshold)}.")

    @halloffame.command(name="settings")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def hof_settings(self, ctx: commands.Context):
        """Show current hall of fame settings."""
        data = await self.config.guild(ctx.guild).all()
        channel_id = data.get("target_channel_id")
        threshold = int(data.get("threshold", 5))
        emoji = data.get("emoji", "⭐")

        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        channel_display = channel.mention if isinstance(channel, discord.TextChannel) else "Not set"

        await ctx.send(
            f"Hall of Fame settings:\n"
            f"Channel: {channel_display}\n"
            f"Emoji: {emoji}\n"
            f"Threshold: {threshold}"
        )

    @halloffame.command(name="resetposts")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def hof_resetposts(self, ctx: commands.Context):
        """Clear tracked source->hall-of-fame mappings."""
        await self.config.guild(ctx.guild).posts.set({})
        await ctx.send("Cleared tracked Hall of Fame post mappings.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._process_reaction_payload(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._process_reaction_payload(payload)

    async def _process_reaction_payload(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        if payload.user_id == self.bot.user.id:
            return

        settings = await self.config.guild(guild).all()
        target_channel_id = settings.get("target_channel_id")
        target_channel = guild.get_channel(target_channel_id) if target_channel_id else None
        if not isinstance(target_channel, discord.TextChannel):
            return

        configured_emoji = settings.get("emoji", "⭐")
        if not self._emoji_matches(configured_emoji, payload.emoji):
            return

        source_channel = guild.get_channel(payload.channel_id)
        if not isinstance(source_channel, discord.TextChannel):
            return

        try:
            source_message = await source_channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        count = await self._count_valid_reactions(source_message, configured_emoji)
        threshold = int(settings.get("threshold", 5))

        posts = settings.get("posts", {})
        source_id = str(source_message.id)
        existing = posts.get(source_id)

        if count < threshold:
            return

        content = self._build_starboard_content(source_message, configured_emoji, count)
        embed = await self._build_starboard_embed(source_message, configured_emoji, count)

        if existing:
            starboard_channel = guild.get_channel(existing.get("starboard_channel_id", 0))
            if isinstance(starboard_channel, discord.TextChannel):
                try:
                    starboard_msg = await starboard_channel.fetch_message(existing["starboard_message_id"])
                    await starboard_msg.edit(content=content, embed=embed)
                    posts[source_id]["last_count"] = count
                    await self.config.guild(guild).posts.set(posts)
                    return
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, KeyError):
                    pass

        try:
            sent = await target_channel.send(content=content, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

        posts[source_id] = {
            "starboard_message_id": sent.id,
            "starboard_channel_id": sent.channel.id,
            "last_count": count,
        }
        await self.config.guild(guild).posts.set(posts)

    async def _count_valid_reactions(self, message: discord.Message, configured_emoji: str) -> int:
        for reaction in message.reactions:
            if not self._emoji_matches(configured_emoji, reaction.emoji):
                continue

            unique_non_bot_ids = set()
            async for user in reaction.users(limit=None):
                if user.bot:
                    continue
                if user.id == message.author.id:
                    continue
                unique_non_bot_ids.add(user.id)
            return len(unique_non_bot_ids)

        return 0

    def _build_starboard_content(self, message: discord.Message, emoji: str, count: int) -> str:
        return f"{emoji} **{count}** | {message.channel.mention} | [Jump to message]({message.jump_url})"

    async def _build_starboard_embed(self, message: discord.Message, emoji: str, count: int) -> discord.Embed:
        embed = discord.Embed(
            description=message.content or "[No text content]",
            color=discord.Color.gold(),
            timestamp=message.created_at,
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Reacts", value=f"{emoji} {count}", inline=True)

        if message.reference and message.reference.message_id:
            ref_text = "Unable to load replied-to message"
            ref_channel = message.channel
            if message.reference.channel_id:
                maybe_channel = message.guild.get_channel(message.reference.channel_id)
                if isinstance(maybe_channel, discord.TextChannel):
                    ref_channel = maybe_channel
            try:
                replied = await ref_channel.fetch_message(message.reference.message_id)
                preview = replied.content.strip() if replied.content else "[No text content]"
                if len(preview) > 180:
                    preview = preview[:177] + "..."
                ref_text = f"Replying to {replied.author.mention}: {preview}"
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            embed.add_field(name="Reply Context", value=ref_text, inline=False)

        media_urls = self._collect_media_urls(message)
        if media_urls:
            embed.set_image(url=media_urls[0])
            if len(media_urls) > 1:
                extras = "\n".join(media_urls[1:6])
                embed.add_field(name="More Media", value=extras, inline=False)

        embed.set_footer(text=f"Message ID: {message.id}")
        return embed

    def _collect_media_urls(self, message: discord.Message) -> List[str]:
        urls: List[str] = []

        for attachment in message.attachments:
            if self._looks_like_image_or_gif(attachment.url, attachment.content_type):
                urls.append(attachment.url)

        for emb in message.embeds:
            if emb.image and emb.image.url and self._looks_like_image_or_gif(emb.image.url, None):
                urls.append(emb.image.url)
            if emb.thumbnail and emb.thumbnail.url and self._looks_like_image_or_gif(emb.thumbnail.url, None):
                urls.append(emb.thumbnail.url)
            if emb.video and emb.video.url and self._looks_like_image_or_gif(emb.video.url, None):
                urls.append(emb.video.url)
            if emb.url and self._looks_like_image_or_gif(emb.url, None):
                urls.append(emb.url)

        deduped = []
        seen = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    @staticmethod
    def _looks_like_image_or_gif(url: Optional[str], content_type: Optional[str]) -> bool:
        if content_type and (content_type.startswith("image/") or content_type == "image/gif"):
            return True

        if not url:
            return False

        lowered = url.lower()
        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".apng")
        if any(ext in lowered for ext in image_exts):
            return True

        gif_hosts = ("tenor.com", "giphy.com", "media.discordapp.net", "cdn.discordapp.com")
        return any(host in lowered for host in gif_hosts)

    def _resolve_emoji(self, guild: discord.Guild, raw: str) -> Tuple[Optional[str], Optional[object], Optional[str]]:
        text = (raw or "").strip()
        if not text:
            return None, None, "Emoji is required."

        if text.startswith(":") and text.endswith(":") and len(text) >= 3:
            name = text.strip(":")
            matches = [e for e in guild.emojis if e.name.lower() == name.lower()]
            if len(matches) == 1:
                chosen = matches[0]
                return str(chosen), chosen, None
            if len(matches) > 1:
                return None, None, "Multiple emojis with that name found. Use a full emoji mention like <:name:id>."

        try:
            pe = discord.PartialEmoji.from_str(text)
        except Exception:
            pe = None

        if pe is None:
            return text, text, None

        if pe.id:
            if guild.get_emoji(pe.id) is None:
                return None, None, "Custom emoji must be from this server."
            return str(pe), pe, None

        if pe.name:
            return pe.name, pe.name, None

        return None, None, "Could not parse emoji."

    @staticmethod
    def _emoji_matches(configured: str, incoming_emoji: object) -> bool:
        cfg_text = str(configured).strip()

        try:
            cfg_pe = discord.PartialEmoji.from_str(cfg_text)
        except Exception:
            cfg_pe = None

        incoming_text = str(incoming_emoji)

        if cfg_pe and cfg_pe.id:
            incoming_id = getattr(incoming_emoji, "id", None)
            return bool(incoming_id and int(incoming_id) == int(cfg_pe.id))

        incoming_name = getattr(incoming_emoji, "name", None)
        if cfg_pe and cfg_pe.name:
            return cfg_pe.name == incoming_name or cfg_pe.name == incoming_text

        return cfg_text == incoming_text
