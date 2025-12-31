import random
from .constants import BASE_HP

# small Flair
ATTACKS = [
    "Pistol", "Gatling", "Bazooka", "Red Hawk", "Diable Jambe", "Oni Giri",
    "King Cobra", "Hiken", "Shishi Sonson", "Rengoku", "Armament Strike",
    "Observation Stab", "Sky Walk Kick", "Elephant Gun"
]
FLARE = ["🔥","⚡️","💥","✨","🌪️","🔪","🥊"]

def _flair(name):
    return f"{random.choice(FLARE)} {name} {random.choice(FLARE)}"

def simulate(p1, p2, fruits):
    """
    Lightweight deterministic-ish simulate used by the cog.
    Returns (winner, turns, final_hp1, final_hp2)
    turns: list of tuples (side, dmg, hp_after, attack_name, crit)
    side: "p1" or "p2"
    """
    max_hp1 = BASE_HP + int(p1.get("level", 1)) * 6
    max_hp2 = BASE_HP + int(p2.get("level", 1)) * 6
    hp1 = max_hp1
    hp2 = max_hp2
    turns = []

    # state
    conq_used = {"p1": False, "p2": False}
    fruit_cd = {"p1": 0, "p2": 0}
    consec_named = {"p1": 0, "p2": 0}

    current = "p1"
    while hp1 > 0 and hp2 > 0:
        attacker = p1 if current == "p1" else p2
        defender = p2 if current == "p1" else p1

        # decrement cooldowns
        for k in fruit_cd:
            if fruit_cd[k] > 0:
                fruit_cd[k] -= 1

        # base attack selection
        # more frequent named attacks
        named_prob = 0.45 + (int((attacker.get("haki") or {}).get("armament",0)) * 0.01)
        named_prob = min(0.9, named_prob * (1.0 / (1.0 + consec_named[current]*0.35)))
        use_named = random.random() < named_prob

        if use_named:
            attack_name = _flair(random.choice(ATTACKS))
            consec_named[current] += 1
        else:
            attack_name = random.choice(["Kick","Punch","Jab","Hit"])
            consec_named[current] = 0

        # fruit ability as its own attack (if owned and off-cd)
        fruit_obj = None
        fname = (attacker.get("fruit") or "")
        if fruits and fname:
            fruit_obj = fruits.get(fname)
        if fruit_obj and fruit_cd[current] == 0:
            bonus = int(fruit_obj.get("bonus", 0))
            chance = min(0.5, 0.15 + bonus*0.03)
            if random.random() < chance:
                ability = fruit_obj.get("ability","Ability")
                attack_name = f"{ability} — {fruit_obj.get('name')}"
                dmg = int(random.randint(12, 26) * (1 + bonus*0.01))
                fruit_cd[current] = 3
                markers = True
            else:
                markers = False
        else:
            markers = False

        # IMPORTANT: crit must always be defined each turn
        crit = False
        attack_name = "Attack"

        # base damage
        if not markers:
            dmg = random.randint(8, 18)
            # haki armament bonus
            arm = int((attacker.get("haki") or {}).get("armament", 0) or 0)
            dmg = int(dmg * (1.0 + arm*0.01))
            # conqueror possibility (only if unlocked)
            if (attacker.get("haki") or {}).get("conquerors"):
                if not conq_used[current] and random.random() < 0.06:
                    dmg = int(dmg * 1.8)
                    conq_used[current] = True
                    attack_name = f"Conqueror's {attack_name} ⚡️"
            # crit small chance
            if random.random() < 0.06:
                dmg = int(dmg * 1.5)
                crit = True

        # defender observation dodge
        d_obs = int((defender.get("haki") or {}).get("observation", 0) or 0)
        dodge_chance = min(0.6, d_obs * 0.01)
        if random.random() < dodge_chance:
            turns.append((current, 0, hp2 if current=="p1" else hp1, "Dodged", False))
            current = "p2" if current=="p1" else "p1"
            continue

        # apply damage and append
        if current == "p1":
            hp2 = max(0, hp2 - int(dmg))
            hp_after = hp2
        else:
            hp1 = max(0, hp1 - int(dmg))
            hp_after = hp1

        turns.append((current, int(dmg), int(hp_after), attack_name, bool(crit)))
        current = "p2" if current == "p1" else "p1"

    winner = "p1" if hp2 <= 0 and hp1 > 0 else ("p2" if hp1 <= 0 and hp2 > 0 else ("p1" if hp2<=0 else "p2"))
    return winner, turns, max(0,int(hp1)), max(0,int(hp2))
