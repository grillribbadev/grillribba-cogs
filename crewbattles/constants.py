MAX_LEVEL = 100
BASE_HP = 100

DEFAULT_GUILD = {
    "enabled": True,
    "beri_win": 500,
    "beri_loss": 150,
    "exp_win": 60,
    "exp_loss": 25,
    "crew_points_win": 1,
    "turn_delay": 1.5
}

DEFAULT_USER = {
    "started": False,
    "level": 1,
    "exp": 0,
    "wins": 0,
    "losses": 0,
    "fruit": None,
    "haki": {
        "armament": 0,
        "observation": 0,
        "conquerors": False
    },
    # timestamp (unix) of last haki training action for rate-limiting
    "last_haki_train": 0
}
