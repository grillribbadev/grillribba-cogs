MAX_LEVEL = 100
BASE_HP = 100

# guild defaults (used by Config in the cog)
DEFAULT_GUILD = {
    "turn_delay": 1.5,
    "beri_win": 0,
    "beri_loss": 0,
    "exp_win": 10,
    "exp_loss": 2,
    "crew_points_win": 0,
    "haki_cost": 500,
    "haki_cooldown": 3600,
}

# default user record (keeps shape used throughout the cog)
DEFAULT_USER = {
    "started": False,
    "fruit": None,
    "level": 1,
    "exp": 0,
    "wins": 0,
    "losses": 0,
    "team": None,
    "haki": {
        "armament": 0,
        "observation": 0,
        "conquerors": False,
        "conqueror": 0,
    },
    "last_haki_train": 0,
}
