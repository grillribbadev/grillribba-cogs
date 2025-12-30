from .constants import BASE_HP
import random

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

def simulate(p1, p2, fruit_manager=None):
    """
    Simulate a battle between p1 and p2.

    New: optional fruit_manager (FruitManager) can be passed so abilities on fruits
    may trigger during a fight. Each fruit object should include 'name', 'bonus',
    and 'ability' fields (ability is a free-text description).

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
    consec_named = {"p1": 0, "p2": 0}
    consec_def_mark = {"p1": 0, "p2": 0}
    conq_used = {"p1": False, "p2": False}
    # fruit ability cooldown (turns until ability can trigger again for that player)
    fruit_cd = {"p1": 0, "p2": 0}

    current = "p1"
    GENERIC_ATTACKS = ["Kick", "Punch", "Hit", "Jab"]

    while hp1 > 0 and hp2 > 0:
        # decrement fruit cooldowns at start of each loop
        for k in fruit_cd:
            if fruit_cd[k] > 0:
                fruit_cd[k] -= 1

        attacker = p1 if current == "p1" else p2
        defender = p2 if current == "p1" else p1

        # handle skip (frightened) status
        if skip[current]:
            turns.append((current, 0, hp2 if current == "p1" else hp1, "Frightened — skipped turn", False))
            skip[current] = False
            consec_named[current] = 0
            current = "p2" if current == "p1" else "p1"
            continue

        a_haki = (attacker.get("haki") or {})
        d_haki = (defender.get("haki") or {})

        a_arm = max(0, int(a_haki.get("armament", 0)))
        a_obs = max(0, int(a_haki.get("observation", 0)))
        a_conq_unlocked = bool(a_haki.get("conquerors"))
        a_conq_lvl = max(0, int(a_haki.get("conqueror", 0)))

        d_arm = max(0, int(d_haki.get("armament", 0)))
        d_obs = max(0, int(d_haki.get("observation", 0)))
        d_conq_unlocked = bool(d_haki.get("conquerors"))
        d_conq_lvl = max(0, int(d_haki.get("conqueror", 0)))

        # Attack/defense/dodge calculations (same as before)
        a_atk_mult = 1.0 + (a_arm * 0.01) if a_arm > 0 else 1.0
        d_def_factor = 1.0 - min(0.50, d_arm * 0.005) if d_arm > 0 else 1.0
        d_dodge = min(0.65, d_obs * 0.01) if d_obs > 0 else 0.0
        if a_conq_unlocked and not conq_used[current]:
            a_conq_chance = min(0.50, 0.06 + a_conq_lvl * 0.0009)
            a_conq_mult = min(3.0, 1.5 + a_conq_lvl * 0.0025)
        else:
            a_conq_chance = 0.0
            a_conq_mult = 1.0

        # fruit data lookup (if provided) — only allow ability if player's fruit matches an entry
        fruit_obj = None
        fruit_name = (attacker.get("fruit") or "")
        if fruit_manager and fruit_name:
            # FruitManager.get is case-insensitive; use it to find the canonical fruit record
            try:
                fruit_obj = fruit_manager.get(fruit_name)
            except Exception:
                fruit_obj = None
            # only use ability when the fruit exists and names match (safety)
            if fruit_obj and fruit_obj.get("name", "").strip().lower() != fruit_name.strip().lower():
                fruit_obj = None

        # decide named attack
        base_named_prob = 0.20 + (a_arm * 0.01)
        base_named_prob = min(0.75, base_named_prob)
        named_prob = base_named_prob * (1.0 / (1.0 + consec_named[current] * 0.6))
        use_named = (random.random() < named_prob) and (a_arm > 0 or a_conq_unlocked)

        if use_named:
            attack_name = random.choice(ATTACKS)
            consec_named[current] += 1
        else:
            attack_name = random.choice(GENERIC_ATTACKS)
            consec_named[current] = 0

        markers = []
        if a_arm > 0 and use_named:
            markers.append("⚔️")

        base = random.randint(10, 20)
        dmg = int(base * a_atk_mult)

        # dodge check
        if d_obs > 0 and random.random() < d_dodge:
            consec_named[current] = 0
            consec_def_mark["p1" if current == "p2" else "p2"] = max(0, consec_def_mark["p1" if current == "p2" else "p2"] - 1)
            turns.append((current, 0, hp2 if current == "p1" else hp1, "🛡️ Dodged the attack!", False))
            current = "p2" if current == "p1" else "p1"
            continue

        # Conqueror
        crit = False
        if a_conq_unlocked and (not conq_used[current]) and random.random() < a_conq_chance:
            crit = True
            dmg = int(dmg * a_conq_mult)
            other = "p2" if current == "p1" else "p1"
            skip[other] = True
            markers.append("⚡️")
            conq_used[current] = True
            consec_named[current] += 1

        # Fruit ability trigger (occasional, throttled)
        ability_triggered = False
        if fruit_obj and fruit_obj.get("ability"):
            # base chance scales with fruit bonus and is throttled by fruit_cd
            bonus = int(fruit_obj.get("bonus", 0))
            chance = min(0.30, 0.08 + (bonus * 0.01))  # e.g. 8% + 1% per bonus point capped at 30%
            if fruit_cd[current] == 0 and random.random() < chance:
                # default ability effect: extra damage scaling with bonus
                extra_mult = 1.0 + min(1.0, 0.10 + bonus * 0.01)  # modest extra multiplier
                extra = int(dmg * (extra_mult - 1.0))
                dmg += extra
                # mark and throttle future triggers for this player for a few turns
                markers.append("✨")
                ability_triggered = True
                fruit_cd[current] = 3  # cooldown in turns
                # include ability text in attack name if short
                ability_text = (fruit_obj.get("ability") or "").split("\n")[0][:32]
                if ability_text:
                    markers.append(f"({ability_text})")

        # defender defend marker
        if d_arm > 0:
            def_display_prob = 0.25 + (d_arm * 0.005)
            def_display_prob = min(0.65, def_display_prob)
            def_display_prob = def_display_prob * (1.0 / (1.0 + consec_def_mark["p1" if current == "p2" else "p1"] * 0.6))
            if random.random() < def_display_prob:
                markers.append("🛡️")
                consec_def_mark["p1" if current == "p2" else "p1"] += 1
            else:
                consec_def_mark["p1" if current == "p2" else "p1"] = max(0, consec_def_mark["p1" if current == "p2" else "p1"] - 1)

        dmg = max(0, int(dmg * d_def_factor))

        if markers:
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
