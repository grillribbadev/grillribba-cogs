def haki_bonus(player):
    bonus = 0
    bonus += player["haki"]["armament"] * 2
    bonus += player["haki"]["observation"]
    if player["haki"]["conquerors"]:
        bonus += 5
    return bonus
