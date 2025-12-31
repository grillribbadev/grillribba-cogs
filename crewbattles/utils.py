from .constants import MAX_LEVEL

EXP_PER_LEVEL = 150

def exp_to_next(level: int) -> int:
    """Flat EXP curve: 150 EXP required per level (until MAX_LEVEL)."""
    try:
        lvl = int(level)
    except Exception:
        lvl = 1
    if lvl >= MAX_LEVEL:
        return 0
    return EXP_PER_LEVEL
