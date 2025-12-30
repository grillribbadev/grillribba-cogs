import random
from .constants import BASE_HP
from .haki import get_haki_effects

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

    # flags to indicate the next turn should be skipped for a side
    skip = {"p1": False, "p2": False}

    current = "p1"  # p1 starts
    while hp1 > 0 and hp2 > 0:
        attacker = p1 if current == "p1" else p2
        defender = p2 if current == "p1" else p1

        # handle skip (frightened) status
        if skip[current]:
            # attacker is frightened/skipping their action
            turns.append((current, 0, hp2 if current == "p1" else hp1, "Frightened - Skipped", False))
            skip[current] = False
            # switch turn
            current = "p2" if current == "p1" else "p1"
            continue

        a_eff = get_haki_effects(attacker)
        d_eff = get_haki_effects(defender)

        attack_name = random.choice(ATTACKS)
        base = random.randint(10, 20)
        dmg = base + int(a_eff["atk_bonus"])

        # defender dodge check (observation)
        if random.random() < d_eff["dodge"]:
            # defender dodged
            crit = False
            # HP does not change
            hp_after = hp2 if current == "p1" else hp1
            turns.append((current, 0, hp_after, attack_name + " (Dodged)", False))
            # switch turn
            current = "p2" if current == "p1" else "p1"
            continue

        # Conqueror's Haki: small chance to frighten and deal critical damage
        crit = False
        if random.random() < a_eff["conqueror_chance"]:
            crit = True
            dmg = int(dmg * a_eff["conqueror_mult"])
            # frighten defender: they will skip their next turn
            other = "p2" if current == "p1" else "p1"
            skip[other] = True

        # apply defender's defense bonus (reduce incoming damage)
        dmg = max(0, dmg - int(d_eff["def_bonus"]))

        # apply damage
        if current == "p1":
            hp2 -= dmg
            hp_after = max(0, hp2)
        else:
            hp1 -= dmg
            hp_after = max(0, hp1)

        turns.append((current, int(dmg), int(hp_after), attack_name, bool(crit)))

        # switch turn
        current = "p2" if current == "p1" else "p1"

    winner = "p1" if hp2 <= 0 and hp1 > 0 else ("p2" if hp1 <= 0 and hp2 > 0 else ("p1" if hp2 <= 0 else "p2"))
    return winner, turns, max(0, int(hp1)), max(0, int(hp2))
