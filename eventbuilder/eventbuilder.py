import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import datetime

class EventBuilder(commands.Cog):
    """Interactive Event Builder with role & channel support."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=13371338, force_registration=True)
        self.config.register_guild(
            events={},           # event_name: {desc, deadline, ping}
            roles=[],            # list of allowed role IDs
            announce_channel=None
        )

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.command()
    async def event(self, ctx: commands.Context, subcommand: str, *, name: str = None):
        """Create or remove events interactively."""
        subcommand = subcommand.lower()

        if subcommand == "create" and name:
            view = await EventCreateView.build(self, ctx.author, ctx.guild, name)
            embed = discord.Embed(
                title=f"📅 Creating Event: {name}",
                description="Use the buttons below to configure the event details.",
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed, view=view)

        elif subcommand == "remove" and name:
            async with self.config.guild(ctx.guild).events() as events:
                if name.lower() in events:
                    del events[name.lower()]
                    await ctx.send(f"🗑️ Event `{name}` removed.")
                else:
                    await ctx.send("❌ That event doesn't exist.")

        else:
            prefix = ctx.clean_prefix
            await ctx.send(f"❌ Usage: `{prefix}event create <name>` or `{prefix}event remove <name>`.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def eventroles(self, ctx):
        """List roles allowed to be used as pings."""
        role_ids = await self.config.guild(ctx.guild).roles()
        roles = [ctx.guild.get_role(rid) for rid in role_ids if ctx.guild.get_role(rid)]
        if not roles:
            await ctx.send("📭 No roles have been added.")
        else:
            await ctx.send("**Allowed Roles for Pings:**\n" + "\n".join(f"- {r.mention}" for r in roles))

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def eventaddrole(self, ctx, role: discord.Role):
        """Add a role to the allowed ping list."""
        async with self.config.guild(ctx.guild).roles() as roles:
            if role.id in roles:
                return await ctx.send("⚠️ That role is already allowed.")
            roles.append(role.id)
        await ctx.send(f"✅ {role.mention} added.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def eventdelrole(self, ctx, role: discord.Role):
        """Remove a role from the ping list."""
        async with self.config.guild(ctx.guild).roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
                await ctx.send(f"🗑️ {role.mention} removed.")
            else:
                await ctx.send("❌ That role is not in the list.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def eventsetchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where event announcements go."""
        await self.config.guild(ctx.guild).announce_channel.set(channel.id)
        await ctx.send(f"📢 Event announcements will go to {channel.mention}.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def eventlist(self, ctx):
        """List all saved events (upcoming and expired)."""
        data = await self.config.guild(ctx.guild).events()
        if not data:
            return await ctx.send("📭 No events found.")

        now = datetime.datetime.utcnow()
        upcoming = []
        expired = []

        for name, details in data.items():
            try:
                when = datetime.datetime.fromisoformat(details["when"])
            except:
                continue

            ts = int(when.timestamp())
            line = f"• **{name}** — <t:{ts}:F>"

            if when > now:
                upcoming.append(line)
            else:
                expired.append(line)

        embed = discord.Embed(title="📅 Events", color=discord.Color.blurple())

        if upcoming:
            embed.add_field(name="🟢 Upcoming", value="\n".join(upcoming), inline=False)
        if expired:
            embed.add_field(name="🔴 Expired", value="\n".join(expired), inline=False)

        await ctx.send(embed=embed)




# UI view and role dropdown


class RoleSelect(discord.ui.Select):
    def __init__(self, roles: list[discord.Role]):
        options = [
            discord.SelectOption(label="@everyone", value="@everyone"),
            discord.SelectOption(label="@here", value="@here"),
        ] + [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in roles
        ]

        super().__init__(
            placeholder="📣 Who to ping?",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        view: EventCreateView = self.view
        value = self.values[0]
        if value.startswith("@"):
            view.ping_target = value
        else:
            role = interaction.guild.get_role(int(value))
            view.ping_target = role.mention if role else "@deleted-role"

        await interaction.response.send_message(f"✅ Ping set to: `{view.ping_target}`", ephemeral=True)


class EventCreateView(discord.ui.View):
    def __init__(self, cog, author, guild, name, role_dropdown):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.guild = guild
        self.name = name
        self.description = "No description set."
        self.when = None  # event start time
        self.deadline = None  # optional
        self.image_url = None  # optional
        self.ping_target = "@everyone"

        self.add_item(role_dropdown)

    @classmethod
    async def build(cls, cog, author, guild, name):
        role_ids = await cog.config.guild(guild).roles()
        roles = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid)]
        role_dropdown = RoleSelect(roles)
        return cls(cog, author, guild, name, role_dropdown)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user == self.author

    @discord.ui.button(label="📝 Set Description", style=discord.ButtonStyle.primary)
    async def set_description(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("✍️ Type the description below.", ephemeral=True)

        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg = await self.cog.bot.wait_for("message", check=check, timeout=120)
            self.description = msg.content
            await interaction.followup.send("✅ Description updated.", ephemeral=True)
        except:
            await interaction.followup.send("⏳ Timeout.", ephemeral=True)

    @discord.ui.button(label="📅 Set Start Time (When)", style=discord.ButtonStyle.secondary)
    async def set_when(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("📆 Enter start time: `YYYY-MM-DD HH:MM` (UTC)", ephemeral=True)

        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg = await self.cog.bot.wait_for("message", check=check, timeout=120)
            self.when = datetime.datetime.strptime(msg.content, "%Y-%m-%d %H:%M")
            await interaction.followup.send("✅ Start time set.", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ Invalid format.", ephemeral=True)
        except:
            await interaction.followup.send("⏳ Timeout.", ephemeral=True)

    @discord.ui.button(label="📌 Set Deadline (Optional)", style=discord.ButtonStyle.secondary)
    async def set_deadline(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("📌 Enter deadline (or type `cancel`): `YYYY-MM-DD HH:MM` (UTC)", ephemeral=True)

        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg = await self.cog.bot.wait_for("message", check=check, timeout=120)
            if msg.content.lower() == "cancel":
                self.deadline = None
                return await interaction.followup.send("❎ Deadline removed.", ephemeral=True)

            self.deadline = datetime.datetime.strptime(msg.content, "%Y-%m-%d %H:%M")
            await interaction.followup.send("✅ Deadline set.", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ Invalid format.", ephemeral=True)
        except:
            await interaction.followup.send("⏳ Timeout.", ephemeral=True)

    @discord.ui.button(label="🖼 Set Image (Optional)", style=discord.ButtonStyle.secondary)
    async def set_image(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("🖼 Send an image **URL** now (or type `cancel` to clear it):", ephemeral=True)

        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg = await self.cog.bot.wait_for("message", check=check, timeout=120)
            if msg.content.lower() == "cancel":
                self.image_url = None
                return await interaction.followup.send("❎ Image removed.", ephemeral=True)

            if not msg.content.startswith("http"):
                return await interaction.followup.send("❌ Must be a valid URL.", ephemeral=True)

            self.image_url = msg.content
            await interaction.followup.send("✅ Image URL set.", ephemeral=True)
        except:
            await interaction.followup.send("⏳ Timeout.", ephemeral=True)

    @discord.ui.button(label="📤 Save & Announce", style=discord.ButtonStyle.success)
    async def save_event(self, interaction: discord.Interaction, _):
        if not self.when:
            return await interaction.response.send_message("⏰ You must set a start time first.", ephemeral=True)

        channel_id = await self.cog.config.guild(self.guild).announce_channel()
        channel = self.guild.get_channel(channel_id) if channel_id else None
        if not channel:
            return await interaction.response.send_message("❌ No announcement channel set.", ephemeral=True)

        embed = discord.Embed(
            title=f"📢 {self.name}",
            description=self.description,
            color=discord.Color.green()
        )

        when_ts = int(self.when.timestamp())
        embed.add_field(name="🕒 When", value=f"<t:{when_ts}:F>\n`{self.when.strftime('%Y-%m-%d %H:%M')} UTC`", inline=False)

        if self.deadline:
            deadline_ts = int(self.deadline.timestamp())
            embed.add_field(name="📌 Deadline", value=f"<t:{deadline_ts}:F>\n`{self.deadline.strftime('%Y-%m-%d %H:%M')} UTC`", inline=False)

        if self.image_url:
            embed.set_image(url=self.image_url)

        embed.set_footer(text="Created via EventBuilder")

        mention_str = self.ping_target
        if self.ping_target == "@everyone":
            mention_str = "@everyone"
        elif self.ping_target == "@here":
            mention_str = "@here"
        elif self.ping_target.startswith("<@&"):
            mention_str = self.ping_target
        else:
            # try resolving manually
            try: 
                role = self.guild.get_role(int(self.ping_target.strip("<@&>")))
                if role:
                    mention_str = role.mention
            except:
                mention_str = "@invalid-role"
        await channel.send(content=mention_str, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True, roles=True))


        # Save event
        async with self.cog.config.guild(self.guild).events() as events:
            events[self.name.lower()] = {
                "desc": self.description,
                "when": self.when.isoformat(),
                "deadline": self.deadline.isoformat() if self.deadline else None,
                "image": self.image_url,
                "ping": self.ping_target
            }

        await interaction.response.send_message("✅ Event saved and announced!", ephemeral=True)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("❌ Event creation cancelled.", ephemeral=True)
        self.stop()
