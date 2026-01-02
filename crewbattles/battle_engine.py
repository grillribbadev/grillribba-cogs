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


def _fruit_data(fruits_mgr, fruit_name: str):
    """Try shop first, then pool (so owned fruits still work even if not stocked)."""
    if not fruit_name:
        return None
    f = None
    try:
        f = fruits_mgr.get(fruit_name)
    except Exception:
        f = None
    if not isinstance(f, dict):
        try:
            f = fruits_mgr.pool_get(fruit_name)
        except Exception:
            f = None
    return f if isinstance(f, dict) else None


def _fruit_bonus(fruits_mgr, fruit_name):
    f = _fruit_data(fruits_mgr, fruit_name)
    if not f:
        return 0
    try:
        return int(f.get("bonus", 0) or 0)
    except Exception:
        return 0


def _fruit_ability(fruits_mgr, fruit_name: str) -> str:
    f = _fruit_data(fruits_mgr, fruit_name)
    if not f:
        return ""
    return str(f.get("ability", "") or "").strip()


def _ability_profile(ability: str) -> dict:
    """
    Ability effects are intentionally small and readable.
    All of these ONLY apply if the player owns the fruit (equipped fruit provides the ability string).
    """
    a = (ability or "").strip().lower()

    # Common knobs:
    # - dodge_bonus: adds directly to dodge chance
    # - crit_bonus: adds directly to crit chance
    # - shield_start: absorb damage before HP
    # - on_hit_bonus: (chance, extra_damage)
    # - on_hit_weaken: (chance, weaken_mult_for_target_next_attack)  e.g. 0.75 means target's next dmg * 0.75
    # - on_hit_dodge_down: (chance, dodge_penalty, turns)
    # - room_cancel_dodge: chance to cancel a successful dodge
    # - anti_counter: (chance, turns) disables conqueror counter while active
    # - double_strike: (chance, extra_hit_mult) extra hit damage multiplier of base hit

    profiles = {
        "rubber resilience": {"soak": (0.15, 0.40)},  # 15% reduce incoming dmg by 40%
        "flame burst": {"on_hit_bonus": (0.20, 6)},
        "ice prison": {"on_hit_weaken": (0.15, 0.75)},
        "thunderclap": {"on_hit_bonus": (0.12, 8)},
        "smoke screen": {"dodge_bonus": 0.04},
        "sand coffin": {"on_hit_dodge_down": (0.15, 0.05, 2)},
        "magma fist": {"on_hit_bonus": (0.15, 10)},
        "dragon's breath": {"double_strike": (0.10, 0.50)},
        "phoenix flames": {"shield_start": 10},
        "kitsune mirage": {"dodge_bonus": 0.05},
        "jurassic rampage": {"crit_bonus": 0.05},
        "mammoth guard": {"soak": (0.10, 0.50)},
        "venom coating": {"on_hit_bonus": (0.15, 5)},
        "gravity well": {"on_hit_dodge_down": (0.12, 0.06, 1), "on_hit_bonus": (0.10, 4)},
        "room": {"room_cancel_dodge": 0.20},
        "split body": {"dodge_bonus": 0.03},
        "slipstream": {"dodge_bonus": 0.03},
        "dark vortex": {"anti_counter": (0.15, 2)},
        "light speed": {"crit_bonus": 0.04},
        "gas chamber": {"on_hit_bonus": (0.10, 6)},
        "forest bind": {"on_hit_weaken": (0.10, 0.80)},
        "quake shockwave": {"on_hit_bonus": (0.08, 12)},
        "string snare": {"on_hit_weaken": (0.10, 0.80)},
        "soul pledge": {"on_hit_bonus": (0.10, 7)},
        "barrier wall": {"shield_start": 15},
        "mochi trap": {"on_hit_weaken": (0.12, 0.70)},
        "dice blade": {"on_hit_bonus": (0.12, 6)},
        "mythic howl": {"crit_bonus": 0.06},
        "golden impact": {"shield_start": 12},
        "griffin talon": {"double_strike": (0.10, 0.40)},
        "saber pounce": {"on_hit_bonus": (0.12, 7)},
        "trike gore": {"on_hit_bonus": (0.12, 7)},
        "giraffe whip": {"on_hit_bonus": (0.10, 5)},
        "bubble prison": {"on_hit_weaken": (0.12, 0.75)},
        "weight smash": {"on_hit_bonus": (0.10, 9)},
    }
    return profiles.get(a, {})


