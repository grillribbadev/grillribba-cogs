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

    # per-battle state to prevent spamming
    skip = {"p1": False, "p2": False}
    consec_named = {"p1": 0, "p2": 0}      # consecutive named/haki attacks by attacker
    consec_def_mark = {"p1": 0, "p2": 0}   # consecutive times defender showed a defense marker
    conq_used = {"p1": False, "p2": False} # whether conqueror triggered already for that player

    current = "p1"

    GENERIC_ATTACKS = ["Kick", "Punch", "Hit", "Jab"]

    while hp1 > 0 and hp2 > 0:
        attacker = p1 if current == "p1" else p2
        defender = p2 if current == "p1" else p1

        # handle skip (frightened) status
        if skip[current]:
            turns.append((current, 0, hp2 if current == "p1" else hp1, "Frightened — skipped turn", False))
            skip[current] = False
            # reset consecutive named (they were forced to skip)
            consec_named[current] = 0
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
        # Dodge chance from observation: each obs point = +1% dodge, capped at 65% (more frequent)
        d_dodge = min(0.65, d_obs * 0.01) if d_obs > 0 else 0.0
        # Conqueror base chance, but limited to once per player per battle
        a_conq_chance = 0.06 if a_conq and not conq_used[current] else 0.0
        a_conq_mult = 1.75 if a_conq else 1.0

        # decide whether this attack is named (haki flavored) or generic
        base_named_prob = 0.20 + (a_arm * 0.01)      # scales with armament
        base_named_prob = min(0.75, base_named_prob)
        # reduce probability if attacker has used named attacks consecutively
        named_prob = base_named_prob * (1.0 / (1.0 + consec_named[current] * 0.6))
        use_named = (random.random() < named_prob) and (a_arm > 0 or a_conq)

        if use_named:
            attack_name = random.choice(ATTACKS)
            consec_named[current] += 1
        else:
            attack_name = random.choice(GENERIC_ATTACKS)
            consec_named[current] = 0

        markers = []

        # offensive armament marker when attacker has armament (only mark sometimes to avoid spam)
        if a_arm > 0 and use_named:
            markers.append("⚔️")

        base = random.randint(10, 20)
        dmg = int(base * a_atk_mult)

        # Dodge check — more frequent now; simple message without attack name
        if d_obs > 0 and random.random() < d_dodge:
            # reset consecutive named for attacker (they missed)
            consec_named[current] = 0
            # reduce defender consecutive defend marker growth (dodge is not same as marking defend)
            consec_def_mark["p1" if current == "p2" else "p2"] = max(0, consec_def_mark["p1" if current == "p2" else "p2"] - 1)
            turns.append((current, 0, hp2 if current == "p1" else hp1, "🛡️ Dodged the attack!", False))
            current = "p2" if current == "p1" else "p1"
            continue

        # Conqueror's Haki: limited to once per player per battle
        crit = False
        if a_conq and (not conq_used[current]) and random.random() < a_conq_chance:
            crit = True
            dmg = int(dmg * a_conq_mult)
            other = "p2" if current == "p1" else "p1"
            skip[other] = True
            markers.append("⚡️")
            conq_used[current] = True
            # using conqueror reduces chance to use named next turn (increase consec_named to throttle)
            consec_named[current] += 1

        # defender's armament-as-defense marker: show only occasionally to avoid constant "Defend"
        if d_arm > 0:
            # base chance to display defend marker depends on defender armament
            def_display_prob = 0.25 + (d_arm * 0.005)
            def_display_prob = min(0.65, def_display_prob)
            # reduce when used consecutively
            def_display_prob = def_display_prob * (1.0 / (1.0 + consec_def_mark["p1" if current == "p2" else "p1"] * 0.6))
            if random.random() < def_display_prob:
                markers.append("🛡️")
                consec_def_mark["p1" if current == "p2" else "p1"] += 1
            else:
                # not showing statement this round; reduce consecutive counter slightly
                consec_def_mark["p1" if current == "p2" else "p1"] = max(0, consec_def_mark["p1" if current == "p2" else "p1"] - 1)

        # apply defender's defense factor (scales with defender armament) — numeric always applies
        dmg = max(0, int(dmg * d_def_factor))

        # append markers only when relevant
        if markers:
            attack_name = f"{attack_name} {' '.join(markers)}"

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
