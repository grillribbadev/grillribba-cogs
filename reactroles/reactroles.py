from __future__ import annotations
import discord
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional, Dict, Any, List, Tuple
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

    # ---------- helpers ----------
    def _is_adminish(self, member: discord.Member) -> bool:
        perms = getattr(member, "guild_permissions", None)
        if not perms:
            return False
        return bool(perms.administrator or perms.manage_guild)

    def _parse_role(self, guild: discord.Guild, raw: str) -> Optional[discord.Role]:
        if not raw:
            return None
        s = raw.strip()
        # <@&123>
        if s.startswith("<@&") and s.endswith(">"):
            s = s[3:-1]
        # plain id
        if s.isdigit():
            return guild.get_role(int(s))
        # by name (best-effort exact)
        lower = s.lower()
        for r in guild.roles:
            if r.name.lower() == lower:
                return r
        return None

    def _parse_channel(self, guild: discord.Guild, raw: str) -> Optional[discord.TextChannel]:
        if not raw:
            return None
        s = raw.strip()
        # <#123>
        if s.startswith("<#") and s.endswith(">"):
            s = s[2:-1]
        if s.isdigit():
            ch = guild.get_channel(int(s))
            return ch if isinstance(ch, discord.TextChannel) else None
        lower = s.lstrip("#").lower()
        for ch in guild.text_channels:
            if ch.name.lower() == lower:
                return ch
        return None

    async def _get_post_options(self, guild: discord.Guild) -> List[discord.SelectOption]:
        posts = await self.config.guild(guild).posts()
        # Discord selects can only show 25 options.
        msg_ids = sorted((int(mid) for mid in posts.keys() if str(mid).isdigit()), reverse=True)[:25]
        options: List[discord.SelectOption] = []
        for mid in msg_ids:
            data = posts.get(str(mid), {})
            channel_id = data.get("_meta", {}).get("channel_id")
            channel = guild.get_channel(channel_id) if channel_id else None
            binds_count = sum(1 for k in data.keys() if k != "_meta")
            title = "(unknown)"
            try:
                if channel is not None:
                    msg = await channel.fetch_message(mid)
                    if msg.embeds:
                        title = msg.embeds[0].title or "(no title)"
            except Exception:
                pass

            label = title[:100]
            desc = f"{('#' + channel.name) if channel else 'unknown channel'} • {binds_count} emoji(s)"[:100]
            options.append(discord.SelectOption(label=label, description=desc, value=str(mid)))
        if not options:
            options.append(discord.SelectOption(label="No posts configured", value="none", description="Use Create to make one"))
        return options

    # ---------- interactive menu ----------
    class _CreatePostModal(discord.ui.Modal):
        def __init__(self, cog: "ReactRoles"):
            super().__init__(title="Create reaction-role post")
            self.cog = cog
            self.channel = discord.ui.TextInput(label="Channel (#channel or id)", placeholder="#roles", max_length=100)
            self.title_in = discord.ui.TextInput(label="Title", placeholder="React for Roles", max_length=256)
            self.desc_in = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=2000)
            self.add_item(self.channel)
            self.add_item(self.title_in)
            self.add_item(self.desc_in)

        async def on_submit(self, interaction: discord.Interaction):
            assert interaction.guild is not None
            ch = self.cog._parse_channel(interaction.guild, str(self.channel.value))
            if not ch:
                return await interaction.response.send_message("Couldn't find that text channel.", ephemeral=True)
            emb = discord.Embed(title=str(self.title_in.value)[:256], description=str(self.desc_in.value)[:2000], color=EMBED_OK)
            msg = await ch.send(embed=emb)
            await self.cog.config.guild(interaction.guild).posts.set_raw(
                str(msg.id), value={"_meta": {"channel_id": ch.id}}
            )
            await interaction.response.send_message(f"Created post in {ch.mention}.", ephemeral=True)

    class _AddMappingModal(discord.ui.Modal):
        def __init__(self, cog: "ReactRoles", message_id: int):
            super().__init__(title="Add emoji → role")
            self.cog = cog
            self.message_id = message_id
            self.emoji = discord.ui.TextInput(label="Emoji", placeholder="😀 or <:name:id>", max_length=64)
            self.role = discord.ui.TextInput(label="Role (@role, id, or exact name)", placeholder="@Member", max_length=128)
            self.booster = discord.ui.TextInput(label="Nitro booster only? (yes/no)", placeholder="no", max_length=8, required=False)
            self.add_item(self.emoji)
            self.add_item(self.role)
            self.add_item(self.booster)

        async def on_submit(self, interaction: discord.Interaction):
            assert interaction.guild is not None
            posts = await self.cog.config.guild(interaction.guild).posts()
            data = posts.get(str(self.message_id))
            if not data:
                return await interaction.response.send_message("That post isn't tracked anymore.", ephemeral=True)
            channel_id = data.get("_meta", {}).get("channel_id")
            channel = interaction.guild.get_channel(channel_id) if channel_id else None
            if not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("Channel for that post is missing.", ephemeral=True)

            role_obj = self.cog._parse_role(interaction.guild, str(self.role.value))
            if not role_obj:
                return await interaction.response.send_message("Couldn't resolve that role.", ephemeral=True)

            booster_raw = (str(self.booster.value or "").strip().lower() if self.booster.value is not None else "")
            booster_only = booster_raw in {"y", "yes", "true", "1"}

            try:
                msg = await channel.fetch_message(self.message_id)
                await msg.add_reaction(str(self.emoji.value))
            except Exception:
                return await interaction.response.send_message("Failed to add reaction (bad emoji or missing perms).", ephemeral=True)

            data[str(self.emoji.value)] = {"role_id": role_obj.id, "booster_only": booster_only}
            await self.cog.config.guild(interaction.guild).posts.set_raw(str(self.message_id), value=data)
            await interaction.response.send_message(f"Added mapping {self.emoji.value} → {role_obj.mention}.", ephemeral=True)

    class _RemoveMappingModal(discord.ui.Modal):
        def __init__(self, cog: "ReactRoles", message_id: int):
            super().__init__(title="Remove emoji mapping")
            self.cog = cog
            self.message_id = message_id
            self.emoji = discord.ui.TextInput(label="Emoji to remove", placeholder="😀 or <:name:id>", max_length=64)
            self.add_item(self.emoji)

        async def on_submit(self, interaction: discord.Interaction):
            assert interaction.guild is not None
            posts = await self.cog.config.guild(interaction.guild).posts()
            data = posts.get(str(self.message_id))
            if not data:
                return await interaction.response.send_message("That post isn't tracked anymore.", ephemeral=True)
            emoji = str(self.emoji.value)
            if emoji not in data:
                return await interaction.response.send_message("That emoji isn't mapped.", ephemeral=True)

            del data[emoji]
            await self.cog.config.guild(interaction.guild).posts.set_raw(str(self.message_id), value=data)

            channel_id = data.get("_meta", {}).get("channel_id")
            channel = interaction.guild.get_channel(channel_id) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                try:
                    msg = await channel.fetch_message(self.message_id)
                    await msg.clear_reaction(emoji)
                except Exception:
                    pass
            await interaction.response.send_message("Mapping removed.", ephemeral=True)

    class _PostToChannelModal(discord.ui.Modal):
        def __init__(self, cog: "ReactRoles", message_id: int):
            super().__init__(title="Post to channel")
            self.cog = cog
            self.message_id = message_id
            self.channel = discord.ui.TextInput(label="Target channel (#channel or id)", placeholder="#roles", max_length=100)
            self.title_in = discord.ui.TextInput(label="Title (optional)", placeholder="(leave blank to keep original)", max_length=256, required=False)
            self.desc_in = discord.ui.TextInput(
                label="Description (optional)",
                placeholder="(leave blank to keep original)",
                style=discord.TextStyle.paragraph,
                max_length=2000,
                required=False,
            )
            self.add_item(self.channel)
            self.add_item(self.title_in)
            self.add_item(self.desc_in)

        async def on_submit(self, interaction: discord.Interaction):
            assert interaction.guild is not None

            target = self.cog._parse_channel(interaction.guild, str(self.channel.value))
            if not target:
                return await interaction.response.send_message("Couldn't find that text channel.", ephemeral=True)

            posts = await self.cog.config.guild(interaction.guild).posts()
            binds = posts.get(str(self.message_id))
            if not binds:
                return await interaction.response.send_message("That post isn't tracked anymore.", ephemeral=True)

            # Pull original embed title/description if possible.
            orig_title = "React for Roles"
            orig_desc = "React below to get roles."
            src_channel_id = binds.get("_meta", {}).get("channel_id")
            src_channel = interaction.guild.get_channel(src_channel_id) if src_channel_id else None
            if isinstance(src_channel, discord.TextChannel):
                try:
                    src_msg = await src_channel.fetch_message(self.message_id)
                    if src_msg.embeds:
                        e = src_msg.embeds[0]
                        if e.title:
                            orig_title = e.title
                        if e.description:
                            orig_desc = e.description
                except Exception:
                    pass

            title = (str(self.title_in.value).strip() if self.title_in.value is not None else "")
            desc = (str(self.desc_in.value).strip() if self.desc_in.value is not None else "")
            title = title or orig_title
            desc = desc or orig_desc

            emb = discord.Embed(title=title[:256], description=desc[:2000], color=EMBED_OK)
            new_msg = await target.send(embed=emb)

            # Copy mappings (not _meta), track new post.
            new_data = {k: v for k, v in binds.items() if k != "_meta"}
            new_data["_meta"] = {"channel_id": target.id}
            await self.cog.config.guild(interaction.guild).posts.set_raw(str(new_msg.id), value=new_data)

            # Add reactions
            for emoji in new_data:
                if emoji == "_meta":
                    continue
                try:
                    await new_msg.add_reaction(emoji)
                except Exception:
                    pass

            await interaction.response.send_message(f"Posted to {target.mention}.", ephemeral=True)

    class _MenuView(discord.ui.View):
        def __init__(self, cog: "ReactRoles", author_id: int, guild: discord.Guild):
            super().__init__(timeout=300)
            self.cog = cog
            self.author_id = author_id
            self.guild = guild
            self.selected_message_id: Optional[int] = None

            self.post_select = discord.ui.Select(placeholder="Select a reaction-role post…", min_values=1, max_values=1)
            self.post_select.callback = self._on_select  # type: ignore
            self.add_item(self.post_select)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
                return False
            if interaction.guild is None:
                await interaction.response.send_message("Use this in a server.", ephemeral=True)
                return False
            member = interaction.guild.get_member(interaction.user.id)
            if not member or not self.cog._is_adminish(member):
                await interaction.response.send_message("You need Manage Server (or Admin) to use this.", ephemeral=True)
                return False
            return True

        async def refresh_options(self):
            self.post_select.options = await self.cog._get_post_options(self.guild)
            if self.post_select.options and self.post_select.options[0].value == "none":
                self.selected_message_id = None

        async def _on_select(self, interaction: discord.Interaction):
            val = self.post_select.values[0]
            self.selected_message_id = int(val) if val.isdigit() else None
            await interaction.response.edit_message(embed=await self._render_embed(), view=self)

        async def _render_embed(self) -> discord.Embed:
            emb = discord.Embed(title="Reaction Roles — Menu", color=EMBED_OK)
            if not self.selected_message_id:
                emb.description = "Select a post above, or click **Create**."
                return emb

            posts = await self.cog.config.guild(self.guild).posts()
            data = posts.get(str(self.selected_message_id))
            if not data:
                emb.color = EMBED_ERR
                emb.description = "That post is no longer tracked."
                return emb

            channel_id = data.get("_meta", {}).get("channel_id")
            channel = self.guild.get_channel(channel_id) if channel_id else None
            binds = [(k, v) for k, v in data.items() if k != "_meta"]
            emb.description = f"**Post:** `{self.selected_message_id}`\n**Channel:** {channel.mention if channel else '*unknown*'}\n**Mappings:** {len(binds)}"
            if binds:
                preview = []
                for emoji, info in binds[:10]:
                    role = self.guild.get_role(info.get("role_id"))
                    txt = f"{emoji} → {(role.mention if role else 'missing role')}"
                    if info.get("booster_only"):
                        txt += " (Nitro only)"
                    preview.append(txt)
                emb.add_field(name="Current mappings (top 10)", value="\n".join(preview), inline=False)
            return emb

        @discord.ui.button(label="Create", style=discord.ButtonStyle.success)
        async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(ReactRoles._CreatePostModal(self.cog))

        @discord.ui.button(label="Add mapping", style=discord.ButtonStyle.primary)
        async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.selected_message_id:
                return await interaction.response.send_message("Select a post first.", ephemeral=True)
            await interaction.response.send_modal(ReactRoles._AddMappingModal(self.cog, self.selected_message_id))

        @discord.ui.button(label="Remove mapping", style=discord.ButtonStyle.secondary)
        async def rm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.selected_message_id:
                return await interaction.response.send_message("Select a post first.", ephemeral=True)
            await interaction.response.send_modal(ReactRoles._RemoveMappingModal(self.cog, self.selected_message_id))

        @discord.ui.button(label="Delete post", style=discord.ButtonStyle.danger)
        async def del_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.selected_message_id:
                return await interaction.response.send_message("Select a post first.", ephemeral=True)
            posts = await self.cog.config.guild(self.guild).posts()
            data = posts.get(str(self.selected_message_id))
            if not data:
                return await interaction.response.send_message("That post isn't tracked.", ephemeral=True)

            channel_id = data.get("_meta", {}).get("channel_id")
            channel = self.guild.get_channel(channel_id) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                try:
                    msg = await channel.fetch_message(self.selected_message_id)
                    await msg.delete()
                except Exception:
                    pass

            await self.cog.config.guild(self.guild).posts.clear_raw(str(self.selected_message_id))
            self.selected_message_id = None
            await self.refresh_options()
            await interaction.response.edit_message(embed=await self._render_embed(), view=self)

        @discord.ui.button(label="Post to channel", style=discord.ButtonStyle.primary)
        async def post_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.selected_message_id:
                return await interaction.response.send_message("Select a post first.", ephemeral=True)
            await interaction.response.send_modal(ReactRoles._PostToChannelModal(self.cog, self.selected_message_id))

        @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
        async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.refresh_options()
            await interaction.response.edit_message(embed=await self._render_embed(), view=self)

        @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
        async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            for item in self.children:
                item.disabled = True  # type: ignore
            await interaction.response.edit_message(view=self)
            self.stop()

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

    @commands.hybrid_group(name="rr", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin()
    async def rr(self, ctx: commands.Context):
        """Reaction role manager."""
        await ctx.send_help()

    @rr.command(name="menu")
    async def rr_menu(self, ctx: commands.Context):
        """Open an interactive menu to manage reaction-role posts."""
        if not ctx.guild:
            return await ctx.send("Use this in a server.")

        view = ReactRoles._MenuView(self, ctx.author.id, ctx.guild)
        await view.refresh_options()
        emb = await view._render_embed()

        # If invoked as a slash command, respond ephemerally.
        if ctx.interaction is not None:
            await ctx.interaction.response.send_message(embed=emb, view=view, ephemeral=True)
        else:
            # Prefix invocation still works: send a normal message with components.
            await ctx.send(embed=emb, view=view)

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

        channel_id = data.get("_meta", {}).get("channel_id")
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
        """
        Remove a single emoji→role mapping from a tracked message,
        and remove the emoji from the message's reactions.
        """
        posts = await self.config.guild(ctx.guild).posts()
        data = posts.get(str(message_id))
        if not data or emoji not in data:
            return await ctx.send("That emoji is not mapped.")

        # Remove from config
        del data[emoji]
        await self.config.guild(ctx.guild).posts.set_raw(str(message_id), value=data)

        # Remove emoji from message
        channel_id = data.get("_meta", {}).get("channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        if channel:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.clear_reaction(emoji)
                await ctx.send("Mapping removed and emoji removed from message.")
            except discord.Forbidden:
                await ctx.send("Mapping removed. I can't remove the emoji (missing permission).")
            except discord.HTTPException:
                await ctx.send("Mapping removed. Failed to remove emoji from message.")
            except discord.NotFound:
                await ctx.send("Mapping removed. Message not found.")
        else:
            await ctx.send("Mapping removed. Channel not found.")

    @rr.command(name="delete")
    async def rr_delete(self, ctx, message_id: int):
        """Delete a reaction-role message from config and the actual message if possible."""
        posts = await self.config.guild(ctx.guild).posts()
        data = posts.get(str(message_id))
        if not data:
            return await ctx.send("That message ID isn't being tracked.")

        channel_id = data.get("_meta", {}).get("channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        if channel:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.delete()
                await ctx.send("Embed message deleted.")
            except discord.NotFound:
                await ctx.send("Message already deleted.")
            except discord.Forbidden:
                await ctx.send("I don't have permission to delete the message.")
            except discord.HTTPException:
                await ctx.send("Discord API error while deleting the message.")
        else:
            await ctx.send("Channel not found—only removing config entry.")

        await self.config.guild(ctx.guild).posts.clear_raw(str(message_id))
        await ctx.send(f"Removed message `{message_id}` from reaction-role config.")

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
        channel = ctx.guild.get_channel(binds.get("_meta", {}).get("channel_id"))
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
