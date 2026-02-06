from __future__ import annotations

import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Iterable, Tuple, Optional

import discord

log = logging.getLogger("red.mutelist")


def member_has_any_role(member: discord.Member, role_ids: set[int]) -> bool:
    """Check if a member has any of the specified roles.
    
    Args:
        member: The member to check
        role_ids: Set of role IDs to check for
        
    Returns:
        True if the member has at least one of the roles
    """
    return any(r.id in role_ids for r in member.roles)


def format_user_line(
    member: discord.Member,
    *,
    reason: str | None,
    moderator_id: int | None,
    at_ts: int | None,
    until_ts: int | None,
) -> str:
    """Format a single line for the muted members list.
    
    Args:
        member: The muted member
        reason: Mute reason
        moderator_id: ID of the moderator who applied the mute
        at_ts: Unix timestamp when muted
        until_ts: Unix timestamp when mute expires (None for permanent)
        
    Returns:
        Formatted string for display
    """
    reason_txt = reason.strip() if (reason and reason.strip()) else "no reason provided"
    
    # Use Discord's timestamp formatting for better display
    if at_ts:
        when = f"<t:{at_ts}:f>"  # Discord timestamp format
    else:
        when = "unknown"
    
    if until_ts:
        now = int(datetime.now(timezone.utc).timestamp())
        if until_ts <= now:
            until = f"<t:{until_ts}:R> (expired)"
        else:
            until = f"<t:{until_ts}:R>"
    else:
        until = "permanent"
    
    mod_disp = f"<@{moderator_id}>" if moderator_id else "unknown"
    
    return (
        f"• **{member}** (`{member.id}`)\n"
        f"  └ **Reason:** {reason_txt}\n"
        f"  └ **By:** {mod_disp} | **When:** {when} | **Until:** {until}"
    )


def parse_time(time_str: str) -> timedelta | None:
    """Parse a time string into a timedelta.
    
    Supports formats like:
    - 1h, 2d, 3w, 4m (hours, days, weeks, months)
    - 1h30m, 2d12h (combined)
    - 90m, 48h (numeric conversions)
    
    Args:
        time_str: The time string to parse
        
    Returns:
        timedelta object or None if parsing fails
    """
    time_str = time_str.lower().strip()
    
    # Pattern: number followed by unit (s/m/h/d/w/M/y)
    pattern = r"(\d+)\s*([smhdwMy])"
    matches = re.findall(pattern, time_str)
    
    if not matches:
        return None
    
    total_seconds = 0
    
    for value, unit in matches:
        value = int(value)
        
        if unit == "s":
            total_seconds += value
        elif unit == "m":
            total_seconds += value * 60
        elif unit == "h":
            total_seconds += value * 3600
        elif unit == "d":
            total_seconds += value * 86400
        elif unit == "w":
            total_seconds += value * 604800
        elif unit == "M":
            total_seconds += value * 2592000  # 30 days
        elif unit == "y":
            total_seconds += value * 31536000  # 365 days
    
    return timedelta(seconds=total_seconds) if total_seconds > 0 else None


def humanize_timedelta(
    *,
    seconds: int | None = None,
    timedelta: timedelta | None = None,
) -> str:
    """Convert a timedelta to a human-readable string.
    
    Args:
        seconds: Number of seconds (alternative to timedelta)
        timedelta: timedelta object
        
    Returns:
        Human-readable time string (e.g., "2 days, 3 hours, 15 minutes")
    """
    if timedelta is None:
        if seconds is None:
            return "unknown"
        timedelta = globals()['timedelta'](seconds=seconds)
    
    total_seconds = int(timedelta.total_seconds())
    
    if total_seconds == 0:
        return "0 seconds"
    
    # Calculate time units
    years, remainder = divmod(total_seconds, 31536000)
    months, remainder = divmod(remainder, 2592000)
    weeks, remainder = divmod(remainder, 604800)
    days, remainder = divmod(remainder, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if weeks:
        parts.append(f"{weeks} week{'s' if weeks != 1 else ''}")
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds and not parts:  # Only show seconds if no larger units
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    
    # Join with commas, but use "and" for the last item
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + f" and {parts[-1]}"
    elif parts:
        return parts[0]
    else:
        return "0 seconds"


async def get_audit_reason(
    guild: discord.Guild,
    member: discord.Member,
    role_ids: set[int],
) -> Tuple[str, int, int] | None:
    """Try to find the mute reason from audit logs.
    
    Args:
        guild: The guild to check
        member: The member to look up
        role_ids: Set of mute role IDs
        
    Returns:
        Tuple of (reason, moderator_id, timestamp) or None
    """
    try:
        async for entry in guild.audit_logs(
            limit=25,
            action=discord.AuditLogAction.member_role_update,
        ):
            if entry.target.id != member.id:
                continue
            
            # Check if a configured mute role was added
            added_roles = set(
                getattr(entry.changes.after, "roles", []) or []
            ) - set(
                getattr(entry.changes.before, "roles", []) or []
            )
            
            if any(r.id in role_ids for r in added_roles):
                reason = (entry.reason or "").strip()
                mod_id = entry.user.id if entry.user else 0
                at_ts = (
                    int(entry.created_at.replace(tzinfo=timezone.utc).timestamp())
                    if entry.created_at
                    else None
                )
                return (reason, mod_id, at_ts)
    
    except discord.Forbidden:
        log.debug(f"Missing audit log permissions in guild {guild.id}")
    except Exception as e:
        log.debug(f"Audit log scan failed in guild {guild.id}: {e}")
    
    return None


def format_duration(seconds: int) -> str:
    """Format a duration in seconds to a short string.
    
    Args:
        seconds: Number of seconds
        
    Returns:
        Short formatted string (e.g., "2d 3h")
    """
    if seconds < 60:
        return f"{seconds}s"
    
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    
    hours = minutes // 60
    if hours < 24:
        mins = minutes % 60
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    
    days = hours // 24
    hrs = hours % 24
    return f"{days}d {hrs}h" if hrs else f"{days}d"


def truncate_text(text: str, max_length: int = 100) -> str:
    """Truncate text to a maximum length, adding ellipsis if needed.
    
    Args:
        text: The text to truncate
        max_length: Maximum length (default: 100)
        
    Returns:
        Truncated text with ellipsis if needed
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."
