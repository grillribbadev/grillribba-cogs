def haki_bonus(player: dict) -> float:
    """
    Small helper returning an attack multiplier based on player's armament haki.
    (Used optionally in battle engine.)
    """
    try:
        haki = player.get("haki", {}) or {}
        arm = int(haki.get("armament", 0))
    except Exception:
        arm = 0
    return 1.0 + (arm * 0.01)

def get_haki_effects(player):
    """
    Return a dict of derived Haki effects for battle calculations.

    Effects:
    - armament: increases attack and defense (each point gives small bonuses)
    - observation: increases dodge chance (probability between 0.0 - 0.5)
    - conquerors: if unlocked, gives a small chance to frighten the enemy (skip next turn)
      and to make the attack critical (damage multiplier).
    """
    haki = (player or {}).get("haki", {}) or {}
    arm = int(haki.get("armament", 0))
    obs = int(haki.get("observation", 0))
    conquer = bool(haki.get("conquerors", False))

    # Tunable coefficients:
    atk_bonus = arm * 0.5          # extra damage per armament point
    def_bonus = arm * 0.3          # damage reduction per armament point
    dodge_chance = min(0.5, obs * 0.004)  # each obs point -> 0.004 dodge, capped at 50%
    conqueror_chance = 0.05 if conquer else 0.0  # 5% chance to trigger
    conqueror_mult = 1.5          # critical damage multiplier when conqueror triggers

    return {
        "atk_bonus": atk_bonus,
        "def_bonus": def_bonus,
        "dodge": dodge_chance,
        "conqueror_chance": conqueror_chance,
        "conqueror_mult": conqueror_mult,
        "conqueror_unlocked": conquer,
    }
