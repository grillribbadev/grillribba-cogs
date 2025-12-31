from .constants import BASE_HP
import random

# small Flair
ATTACKS = [
    "Pistol", "Gatling", "Bazooka", "Red Hawk", "Diable Jambe", "Oni Giri",
    "King Cobra", "Hiken", "Shishi Sonson", "Rengoku", "Armament Strike",
    "Observation Stab", "Sky Walk Kick", "Elephant Gun"
]

# Haki-themed attacks (only used if the user actually has that haki unlocked/leveled)
ARMAMENT_ATTACKS = [
    "Armament Punch", "Armament Kick", "Armament Strike", "Black Fist", "Hardening Smash"
]
CONQUEROR_ATTACKS = [
    "Conqueror's Burst", "Haoshoku Wave", "King's Pressure"
]

def _haki_dict(p):
    h = p.get("haki", {}) or {}
    # normalize keys that your code uses
    arm = int(h.get("armament", 0) or 0)
    obs = int(h.get("observation", 0) or 0)
    conq_unlocked = bool(h.get("conquerors"))
    conq_lvl = int(h.get("conqueror", 0) or 0) if h.get("conqueror") is not None else 0
    return arm, obs, conq_unlocked, conq_lvl

def _fruit_bonus(fruits_mgr, fruit_name):
    if not fruit_name:
        return 0
    try:
        f = fruits_mgr.get(fruit_name)
    except Exception:
        f = None
    if not f:
        return 0
    try:
        return int(f.get("bonus", 0) or 0)
    except Exception:
        return 0

