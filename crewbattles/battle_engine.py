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

# small emoji pools to add flair to named attacks
FLARE_EMOJIS = ["🔥", "💥", "⚔️", "🌪️", "🌊", "⚡️", "🌀", "✨", "🔪", "🥊"]
FLARE_SUFFIX = ["!", "!!", "〜", "☆", "✦", "✴️"]

def _flair_named_attack(name: str) -> str:
    """Return attack name decorated with random flair emojis/suffixes."""
    prefix = random.choice(FLARE_EMOJIS)
    suffix = random.choice(FLARE_EMOJIS + FLARE_SUFFIX)
    # avoid duplicate emoji when suffix equals prefix
    if suffix == prefix:
        suffix = random.choice(FLARE_SUFFIX)
    return f"{prefix} {name} {suffix}"

def simulate(p1, p2, fruit_manager=None):
    """
    Simulate a battle between p1 and p2.

    - Named attacks are more frequent for everyone (not only haki users).
    - Haki markers (Armament/Conqueror/Defend) only appear when the user has the haki.
    - Fruit abilities are their own attacks (replace the normal attack) and trigger more frequently,
      throttled per-battle by a short cooldown.
    """
    hp1 = BASE_HP + int(p1.get("level", 1)) * 6
    hp2 = BASE_HP + int(p2.get("level", 1)) * 6

    turns = []

    # per-battle state to prevent spamming
    skip = {"p1": False, "p2": False}
    consec_named = {"p1": 0, "p2": 0}      # consecutive named/haki attacks by attacker
    consec_def_mark = {"p1": 0, "p2": 0}   # consecutive times defender showed a defense marker
    conq_used = {"p1": False, "p2": False} # whether conqueror triggered already for that player

    # fruit ability cooldown in turns (prevents ability every turn)
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

        # Attack/defense/dodge calculations
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
            try:
                fruit_obj = fruit_manager.get(fruit_name)
            except Exception:
                fruit_obj = None
            if fruit_obj and fruit_obj.get("name", "").strip().lower() != fruit_name.strip().lower():
                fruit_obj = None

        # decide whether to use a named attack (more frequent for everyone now)
        base_named_prob = 0.35 + (a_arm * 0.015)      # higher baseline, scales with armament
        base_named_prob = min(0.90, base_named_prob)
        # milder throttle so named attacks are common but not spammy
        named_prob = base_named_prob * (1.0 / (1.0 + consec_named[current] * 0.4))
        use_named = (random.random() < named_prob)

        if use_named:
            raw_attack = random.choice(ATTACKS)
            # add flair emojis to the named attack
            attack_name = _flair_named_attack(raw_attack)
            consec_named[current] += 1
        else:
            attack_name = random.choice(GENERIC_ATTACKS)
            consec_named[current] = 0

        markers = []
        # only show offensive armament marker when attacker actually has armament and it's a named attack
        if a_arm > 0 and use_named:
            markers.append("⚔️")

        base = random.randint(10, 20)
        dmg = int(base * a_atk_mult)

        # Dodge check (no named attack shown on dodge)
        if d_obs > 0 and random.random() < d_dodge:
            consec_named[current] = 0
            consec_def_mark["p1" if current == "p2" else "p2"] = max(0, consec_def_mark["p1" if current == "p2" else "p2"] - 1)
            turns.append((current, 0, hp2 if current == "p1" else hp1, "Dodged", False))
            current = "p2" if current == "p1" else "p1"
            continue

        # Fruit ability trigger (it is its own attack and replaces the normal attack when it happens)
        ability_triggered = False
        if fruit_obj and fruit_obj.get("ability") and fruit_cd[current] == 0:
            bonus = int(fruit_obj.get("bonus", 0))
            # ability chance: higher baseline + scales with fruit bonus, capped reasonably
            ability_chance = min(0.45, 0.12 + bonus * 0.02)
            if random.random() < ability_chance:
                # ability becomes the attack (not an add-on)
                ability_name = str(fruit_obj.get("ability") or "").strip()
                fruit_canonical = str(fruit_obj.get("name") or "").strip()
                # add flair to ability name as well
                attack_name = _flair_named_attack(f"{ability_name} — {fruit_canonical}")
                # ability damage formula: slightly higher base and scales with bonus
                dmg = int(random.randint(12, 24) * (1.0 + (bonus * 0.01)))
                markers = ["✨"]  # mark as fruit ability
                ability_triggered = True
                # throttle further ability triggers for this player
                fruit_cd[current] = 3

                # When ability triggers, do NOT also apply Conqueror or Armament special triggers this turn.
                crit = False
                # apply defender's defense factor and continue to apply it numerically
                dmg = max(0, int(dmg * d_def_factor))

                if current == "p1":
                    hp2 -= dmg
                    hp_after = max(0, hp2)
                else:
                    hp1 -= dmg
                    hp_after = max(0, hp1)

                turns.append((current, int(dmg), int(hp_after), f"{attack_name} {' '.join(markers)}", False))
                # switch turn and continue main loop
                current = "p2" if current == "p1" else "p1"
                continue

        # Conqueror's Haki: limited to once per player per battle (only if unlocked)
        crit = False
        if a_conq_unlocked and (not conq_used[current]) and random.random() < a_conq_chance:
            crit = True
            dmg = int(dmg * a_conq_mult)
            other = "p2" if current == "p1" else "p1"
            skip[other] = True
            markers.append("⚡️")
            conq_used[current] = True
            consec_named[current] += 1

        # defender's armament-as-defense marker: show occasionally to avoid constant "Defend"
        if d_arm > 0:
            def_display_prob = 0.25 + (d_arm * 0.005)
            def_display_prob = min(0.65, def_display_prob)
            def_display_prob = def_display_prob * (1.0 / (1.0 + consec_def_mark["p1" if current == "p2" else "p1"] * 0.6))
            if random.random() < def_display_prob:
                markers.append("🛡️")
                consec_def_mark["p1" if current == "p2" else "p1"] += 1
            else:
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
