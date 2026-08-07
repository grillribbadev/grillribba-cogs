from __future__ import annotations

import time
from typing import Optional

import discord


def utc_timestamp() -> int:
    """Return the current UTC timestamp."""
    return int(time.time())


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration."""
    seconds = max(0, int(seconds))

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def format_berries(amount: int) -> str:
    """Format a Beri amount."""
    return f"฿{amount:,}"


def rarity_color(rarity: str) -> discord.Color:
    """Return an embed colour for a rarity."""
    rarity = rarity.lower()

    colours = {
        "common": discord.Color.light_grey(),
        "uncommon": discord.Color.green(),
        "rare": discord.Color.blue(),
        "epic": discord.Color.purple(),
        "legendary": discord.Color.gold(),
    }

    return colours.get(rarity, discord.Color.blurple())


def make_embed(
    *,
    title: str,
    description: str = "",
    colour: Optional[discord.Color] = None,
) -> discord.Embed:
    """Create a standard embed."""
    embed = discord.Embed(
        title=title,
        description=description,
        colour=colour or discord.Color.blurple(),
    )

    embed.timestamp = discord.utils.utcnow()

    return embed


def clamp(value: int, minimum: int, maximum: int) -> int:
    """Clamp a value between a minimum and maximum."""
    return max(minimum, min(value, maximum))


def is_number(content: str) -> bool:
    """Return True if the message content is only an integer."""
    return content.strip().isdigit()


def clean_name(name: str) -> str:
    """Normalize a character name for comparisons."""
    return " ".join(name.lower().split())