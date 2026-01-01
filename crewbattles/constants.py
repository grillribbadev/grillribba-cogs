MAX_LEVEL = 100
BASE_HP = 150

DEFAULT_USER = {
    "started": False,
    "level": 1,
    "exp": 0,
    "wins": 0,
    "losses": 0,
    "fruit": None,
    "haki": {"armament": 0, "observation": 0, "conquerors": False, "conqueror": 0},
    "last_haki_train": 0,
    "battle_cd": 60,
    "last_battle": 0,
    "tempban_until": 0,
}

DEFAULT_GUILD = {
    "turn_delay": 1.0,
    "beri_win": 0,
    "beri_loss": 0,
    "crew_points_win": 1,

    # EXP ranges (you can configure with admin commands)
    "exp_win_min": 0,
    "exp_win_max": 0,
    "exp_loss_min": 0,
    "exp_loss_max": 0,

    # Haki training
    "haki_cost": 500,
    "haki_cooldown": 60 * 60,
    "conqueror_unlock_cost": 5000,

    # Fruit removal cost (0 = free)
    "remove_fruit_cost": 0,
}
