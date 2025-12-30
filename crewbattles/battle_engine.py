import random
from .constants import BASE_HP

def simulate(p1, p2):
    hp1 = BASE_HP + p1["level"] * 6
    hp2 = BASE_HP + p2["level"] * 6
    log = []

    while hp1 > 0 and hp2 > 0:
        d1 = random.randint(12, 25)
        hp2 -= d1
        log.append(f"{d1} damage dealt!")
        if hp2 <= 0:
            break
        d2 = random.randint(10, 23)
        hp1 -= d2
        log.append(f"{d2} damage taken!")

    return ("p1" if hp1 > 0 else "p2"), log
