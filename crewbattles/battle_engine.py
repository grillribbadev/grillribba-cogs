import random
from .constants import BASE_HP
from .haki import haki_bonus

def simulate(p1, p2):
    hp1 = BASE_HP + p1["level"] * 6
    hp2 = BASE_HP + p2["level"] * 6
    log = []

    while hp1 > 0 and hp2 > 0:
        dmg1 = random.randint(10, 20) + haki_bonus(p1)
        hp2 -= dmg1
        log.append(("p1", dmg1, max(hp2, 0)))
        if hp2 <= 0:
            break
        dmg2 = random.randint(10, 20) + haki_bonus(p2)
        hp1 -= dmg2
        log.append(("p2", dmg2, max(hp1, 0)))

    return ("p1" if hp1 > 0 else "p2"), log, hp1, hp2
