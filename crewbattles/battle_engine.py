import random
from .constants import BASE_HP

# Named attacks used by the engine when a name is needed
ATTACKS = [
    "Pistol",
    "Gatling",
    "Bazooka",
    "Red Hawk",
    "Diable Jambe",
    "Oni Giri",
    "King Cobra",
    "Hiken",
    "Shishi Sonson",
    "Rengoku",
    "Armament Strike",
    "Observation Stab",
    "Sky Walk Kick",
    "Elephant Gun",
]

def simulate(p1, p2):
    """
    Simulate a battle between p1 and p2.

    Returns:
      winner: "p1" or "p2"
      turns: list of tuples (side, dmg, hp_after, attack_name, crit:bool)
      hp1, hp2: final HP (ints, floored at 0)
    """
    hp1 = BASE_HP + int(p1.get("level", 1)) * 6
    hp2 = BASE_HP + int(p2.get("level", 1)) * 6

    turns = []

    skip = {"p1": False, "p2": False}
    current = "p1"

    GENERIC_ATTACKS = ["Kick", "Punch", "Hit", "Jab"]

    while hp1 > 0 and hp2 > 0:
        attacker = p1 if current == "p1" else p2
        defender = p2 if current == "p1" else p1

        # handle skip (frightened) status
        if skip[current]:
            turns.append((current, 0, hp2 if current == "p1" else hp1, "Frightened — skipped turn", False))
            skip[current] = False
            current = "p2" if current == "p1" else "p1"
            continue

        a_haki = (attacker.get("haki") or {})
        d_haki = (defender.get("haki") or {})

        a_arm = max(0, int(a_haki.get("armament", 0)))
        a_obs = max(0, int(a_haki.get("observation", 0)))
        a_conq = bool(a_haki.get("conquerors"))

        d_arm = max(0, int(d_haki.get("armament", 0)))
        d_obs = max(0, int(d_haki.get("observation", 0)))
        d_conq = bool(d_haki.get("conquerors"))

        # Attack scaling: each armament point = +1% damage (only if attacker has armament > 0)
        a_atk_mult = 1.0 + (a_arm * 0.01) if a_arm > 0 else 1.0
        # Defense scaling: each armament point reduces incoming damage by 0.5%, capped at 50% (only if defender has armament > 0)
        d_def_factor = 1.0 - min(0.50, d_arm * 0.005) if d_arm > 0 else 1.0
        # Dodge chance from observation: each obs point = +0.5% dodge, capped at 50% (only if defender has observation > 0)
        d_dodge = min(0.50, d_obs * 0.005) if d_obs > 0 else 0.0
        # Conqueror effects only if conqueror is unlocked for attacker
        a_conq_chance = 0.06 if a_conq else 0.0
        a_conq_mult = 1.75 if a_conq else 1.0

        # Only Armament/Conqueror attacks get named attacks, else generic
        if a_arm > 0 or a_conq:
            attack_name = random.choice(ATTACKS)
        else:
            attack_name = random.choice(GENERIC_ATTACKS)

        markers = []

        if a_arm > 0:
            markers.append("🛡 Armament")

        base = random.randint(10, 20)
        dmg = int(base * a_atk_mult)

        # Dodge check
        if d_obs > 0 and random.random() < d_dodge:
            turns.append((current, 0, hp2 if current == "p1" else hp1, "Dodged the attack!", False))
            current = "p2" if current == "p1" else "p1"
            continue

        # Conqueror's Haki
        crit = False
        if a_conq and random.random() < a_conq_chance:
            crit = True
            dmg = int(dmg * a_conq_mult)
            other = "p2" if current == "p1" else "p1"
            skip[other] = True
            markers.append("👑 Conqueror")

        if d_arm > 0:
            markers.append("🛡(Def)")

        dmg = max(0, int(dmg * d_def_factor))

        # Only show markers for haki attacks
        if (a_arm > 0 or a_conq or d_arm > 0) and markers:
            attack_name = f"{attack_name} {' '.join(markers)}"

        if current == "p1":
            hp2 -= dmg
            hp_after = max(0, hp2)
        else:
            hp1 -= dmg
            hp_after = max(0, hp1)

        turns.append((current, int(dmg), int(hp_after), attack_name, bool(crit)))
        current = "p2" if current == "p1" else "p1"

    winner = "p1" if hp2 <= 0 and hp1 > 0 else ("p2" if hp1 <= 0 and hp2 > 0 else ("p1" if hp2 <= 0 else "p2"))
    return winner, turns, max(0, int(hp1)), max(0, int(hp2))
