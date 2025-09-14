from __future__ import annotations
import discord
from typing import Iterable

def member_has_any_role(member: discord.Member, role_ids: set[int]) -> bool:
    return any(r.id in role_ids for r in member.roles)

def format_user_line(
    member: discord.Member,
    *,
    reason: str | None,
    moderator_id: int | None,
    at_ts: int | None,
    until_ts: int | None,
) -> str:
    from datetime import datetime, timezone

    reason_txt = reason.strip() if (reason and reason.strip()) else "no reason provided"
    when = (
        datetime.fromtimestamp(at_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if at_ts else "?"
    )
    until = (
        datetime.fromtimestamp(until_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if until_ts else "indef."
    )
    mod_disp = f"<@{moderator_id}>" if moderator_id else "unknown"
    return (
        f"• **{member}** (`{member.id}`)\n"
        f"  reason: {reason_txt}\n"
        f"  by: {mod_disp} at: {when} until: {until}"
    )