def _fruit_tech_chance(ability: str, fruit_bonus: int) -> float:
    """
    Devil Fruit technique proc chance.
    - ONLY if user has a fruit ability
    - Intentionally low frequency (roughly crit-tier but not spammy)
    """
    if not (ability or "").strip():
        return 0.0
    b = int(fruit_bonus or 0)
    # 6% base + up to +2% from bonus, capped at 8%
    return min(0.08, 0.06 + (max(0, min(10, b)) * 0.002))


def simulate(p1: dict, p2: dict, fruits_mgr):
    """
    Flat HP: BASE_HP for both players.
    Haki effects:
      - Armament: crit chance
      - Observation: dodge chance
      - Conquerors: counter-attack with critical damage
    Fruit effects:
      - bonus damage (existing 'bonus')
      - + ability procs/passives (NEW) only if user owns the fruit
    Returns:
      winner: "p1" or "p2"
      turns: list[(side, dmg, defender_hp_after, attack_name, crit)]
      final_hp1, final_hp2
    """
    hp1 = int(BASE_HP)
    hp2 = int(BASE_HP)

    fruit1 = (p1 or {}).get("fruit")
    fruit2 = (p2 or {}).get("fruit")

    bonus1 = _fruit_bonus(fruits_mgr, fruit1)
    bonus2 = _fruit_bonus(fruits_mgr, fruit2)

    ability1 = _fruit_ability(fruits_mgr, fruit1)
    ability2 = _fruit_ability(fruits_mgr, fruit2)

    a1 = _ability_profile(ability1)
    a2 = _ability_profile(ability2)

    state = {
        "p1": {
            "shield": int(a1.get("shield_start", 0) or 0),
            "weaken_mult": 1.0,  # applied to NEXT outgoing attack, then reset
            "dodge_penalty": 0.0,
            "dodge_penalty_turns": 0,
            "anti_counter_turns": 0,
        },
        "p2": {
            "shield": int(a2.get("shield_start", 0) or 0),
            "weaken_mult": 1.0,
            "dodge_penalty": 0.0,
            "dodge_penalty_turns": 0,
            "anti_counter_turns": 0,
        },
    }

    arm1, obs1, conq1, conq_lvl1 = _haki(p1)
    arm2, obs2, conq2, conq_lvl2 = _haki(p2)

    # tuning (UPDATED)
    base_dodge = 0.06
    dodge_per_obs = 0.003   # was 0.002
    max_dodge = 0.45        # was 0.35

    base_crit = 0.08
    crit_per_arm = 0.003    # was 0.002
    max_crit = 0.50         # was 0.40
    crit_mult = 1.5

    counter_base = 0.05
    counter_per_lvl = 0.002
    max_counter = 0.30

    def roll(p: float) -> bool:
        return random.random() < max(0.0, min(1.0, p))

    def attack_name_for(arm: int) -> str:
        """Pick a move name.

        Keep normal attacks as the baseline for variety.
        If the attacker has armament, occasionally show an armament-themed move name.
        """
        a = max(0, int(arm or 0))
        if a <= 0:
            return random.choice(ATTACKS)
        # 10% base + up to +20% at 100 armament, capped at 35%
        armament_name_chance = min(0.35, 0.10 + (a * 0.002))
        if roll(armament_name_chance):
            return random.choice(ARMAMENT_ATTACKS)
        return random.choice(ATTACKS)

    def dodge_chance(side: str, obs: int, ability_profile: dict) -> float:
        bonus = float(ability_profile.get("dodge_bonus", 0.0) or 0.0)
        pen = float(state[side]["dodge_penalty"] or 0.0) if state[side]["dodge_penalty_turns"] > 0 else 0.0
        return min(max_dodge, max(0.0, base_dodge + (obs * dodge_per_obs) + bonus - pen))

    def crit_chance(arm: int, ability_profile: dict) -> float:
        bonus = float(ability_profile.get("crit_bonus", 0.0) or 0.0)
        return min(max_crit, max(0.0, base_crit + (arm * crit_per_arm) + bonus))

    def counter_chance(side: str, unlocked: bool, lvl: int) -> float:
        if not unlocked:
            return 0.0
        if state[side]["anti_counter_turns"] > 0:
            return 0.0
        return min(max_counter, counter_base + (lvl * counter_per_lvl))

    def base_damage(fruit_bonus: int, arm: int) -> int:
        # small deterministic edge: +0..10 damage at arm 0..100
        arm_flat = max(0, min(10, int(arm or 0) // 10))
        return max(1, random.randint(12, 20) + int(fruit_bonus or 0) + arm_flat)

    def apply_armament_reduction(dmg: int, arm: int) -> int:
        # up to 20% reduction at 100 armament (small but very noticeable over many turns)
        pct = min(0.20, max(0.0, (int(arm or 0) * 0.002)))
        return int(dmg * (1.0 - pct))

    def apply_defense_soak(def_side: str, dmg: int, ability_profile: dict, def_arm: int) -> int:
        # NEW: armament always reduces incoming damage a bit
        dmg = apply_armament_reduction(dmg, def_arm)

        soak = ability_profile.get("soak")
        if soak and isinstance(soak, tuple) and len(soak) == 2:
            chance, pct = float(soak[0]), float(soak[1])
            if roll(chance):
                dmg = int(dmg * (1.0 - max(0.0, min(0.9, pct))))
        return max(0, int(dmg))

    def apply_shield(def_side: str, dmg: int) -> int:
        sh = int(state[def_side]["shield"] or 0)
        if sh <= 0 or dmg <= 0:
            return dmg
        absorbed = min(sh, dmg)
        state[def_side]["shield"] = sh - absorbed
        return dmg - absorbed

    def maybe_on_hit(att_ability: dict, def_side: str) -> str:
        """Apply on-hit effects; returns a short suffix to add to attack name."""
        suffix = ""

        # bonus damage
        ohb = att_ability.get("on_hit_bonus")
        if ohb and isinstance(ohb, tuple) and len(ohb) == 2:
            chance, extra = float(ohb[0]), int(ohb[1])
            if roll(chance):
                suffix += " ✨"
                return ("BONUS", extra, suffix)

        # weaken target next attack
        ohw = att_ability.get("on_hit_weaken")
        if ohw and isinstance(ohw, tuple) and len(ohw) == 2:
            chance, mult = float(ohw[0]), float(ohw[1])
            if roll(chance):
                state[def_side]["weaken_mult"] = max(0.3, min(1.0, mult))
                suffix += " 🧊"
                return ("WEAKEN", 0, suffix)

        # dodge down
        ohd = att_ability.get("on_hit_dodge_down")
        if ohd and isinstance(ohd, tuple) and len(ohd) == 3:
            chance, pen, turns = float(ohd[0]), float(ohd[1]), int(ohd[2])
            if roll(chance):
                state[def_side]["dodge_penalty"] = max(0.0, min(0.2, pen))
                state[def_side]["dodge_penalty_turns"] = max(1, min(5, turns))
                suffix += " 🏜️"
                return ("DODGEDOWN", 0, suffix)

        # anti-counter
        ac = att_ability.get("anti_counter")
        if ac and isinstance(ac, tuple) and len(ac) == 2:
            chance, turns = float(ac[0]), int(ac[1])
            if roll(chance):
                state[def_side]["anti_counter_turns"] = max(state[def_side]["anti_counter_turns"], max(1, min(5, turns)))
                suffix += " 🌑"
                return ("ANTICOUNTER", 0, suffix)

        return ("NONE", 0, suffix)

    turns = []
    attacker = random.choice(("p1", "p2"))  # was: attacker = "p1"

    for _ in range(250):
        if hp1 <= 0 or hp2 <= 0:
            break

        # decay timers
        for side in ("p1", "p2"):
            if state[side]["dodge_penalty_turns"] > 0:
                state[side]["dodge_penalty_turns"] -= 1
            if state[side]["anti_counter_turns"] > 0:
                state[side]["anti_counter_turns"] -= 1

        if attacker == "p1":
            # p1 -> p2
            # dodge check (with Room cancel-dodge)
            dodged = roll(dodge_chance("p2", obs2, a2))
            if dodged and a1.get("room_cancel_dodge"):
                if roll(float(a1["room_cancel_dodge"])):
                    dodged = False  # "Room" cancels dodge
            if dodged:
                turns.append(("p1", 0, hp2, "Dodged", False))
                attacker = "p2"
                continue

            dmg = base_damage(bonus1, arm1)  # was base_damage(bonus1)

            dmg = int(dmg * float(state["p1"]["weaken_mult"] or 1.0))
            state["p1"]["weaken_mult"] = 1.0

            # NEW: Devil Fruit Technique (does NOT stack with crit)
            fruit_tech = roll(_fruit_tech_chance(ability1, bonus1))
            if fruit_tech:
                crit = False
                atk_name = f"🍈 {ability1}"
                dmg = int(dmg * crit_mult)  # crit-tier damage
            else:
                crit = roll(crit_chance(arm1, a1))
                atk_name = attack_name_for(arm1)
                if crit:
                    dmg = int(dmg * crit_mult)

            # defender fruit defense effects
            dmg = apply_defense_soak("p2", dmg, a2, arm2)  # was apply_defense_soak("p2", dmg, a2)
            dmg = apply_shield("p2", dmg)

            # attacker fruit on-hit effects
            kind, extra, suffix = maybe_on_hit(a1, "p2")
            if kind == "BONUS":
                dmg += int(extra)

            hp2 = max(0, hp2 - max(0, dmg))
            turns.append(("p1", dmg, hp2, f"{atk_name}{suffix}", crit))
            if hp2 <= 0:
                break

            # optional double strike
            ds = a1.get("double_strike")
            if ds and isinstance(ds, tuple) and len(ds) == 2 and roll(float(ds[0])):
                extra_hit = int(max(1, dmg) * float(ds[1]))
                extra_hit = apply_defense_soak("p2", extra_hit, a2, arm2)  # FIX: pass defender armament
                extra_hit = apply_shield("p2", extra_hit)
                hp2 = max(0, hp2 - extra_hit)
                turns.append(("p1", extra_hit, hp2, "⚡ Double Strike", False))
                if hp2 <= 0:
                    break

            # conqueror counter from defender (p2)
            if roll(counter_chance("p2", conq2, conq_lvl2)):
                cdmg = int(base_damage(bonus2, arm2) * crit_mult)  # was base_damage(bonus2)
                cdmg = apply_defense_soak("p1", cdmg, a1, arm1)
                cdmg = apply_shield("p1", cdmg)
                hp1 = max(0, hp1 - cdmg)
                turns.append(("p2", cdmg, hp1, CONQUEROR_COUNTER, True))
                if hp1 <= 0:
                    break

            attacker = "p2"
            continue

        # attacker == "p2"
        dodged = roll(dodge_chance("p1", obs1, a1))
        if dodged and a2.get("room_cancel_dodge"):
            if roll(float(a2["room_cancel_dodge"])):
                dodged = False
        if dodged:
            turns.append(("p2", 0, hp1, "Dodged", False))
            attacker = "p1"
            continue

        dmg = base_damage(bonus2, arm2)  # was base_damage(bonus2)

        dmg = int(dmg * float(state["p2"]["weaken_mult"] or 1.0))
        state["p2"]["weaken_mult"] = 1.0

        # NEW: Devil Fruit Technique (does NOT stack with crit)
        fruit_tech = roll(_fruit_tech_chance(ability2, bonus2))
        if fruit_tech:
            crit = False
            atk_name = f"🍈 {ability2}"
            dmg = int(dmg * crit_mult)
        else:
            crit = roll(crit_chance(arm2, a2))
            atk_name = attack_name_for(arm2)
            if crit:
                dmg = int(dmg * crit_mult)

        dmg = apply_defense_soak("p1", dmg, a1, arm1)  # was apply_defense_soak("p1", dmg, a1)
        dmg = apply_shield("p1", dmg)

        kind, extra, suffix = maybe_on_hit(a2, "p1")
        if kind == "BONUS":
            dmg += int(extra)

        hp1 = max(0, hp1 - max(0, dmg))
        turns.append(("p2", dmg, hp1, f"{atk_name}{suffix}", crit))
        if hp1 <= 0:
            break

        ds = a2.get("double_strike")
        if ds and isinstance(ds, tuple) and len(ds) == 2 and roll(float(ds[0])):
            extra_hit = int(max(1, dmg) * float(ds[1]))
            extra_hit = apply_defense_soak("p1", extra_hit, a1, arm1)  # FIX: pass defender armament
            extra_hit = apply_shield("p1", extra_hit)
            hp1 = max(0, hp1 - extra_hit)
            turns.append(("p2", extra_hit, hp1, "⚡ Double Strike", False))
            if hp1 <= 0:
                break

        # conqueror counter from defender (p1)
        if roll(counter_chance("p1", conq1, conq_lvl1)):
            cdmg = int(base_damage(bonus1, arm1) * crit_mult)  # was base_damage(bonus1)
            cdmg = apply_defense_soak("p2", cdmg, a2, arm2)
            cdmg = apply_shield("p2", cdmg)
            hp2 = max(0, hp2 - cdmg)
            turns.append(("p1", cdmg, hp2, CONQUEROR_COUNTER, True))
            if hp2 <= 0:
                break

        attacker = "p1"

    winner = "p1" if hp2 <= 0 and hp1 > 0 else "p2"
    return winner, turns, hp1, hp2
