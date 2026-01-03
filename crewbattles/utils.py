from .constants import MAX_LEVEL

EXP_PER_LEVEL = 150


def format_duration(seconds: int) -> str:
    """Return a compact human-readable duration (e.g. '2h 3m', '45s')."""
    try:
        total = int(seconds)
    except Exception:
        total = 0
    total = max(0, total)

    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)

def exp_to_next(level: int) -> int:
    """Flat EXP curve: 150 EXP required per level (until MAX_LEVEL)."""
    try:
        lvl = int(level)
    except Exception:
        lvl = 1
    if lvl >= MAX_LEVEL:
        return 0
    return int(EXP_PER_LEVEL)
