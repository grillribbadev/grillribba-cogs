# roleinfo/roleinfo.py
from __future__ import annotations

from typing import Optional, Iterable
import discord
from discord.utils import format_dt
from redbot.core import commands
from redbot.core.bot import Red

# -------- helpers --------

def humanize_perms(perms: Iterable[str], limit: int = 24) -> tuple[str, int]:
    """
    Turn permission names (snake_case) into Title Case.
    Returns: (joined_string, remaining_count)
    """
    pretty = [p.replace("_", " ").title() for p in perms]
    if len(pretty) > limit:
        shown = ", ".join(pretty[:limit])
        return shown, len(pretty) - limit
    return ", ".join(pretty), 0


class RoleInfo(commands.Cog):
    """Show info about a role in a neat embed."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot

    @commands.command(name="roleinfo", aliases=["ri"])
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(3, 10, commands.BucketType.guild)
    async def roleinfo(
        self,
        ctx: commands.Context,
        *,
        role: Optional[discord.Role] = None,
    ):
        """
        Show info about a role.
        Accepts: @Mention, ID, or exact name.
        If omitted, shows the @everyone role.
        """
        guild = ctx.guild
        assert guild is not None

        role = role or guild.default_role  # default to @everyone

        # Basic facts
        created = format_dt(role.created_at, style="F")
        created_rel = format_dt(role.created_at, style="R")
        color = role.color if role.color.value else discord.Color.dark_grey()
        members = len(role.members)
        position = role.position
        hoist = "Yes" if role.hoist else "No"
        mentionable = "Yes" if role.mentionable else "No"
        managed = "Yes" if role.managed else "No"

        # Permissions
        perms_obj: discord.Permissions = role.permissions
        enabled = [name for name, val in perms_obj if val]
        enabled_str, remaining = humanize_perms(enabled, limit=24)

        # Build embed
        emb = discord.Embed(
            title=f"Role: {role.name}",
            color=color,
            description=role.mention if role != guild.default_role else "@everyone",
        )

        # ---- FIX: only set thumbnail when we actually have a URL ----
        role_icon = getattr(role, "display_icon", None)
        # role.display_icon may be an Asset or None (requires boosted guild features).
        if role_icon and hasattr(role_icon, "url"):
            emb.set_thumbnail(url=role_icon.url)
        elif guild.icon:
            emb.set_thumbnail(url=guild.icon.url)
        # Else: skip setting a thumbnail entirely.

        emb.add_field(
            name="Basics",
            value=(
                f"**ID:** `{role.id}`\n"
                f"**Color:** `{str(role.color)}`\n"
                f"**Position:** `{position}`\n"
                f"**Hoisted:** `{hoist}`\n"
                f"**Mentionable:** `{mentionable}`\n"
                f"**Managed:** `{managed}`\n"
                f"**Created:** {created} ({created_rel})\n"
                f"**Members with role:** `{members}`"
            ),
            inline=False,
        )

        if enabled:
            perms_field = enabled_str + (f"\n…and **{remaining}** more." if remaining else "")
            emb.add_field(name="Enabled Permissions", value=perms_field, inline=False)
        else:
            emb.add_field(name="Enabled Permissions", value="None", inline=False)

        if role == guild.default_role and enabled:
            emb.set_footer(text="These are the permissions granted to everyone unless overridden by channel/category.")

        await ctx.send(embed=emb)

    @roleinfo.error
    async def roleinfo_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.BadArgument):
            await ctx.send("❌ I couldn't find that role. Try a role **mention**, **ID**, or exact **name**.")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Slow down! Try again in {error.retry_after:.1f}s.")
        else:
            raise error