def simulate(p1: dict, p2: dict, fruits_mgr):
    """
    Returns:
      winner: "p1" or "p2"
      turns: list[(side, dmg, defender_hp_after, attack_name, crit)]
      final_hp1, final_hp2
    Notes:
      - side == "p1" means p1 is the attacker and hp is p2's hp AFTER the hit
      - side == "p2" means p2 is the attacker and hp is p1's hp AFTER the hit
      - attack_name == "Dodged" triggers dodge text in your renderer
      - attack_name contains "Frightened" triggers skip text in your renderer
    """
    lvl1 = int(p1.get("level", 1) or 1)
    lvl2 = int(p2.get("level", 1) or 1)

    max_hp1 = int(BASE_HP + lvl1 * 6)
    max_hp2 = int(BASE_HP + lvl2 * 6)
    hp1 = max_hp1
    hp2 = max_hp2

    fruit1 = p1.get("fruit")
    fruit2 = p2.get("fruit")
    bonus1 = _fruit_bonus(fruits_mgr, fruit1)
    bonus2 = _fruit_bonus(fruits_mgr, fruit2)

    arm1, obs1, conq1, conq_lvl1 = _haki_dict(p1)
    arm2, obs2, conq2, conq_lvl2 = _haki_dict(p2)

    # status flags: if True, that player loses their next turn
    frightened_1 = False
    frightened_2 = False

    turns = []
    attacker = "p1"

    # cap turns to avoid infinite loops
    for _ in range(200):
        if hp1 <= 0 or hp2 <= 0:
            break

        if attacker == "p1":
            atk_p, def_p = p1, p2
            atk_lvl, def_lvl = lvl1, lvl2
            atk_bonus, def_bonus = bonus1, bonus2
            atk_arm, atk_obs, atk_conq, atk_conq_lvl = arm1, obs1, conq1, conq_lvl1
            def_arm, def_obs, def_conq, def_conq_lvl = arm2, obs2, conq2, conq_lvl2
            # skip due to frightened
            if frightened_1:
                frightened_1 = False
                turns.append(("p1", 0, hp2, "Frightened", False))
                attacker = "p2"
                continue

            # defender dodge chance
            base_dodge = 0.06  # regular dodge
            obs_dodge = min(0.22, (def_obs / 500.0))  # up to +22% at 110-ish; obs max is 100 => +0.20
            dodge_roll = random.random()
            if dodge_roll < (base_dodge + obs_dodge):
                turns.append(("p1", 0, hp2, "Dodged", False))
                attacker = "p2"
                continue

            # choose attack type/name (Haki attacks only if user has that haki)
            crit = False
            attack_name = random.choice(ATTACKS)

            # Armament chance scales with armament level; only if armament > 0
            arm_chance = min(0.40, atk_arm / 250.0) if atk_arm > 0 else 0.0
            # Conqueror chance only if unlocked; scales mildly with conqueror level
            conq_chance = (0.06 + min(0.12, atk_conq_lvl / 800.0)) if atk_conq else 0.0

            r = random.random()
            if atk_conq and r < conq_chance:
                attack_name = random.choice(CONQUEROR_ATTACKS)
            elif atk_arm > 0 and r < (conq_chance + arm_chance):
                attack_name = random.choice(ARMAMENT_ATTACKS)

            # damage model (simple but flavorful)
            base = random.randint(10, 18)
            level_scale = int(atk_lvl * 0.9)
            fruit_scale = int(atk_bonus * 1.2)

            # armament adds damage when armament attack happens (and a smaller passive bump)
            passive_arm = int(atk_arm / 20) if atk_arm > 0 else 0
            arm_burst = int(atk_arm / 8) if attack_name in ARMAMENT_ATTACKS else 0

            dmg = base + level_scale + fruit_scale + passive_arm + arm_burst

            # crit
            crit_chance = 0.10
            if attack_name in ARMAMENT_ATTACKS:
                crit_chance += 0.06
            if random.random() < crit_chance:
                crit = True
                dmg = int(dmg * 1.5)

            # conqueror special: small chance to frighten defender (skip next turn)
            if attack_name in CONQUEROR_ATTACKS:
                # frighten chance
                fright_chance = 0.25 + min(0.25, atk_conq_lvl / 400.0)  # 25%..50%
                if random.random() < fright_chance:
                    frightened_2 = True
                # conqueror hits slightly harder
                dmg = int(dmg * 1.15)

            dmg = max(0, int(dmg))
            hp2 = max(0, hp2 - dmg)
            turns.append(("p1", dmg, hp2, attack_name, crit))
            attacker = "p2"
            continue

        else:  # attacker == "p2"
            atk_p, def_p = p2, p1
            atk_lvl, def_lvl = lvl2, lvl1
            atk_bonus, def_bonus = bonus2, bonus1
            atk_arm, atk_obs, atk_conq, atk_conq_lvl = arm2, obs2, conq2, conq_lvl2
            def_arm, def_obs, def_conq, def_conq_lvl = arm1, obs1, conq1, conq_lvl1

            if frightened_2:
                frightened_2 = False
                turns.append(("p2", 0, hp1, "Frightened", False))
                attacker = "p1"
                continue

            base_dodge = 0.06
            obs_dodge = min(0.22, (def_obs / 500.0))
            if random.random() < (base_dodge + obs_dodge):
                turns.append(("p2", 0, hp1, "Dodged", False))
                attacker = "p1"
                continue

            crit = False
            attack_name = random.choice(ATTACKS)

            arm_chance = min(0.40, atk_arm / 250.0) if atk_arm > 0 else 0.0
            conq_chance = (0.06 + min(0.12, atk_conq_lvl / 800.0)) if atk_conq else 0.0

            r = random.random()
            if atk_conq and r < conq_chance:
                attack_name = random.choice(CONQUEROR_ATTACKS)
            elif atk_arm > 0 and r < (conq_chance + arm_chance):
                attack_name = random.choice(ARMAMENT_ATTACKS)

            base = random.randint(10, 18)
            level_scale = int(atk_lvl * 0.9)
            fruit_scale = int(atk_bonus * 1.2)

            passive_arm = int(atk_arm / 20) if atk_arm > 0 else 0
            arm_burst = int(atk_arm / 8) if attack_name in ARMAMENT_ATTACKS else 0

            dmg = base + level_scale + fruit_scale + passive_arm + arm_burst

            crit_chance = 0.10
            if attack_name in ARMAMENT_ATTACKS:
                crit_chance += 0.06
            if random.random() < crit_chance:
                crit = True
                dmg = int(dmg * 1.5)

            if attack_name in CONQUEROR_ATTACKS:
                fright_chance = 0.25 + min(0.25, atk_conq_lvl / 400.0)
                if random.random() < fright_chance:
                    frightened_1 = True
                dmg = int(dmg * 1.15)

            dmg = max(0, int(dmg))
            hp1 = max(0, hp1 - dmg)
            turns.append(("p2", dmg, hp1, attack_name, crit))
            attacker = "p1"
            continue

    winner = "p1" if hp2 <= 0 and hp1 > 0 else "p2"
    return winner, turns, hp1, hp2
