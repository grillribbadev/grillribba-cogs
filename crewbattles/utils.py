def exp_to_next(level: int) -> int:
    """
    Simple EXP curve: next = 100 * level^1.8 (rounded)
    Keeps leveling reasonable; adapt as needed.
    """
    try:
        lvl = max(1, int(level))
    except Exception:
        lvl = 1
    return int(round(100 * (lvl ** 1.8)))
