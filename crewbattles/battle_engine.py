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

    # flags to indicate the next turn should be skipped for a side (frightened)
    skip = {"p1": False, "p2": False}

    current = "p1"  # p1 starts
    while hp1 > 0 and hp2 > 0:
        attacker = p1 if current == "p1" else p2
        defender = p2 if current == "p1" else p1

        # handle skip (frightened) status
        if skip[current]:
            turns.append((current, 0, hp2 if current == "p1" else hp1, "Frightened — skipped turn", False))
            skip[current] = False
            current = "p2" if current == "p1" else "p1"
            continue

        # read raw haki values (defensive if missing)
        a_haki = (attacker.get("haki") or {})
        d_haki = (defender.get("haki") or {})

        a_arm = max(0, int(a_haki.get("armament", 0)))
        a_obs = max(0, int(a_haki.get("observation", 0)))
        a_conq = bool(a_haki.get("conquerors"))

        d_arm = max(0, int(d_haki.get("armament", 0)))
        d_obs = max(0, int(d_haki.get("observation", 0)))
        d_conq = bool(d_haki.get("conquerors"))

        # determine whether haki should be applied at all for each side
        a_haki_active = (a_arm > 0) or (a_obs > 0) or a_conq
        d_haki_active = (d_arm > 0) or (d_obs > 0) or d_conq

        # compute derived modifiers (only meaningful if haki active)
        # Attack scaling: each armament point = +1% damage
        a_atk_mult = 1.0 + (a_arm * 0.01) if a_haki_active else 1.0
        # Defense scaling: each armament point reduces incoming damage by 0.5%, capped at 50%
        d_def_factor = 1.0 - min(0.50, d_arm * 0.005) if d_haki_active else 1.0
        # Dodge chance from observation: each obs point = +0.5% dodge, capped at 50%
        d_dodge = min(0.50, d_obs * 0.005) if d_haki_active else 0.0
        # Conqueror effects
        a_conq_chance = 0.06 if a_conq else 0.0
        a_conq_mult = 1.75 if a_conq else 1.0

        attack_name = random.choice(ATTACKS)
        markers = []

        # if attacker has armament, mark offensive armament
        if a_haki_active and a_arm > 0:
            markers.append("🛡 Armament")

        base = random.randint(10, 20)
        # apply attack multiplier (scales with armament level)
        dmg = int(base * a_atk_mult)

        # defender dodge check (observation)
        if random.random() < d_dodge:
            # defender dodged — indicate observation dodge in the display
            attack_display = f"{attack_name} ✨ (Dodged — Observation)"
            turns.append((current, 0, hp2 if current == "p1" else hp1, attack_display, False))
            current = "p2" if current == "p1" else "p1"
            continue

        # Conqueror's Haki: small chance to frighten and deal critical damage
        crit = False
        if a_conq and random.random() < a_conq_chance:
            crit = True
            dmg = int(dmg * a_conq_mult)
            other = "p2" if current == "p1" else "p1"
            skip[other] = True
            markers.append("👑 Conqueror")

        # defender's armament-as-defense marker
        if d_haki_active and d_arm > 0:
            markers.append("🛡(Def)")

        # apply defender's defense factor (scales with defender armament)
        dmg = max(0, int(dmg * d_def_factor))

        # compile final attack name with haki markers if any
        if markers:
            attack_name = f"{attack_name} {' '.join(markers)}"

        # apply damage to the proper target
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
