from __future__ import annotations
import discord
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional
from .constants import EMBED_OK, EMBED_ERR

class ReactRoles(commands.Cog):
    """Reaction role embeds with optional Nitro-only restrictions."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=956321478, force_registration=True)
        # message_id -> {emoji: {role_id, booster_only}, _meta: {"channel_id": int}}
        self.config.register_guild(posts={})
        self._booster_cleanup.start()

    def cog_unload(self):
        self._booster_cleanup.cancel()

    # ---------------- Background cleanup ----------------

    @tasks.loop(minutes=15)
    async def _booster_cleanup(self):
        for guild in self.bot.guilds:
            posts = await self.config.guild(guild).posts()
            booster_roles = {
                int(v["role_id"])
                for msg in posts.values()
                for k, v in msg.items()
                if k != "_meta" and v.get("booster_only")
            }
            for role_id in booster_roles:
                role = guild.get_role(role_id)
                if not role:
                    continue
                for member in role.members:
                    if not member.premium_since:
                        try:
                            await member.remove_roles(role, reason="Lost booster-only role")
                        except Exception:
                            pass

    @_booster_cleanup.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_red_ready()

    # ---------------- Commands ----------------

    @commands.group(name="rr", invoke_without_command=True)
    @commands.admin()
    async def rr(self, ctx: commands.Context):
        """Manage reaction role posts."""
        await ctx.send_help()

    @rr.command(name="create")
    async def rr_create(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        title: str,
        *,
        description: str,
    ):
        """Create a reaction role embed in a target channel."""
        emb = discord.Embed(title=title[:256], description=description[:2000], color=EMBED_OK)
        msg = await channel.send(embed=emb)
        await self.config.guild(ctx.guild).posts.set_raw(str(msg.id), value={"_meta": {"channel_id": channel.id}})
        await ctx.send(f"Reaction role embed created in {channel.mention} with ID `{msg.id}`.")

    @rr.command(name="add")
    async def rr_add(
        self,
        ctx: commands.Context,
        message_id: int,
        emoji: str,
        role: discord.Role,
        booster_only: Optional[bool] = False,
    ):
        """Bind an emoji to a role (booster_only = True/False)."""
        posts = await self.config.guild(ctx.guild).posts()
        data = posts.get(str(message_id))
        if not data:
            return await ctx.send("Message ID not found in config.")
        channel_id = data.get("_meta", {}).get("channel_id")
        channel = ctx.guild.get_channel(channel_id or ctx.channel.id)

        try:
            msg = await channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
        except Exception:
            return await ctx.send("Could not add that emoji to the message (check emoji or permissions).")

        data[str(emoji)] = {"role_id": role.id, "booster_only": booster_only}
        await self.config.guild(ctx.guild).posts.set_raw(str(message_id), value=data)
        await ctx.send(f"Added: {emoji} → {role.mention} (booster only: {booster_only})")

    @rr.command(name="remove")
    async def rr_remove(self, ctx: commands.Context, message_id: int, emoji: str):
        """Remove a reaction-role binding."""
        posts = await self.config.guild(ctx.guild).posts()
        post = posts.get(str(message_id))
        if not post or emoji not in post:
            return await ctx.send("That emoji isn’t configured.")
        del post[emoji]
        await self.config.guild(ctx.guild).posts.set_raw(str(message_id), value=post)
        await ctx.send("Binding removed.")

    @rr.command(name="list")
    async def rr_list(self, ctx: commands.Context):
        """List all reaction role messages with their embed titles."""
        posts = await self.config.guild(ctx.guild).posts()
        if not posts:
            return await ctx.send("No reaction role posts set up.")

        lines = []
        for msg_id, binds in posts.items():
            if not isinstance(binds, dict):
                continue

            channel_id = binds.get("_meta", {}).get("channel_id")
            if not channel_id:
                continue

            channel = ctx.guild.get_channel(channel_id)
            if not channel:
                lines.append(f"`{msg_id}` • *channel missing*")
                continue

            try:
                msg = await channel.fetch_message(int(msg_id))
                title = msg.embeds[0].title if msg.embeds else "(no title)"
            except discord.NotFound:
                title = "*Message not found*"
            except discord.HTTPException:
                title = "*Fetch failed*"

            emoji_count = sum(1 for k in binds if k != "_meta")
            lines.append(f"🆔 `{msg_id}` • {emoji_count} emoji(s) • **{title}**")

        await ctx.send("\n".join(lines[:10]) or "No active reaction roles found.")

    @rr.command(name="post")
    async def rr_post(
        self,
        ctx: commands.Context,
        source_message_id: int,
        channel: discord.TextChannel,
        title: Optional[str] = "React for Roles",
        description: Optional[str] = "React to get your role.",
    ):
        """Repost a reaction role embed to a new channel."""
        posts = await self.config.guild(ctx.guild).posts()
        binds = posts.get(str(source_message_id))
        if not binds:
            return await ctx.send("No bindings found for that message ID.")

        emb = discord.Embed(title=title[:256], description=description[:2000], color=EMBED_OK)
        new_msg = await channel.send(embed=emb)

        new_binds = {k: v for k, v in binds.items() if k != "_meta"}
        new_binds["_meta"] = {"channel_id": channel.id}
        await self.config.guild(ctx.guild).posts.set_raw(str(new_msg.id), value=new_binds)
        await ctx.send(f"Embed posted to {channel.mention} with ID `{new_msg.id}`.")

        for emoji in new_binds:
            if emoji == "_meta":
                continue
            try:
                await new_msg.add_reaction(emoji)
            except Exception:
                await ctx.send(f"Could not add emoji: {emoji}")

    @rr.command(name="updateembed")
    async def rr_updateembed(
        self,
        ctx: commands.Context,
        message_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        *,
        include_roles: Optional[bool] = True,
    ):
        """Update embed title/description and optionally append role list."""
        posts = await self.config.guild(ctx.guild).posts()
        post_data = posts.get(str(message_id))
        if not post_data:
            return await ctx.send("No such reaction role message.")

        channel_id = post_data.get("_meta", {}).get("channel_id")
        channel = ctx.guild.get_channel(channel_id or ctx.channel.id)

        try:
            msg = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("Message not found.")
        if not msg.embeds:
            return await ctx.send("That message has no embed.")

        old = msg.embeds[0]
        title = title or old.title or "React for Roles"
        desc = description or old.description or ""

        if include_roles:
            desc = desc.strip() + "\n\n"
            for emoji, info in post_data.items():
                if emoji == "_meta":
                    continue
                role = ctx.guild.get_role(info["role_id"])
                if role:
                    line = f"{emoji} → {role.name}"
                    if info.get("booster_only"):
                        line += " *(Nitro only)*"
                    desc += f"{line}\n"

        emb = discord.Embed(title=title[:256], description=desc[:4000], color=EMBED_OK)
        await msg.edit(embed=emb)
        await ctx.send("Embed updated.")

    # ---------------- Reaction Listeners ----------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        posts = await self.config.guild(guild).posts()
        bindings = posts.get(str(payload.message_id))
        if not bindings:
            return

        emoji = str(payload.emoji)
        config = bindings.get(emoji)
        if not config:
            return

        role = guild.get_role(config["role_id"])
        member = guild.get_member(payload.user_id)
        if not role or not member or member.bot:
            return

        if config.get("booster_only") and not member.premium_since:
            return  # silently ignore

        try:
            await member.add_roles(role, reason="Reaction role")
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        posts = await self.config.guild(guild).posts()
        bindings = posts.get(str(payload.message_id))
        if not bindings:
            return

        emoji = str(payload.emoji)
        config = bindings.get(emoji)
        if not config:
            return

        role = guild.get_role(config["role_id"])
        member = guild.get_member(payload.user_id)
        if not role or not member:
            return
        try:
            await member.remove_roles(role, reason="Reaction role removed")
        except discord.Forbidden:
            pass
