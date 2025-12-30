import random

BASIC_ATTACKS = [
    "Punch",
    "Kick",
    "Sword Slash",
    "Gun Shot",
]

HARD_ATTACKS = [
    "Gomu Gomu no Pistol",
    "Oni Giri",
    "Fire Fist",
    "Thunder Bagua",
    "Divine Departure",
]

HAKI_ATTACKS = [
    "Armament Haki Strike",
    "Black Blade Slash",
    "Observation Counter",
]


def simulate(p1, p2):
    hp1 = 100 + p1["level"] * 6
    hp2 = 100 + p2["level"] * 6

    turns = []
    attacker = "p1"

    while hp1 > 0 and hp2 > 0:
        crit = random.random() < 0.08  # 8% crit chance
        dmg = random.randint(8, 14)

        attack = random.choice(BASIC_ATTACKS)

        # Haki influence
        if random.random() < 0.25:
            if (
                (attacker == "p1" and p1["haki"].get("armament", 0) > 0)
                or (attacker == "p2" and p2["haki"].get("armament", 0) > 0)
            ):
                attack = random.choice(HAKI_ATTACKS)
                dmg += 4

        # Rare named attacks
        if random.random() < 0.12:
            attack = random.choice(HARD_ATTACKS)
            dmg += 6

        if crit:
            dmg = int(dmg * 1.8)
            attack = f"{attack} 💥"

        if attacker == "p1":
            hp2 = max(0, hp2 - dmg)
            turns.append(("p1", dmg, hp2, attack, crit))
            attacker = "p2"
        else:
            hp1 = max(0, hp1 - dmg)
            turns.append(("p2", dmg, hp1, attack, crit))
            attacker = "p1"

    winner = "p1" if hp1 > 0 else "p2"
    return winner, turns, hp1, hp2