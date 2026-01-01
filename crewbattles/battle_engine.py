import random
from .constants import BASE_HP

ATTACKS = [
    "Pistol", "Gatling", "Bazooka", "Red Hawk", "Diable Jambe", "Oni Giri",
    "King Cobra", "Hiken", "Shishi Sonson", "Rengoku", "Elephant Gun",
]

ARMAMENT_ATTACKS = ["Armament Punch", "Armament Kick", "Armament Strike"]
CONQUEROR_COUNTER = "Conqueror Counter"

def _haki(p: dict):
    h = (p or {}).get("haki", {}) or {}
    arm = int(h.get("armament", 0) or 0)
    obs = int(h.get("observation", 0) or 0)
    conq_unlocked = bool(h.get("conquerors"))
    conq_lvl = int(h.get("conqueror", 0) or 0) if h.get("conqueror") is not None else 0
    return max(0, arm), max(0, obs), conq_unlocked, max(0, conq_lvl)

def _fruit_bonus(fruits_mgr, fruit_name):
    if not fruit_name:
        return 0
    try:
        f = fruits_mgr.get(fruit_name)
    except Exception:
        f = None
    if not isinstance(f, dict):
        return 0
    try:
        return int(f.get("bonus", 0) or 0)
    except Exception:
        return 0

def simulate(p1: dict, p2: dict, fruits_mgr):
    """
    Flat HP: BASE_HP for both players.
    Haki effects only:
      - Armament: increased crit chance
      - Observation: increased dodge chance
      - Conquerors: chance to counter-attack with critical damage
    Returns:
      winner: "p1" or "p2"
      turns: list[(side, dmg, defender_hp_after, attack_name, crit)]
      final_hp1, final_hp2
    """
    hp1 = int(BASE_HP)
    hp2 = int(BASE_HP)

    bonus1 = _fruit_bonus(fruits_mgr, (p1 or {}).get("fruit"))
    bonus2 = _fruit_bonus(fruits_mgr, (p2 or {}).get("fruit"))

    arm1, obs1, conq1, conq_lvl1 = _haki(p1)
    arm2, obs2, conq2, conq_lvl2 = _haki(p2)

    # tuning
    base_dodge = 0.08
    dodge_per_obs = 0.002     # 100 obs => +20%
    max_dodge = 0.35

    base_crit = 0.10
    crit_per_arm = 0.002      # 100 arm => +20%
    max_crit = 0.40
    crit_mult = 1.5

    counter_base = 0.05
    counter_per_lvl = 0.002
    max_counter = 0.30

    def dodge_chance(obs: int) -> float:
        return min(max_dodge, base_dodge + (obs * dodge_per_obs))

    def crit_chance(arm: int) -> float:
        return min(max_crit, base_crit + (arm * crit_per_arm))

    def counter_chance(unlocked: bool, lvl: int) -> float:
        if not unlocked:
            return 0.0
        return min(max_counter, counter_base + (lvl * counter_per_lvl))

    def roll(p: float) -> bool:
        return random.random() < max(0.0, min(1.0, p))

    def base_damage(fruit_bonus: int) -> int:
        # no level scaling
        return max(1, random.randint(12, 20) + int(fruit_bonus or 0))

    turns = []
    attacker = "p1"

    for _ in range(250):
        if hp1 <= 0 or hp2 <= 0:
            break

        if attacker == "p1":
            # p1 -> p2
            if roll(dodge_chance(obs2)):
                turns.append(("p1", 0, hp2, "Dodged", False))
                attacker = "p2"
                continue

            dmg = base_damage(bonus1)
            crit = roll(crit_chance(arm1))
            atk_name = random.choice(ARMAMENT_ATTACKS) if arm1 > 0 else random.choice(ATTACKS)
            if crit:
                dmg = int(dmg * crit_mult)

            hp2 = max(0, hp2 - dmg)
            turns.append(("p1", dmg, hp2, atk_name, crit))
            if hp2 <= 0:
                break

            # conqueror counter from defender (p2)
            if roll(counter_chance(conq2, conq_lvl2)):
                cdmg = int(base_damage(bonus2) * crit_mult)  # counter is critical damage
                hp1 = max(0, hp1 - cdmg)
                turns.append(("p2", cdmg, hp1, CONQUEROR_COUNTER, True))
                if hp1 <= 0:
                    break

            attacker = "p2"
            continue

        # attacker == "p2"
        if roll(dodge_chance(obs1)):
            turns.append(("p2", 0, hp1, "Dodged", False))
            attacker = "p1"
            continue

        dmg = base_damage(bonus2)
        crit = roll(crit_chance(arm2))
        atk_name = random.choice(ARMAMENT_ATTACKS) if arm2 > 0 else random.choice(ATTACKS)
        if crit:
            dmg = int(dmg * crit_mult)

        hp1 = max(0, hp1 - dmg)
        turns.append(("p2", dmg, hp1, atk_name, crit))
        if hp1 <= 0:
            break

        # conqueror counter from defender (p1)
        if roll(counter_chance(conq1, conq_lvl1)):
            cdmg = int(base_damage(bonus1) * crit_mult)
            hp2 = max(0, hp2 - cdmg)
            turns.append(("p1", cdmg, hp2, CONQUEROR_COUNTER, True))
            if hp2 <= 0:
                break

        attacker = "p1"

    winner = "p1" if hp2 <= 0 and hp1 > 0 else "p2"
    return winner, turns, hp1, hp2
