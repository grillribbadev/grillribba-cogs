from __future__ import annotations
import discord
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional
from .constants import EMBED_OK, EMBED_ERR


class ReactRoles(commands.Cog):
    """Fully configurable reaction role embeds with Nitro-only options."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=956321478, force_registration=True)
        self.config.register_guild(posts={})
        self._booster_cleanup.start()

    def cog_unload(self):
        self._booster_cleanup.cancel()

    @tasks.loop(minutes=15)
    async def _booster_cleanup(self):
        for guild in self.bot.guilds:
            posts = await self.config.guild(guild).posts()
            booster_roles = {
                v["role_id"]
                for binds in posts.values()
                for k, v in binds.items()
                if k != "_meta" and v.get("booster_only")
            }

            for role_id in booster_roles:
                role = guild.get_role(role_id)
                if not role:
                    continue
                for member in role.members:
                    if not member.premium_since:
                        try:
                            await member.remove_roles(role, reason="Lost Nitro booster")
                        except Exception:
                            pass

    @_booster_cleanup.before_loop
    async def before_booster_cleanup(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="rr", invoke_without_command=True)
    @commands.admin()
    async def rr(self, ctx):
        """Reaction role manager."""
        await ctx.send_help()

    @rr.command(name="create")
    async def rr_create(self, ctx, channel: discord.TextChannel, title: str, *, description: str):
        """Create a reaction-role embed in the target channel."""
        emb = discord.Embed(title=title[:256], description=description[:2000], color=EMBED_OK)
        msg = await channel.send(embed=emb)
        await self.config.guild(ctx.guild).posts.set_raw(str(msg.id), value={"_meta": {"channel_id": channel.id}})
        await ctx.send(f"Created new reaction-role embed in {channel.mention} (ID: `{msg.id}`).")

    @rr.command(name="add")
    async def rr_add(self, ctx, message_id: int, emoji: str, role: discord.Role, booster_only: Optional[bool] = False):
        """Add a reaction-role binding."""
        posts = await self.config.guild(ctx.guild).posts()
        data = posts.get(str(message_id))
        if not data:
            return await ctx.send("Message ID not found in this server's config.")

        channel_id = data["_meta"]["channel_id"]
        channel = ctx.guild.get_channel(channel_id)

        try:
            msg = await channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
        except Exception:
            return await ctx.send("Failed to add reaction. Invalid emoji or missing perms?")

        data[str(emoji)] = {"role_id": role.id, "booster_only": booster_only}
        await self.config.guild(ctx.guild).posts.set_raw(str(message_id), value=data)
        await ctx.send(f"Added mapping: {emoji} → {role.mention} (Nitro only: `{booster_only}`)")

    @rr.command(name="remove")
    async def rr_remove(self, ctx, message_id: int, emoji: str):
        """Remove a single emoji→role mapping."""
        posts = await self.config.guild(ctx.guild).posts()
        data = posts.get(str(message_id))
        if not data or emoji not in data:
            return await ctx.send("That emoji is not mapped.")
        del data[emoji]
        await self.config.guild(ctx.guild).posts.set_raw(str(message_id), value=data)
        await ctx.send("Emoji-role mapping removed.")

    @rr.command(name="delete")
    async def rr_delete(self, ctx, message_id: int, delete_message: Optional[bool] = False):
        """Delete a reaction-role message from config (optionally delete the actual message)."""
        posts = await self.config.guild(ctx.guild).posts()
        data = posts.get(str(message_id))
        if not data:
            return await ctx.send("That message ID isn't tracked.")
        channel_id = data["_meta"]["channel_id"]
        channel = ctx.guild.get_channel(channel_id)

        if delete_message:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.delete()
            except:
                await ctx.send("Couldn't delete message (missing permissions or already deleted).")

        await self.config.guild(ctx.guild).posts.clear_raw(str(message_id))
        await ctx.send(f"Removed reaction-role ID `{message_id}` from tracking.")

    @rr.command(name="list")
    async def rr_list(self, ctx):
        """List all tracked reaction-role posts with their embed titles."""
        posts = await self.config.guild(ctx.guild).posts()
        if not posts:
            return await ctx.send("No reaction-role messages configured.")

        lines = []
        for msg_id, binds in posts.items():
            channel_id = binds.get("_meta", {}).get("channel_id")
            channel = ctx.guild.get_channel(channel_id)
            bind_count = sum(1 for k in binds if k != "_meta")

            try:
                if channel is None:
                    raise ValueError("Channel missing")
                msg = await channel.fetch_message(int(msg_id))
                title = msg.embeds[0].title if msg.embeds else "(no title)"
                channel_name = f"#{channel.name}"
            except:
                title = "*message not found*"
                channel_name = "*unknown*"

            lines.append(f"`{msg_id}` • {bind_count} emoji(s) • **{title}** • {channel_name}")

        await ctx.send("\n".join(lines[:10]) or "No reaction-role messages found.")

    @rr.command(name="updateembed")
    async def rr_updateembed(
        self,
        ctx,
        message_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        *,
        include_roles: bool = True
    ):
        """Update embed title/description and optionally rewrite role list."""
        posts = await self.config.guild(ctx.guild).posts()
        binds = posts.get(str(message_id))
        if not binds:
            return await ctx.send("Message ID not found.")
        channel = ctx.guild.get_channel(binds["_meta"]["channel_id"])

        try:
            msg = await channel.fetch_message(message_id)
        except:
            return await ctx.send("Message not found in channel.")

        old = msg.embeds[0]
        title = title or old.title or "React for Roles"
        desc = description or old.description or ""

        if include_roles:
            desc = desc.strip() + "\n\n"
            for emoji, info in binds.items():
                if emoji == "_meta":
                    continue
                role = ctx.guild.get_role(info["role_id"])
                if role:
                    txt = f"{emoji} → {role.name}"
                    if info.get("booster_only"):
                        txt += " *(Nitro only)*"
                    desc += txt + "\n"

        emb = discord.Embed(title=title[:256], description=desc[:4000], color=EMBED_OK)
        await msg.edit(embed=emb)
        await ctx.send("Embed updated!")

    @rr.command(name="post")
    async def rr_post(self, ctx, source_message_id: int, channel: discord.TextChannel, title="React for Roles", *, description="React below to get roles."):
        """Repost an existing reaction-role embed to another channel."""
        posts = await self.config.guild(ctx.guild).posts()
        binds = posts.get(str(source_message_id))
        if not binds:
            return await ctx.send("No bindings found for that message ID.")
        emb = discord.Embed(title=title, description=description, color=EMBED_OK)
        new_msg = await channel.send(embed=emb)
        new_data = {k: v for k, v in binds.items() if k != "_meta"}
        new_data["_meta"] = {"channel_id": channel.id}
        await self.config.guild(ctx.guild).posts.set_raw(str(new_msg.id), value=new_data)

        for emoji in new_data:
            if emoji == "_meta":
                continue
            try:
                await new_msg.add_reaction(emoji)
            except:
                pass

        await ctx.send(f"Reposted embed to {channel.mention} (new ID: `{new_msg.id}`).")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        posts = await self.config.guild(guild).posts()
        binds = posts.get(str(payload.message_id))
        if not binds:
            return
        emoji = str(payload.emoji)
        config = binds.get(emoji)
        if not config:
            return
        member = guild.get_member(payload.user_id)
        role = guild.get_role(config["role_id"])
        if not member or member.bot or not role:
            return
        if config.get("booster_only") and not member.premium_since:
            return
        try:
            await member.add_roles(role, reason="Reaction role added")
        except:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        posts = await self.config.guild(guild).posts()
        binds = posts.get(str(payload.message_id))
        if not binds:
            return
        emoji = str(payload.emoji)
        config = binds.get(emoji)
        if not config:
            return
        member = guild.get_member(payload.user_id)
        role = guild.get_role(config["role_id"])
        if not member or not role:
            return
        try:
            await member.remove_roles(role, reason="Reaction role removed")
        except:
            pass
