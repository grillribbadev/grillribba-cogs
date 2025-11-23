from __future__ import annotations

# Embed colors
COLOR_EMBED = 0x00BFFF
COLOR_OK    = 0x32CD32
COLOR_WARN  = 0xFFA500
COLOR_ERR   = 0xCC3333

# Timing defaults
INTERVAL_DEFAULT = 1800   # seconds between posts (cadence)
REWARD_DEFAULT   = 0      # local reward disabled by default
ROUND_DEFAULT    = 120    # seconds a round stays open before timing out

# ---- Teams integration (AAA3A Teams cog, or HTTP if you keep it) ----
TEAMAPI_DEFAULT = {
    "enabled": False,
    "mode": "teamscog",
    "win_points": 1,
    "timeout_points": 0,
    "base_url": "",
    "token": "",
    "endpoint_path": "/api/onepieceguess/event",
}

# Guild-level config schema
DEFAULT_GUILD = {
    "enabled": False,
    "channel_id": None,

    # current game mode
    "mode": "character",

    # cadence & timeout
    "interval": INTERVAL_DEFAULT,
    "roundtime": ROUND_DEFAULT,

    # local reward
    "reward": REWARD_DEFAULT,

    # POOLS (per-mode)
    "characters": [],
    "aliases": {},
    "hints": {},

    "fruits": [],
    "fruit_aliases": {},
    "fruit_hints": {},

    "ships": [],
    "ship_aliases": {},
    "ship_hints": {},

    # hints (embed field)
    "hint_enabled": True,
    "hint_max_chars": 200,

    # Image settings (blur + optional black & white)
    "blur": {
        "mode": "gaussian",
        "strength": 8,
        "bw": False,
    },

    # NEW: image failsafe
    "require_image": {          # per-mode toggle
        "character": False,
        "fruit": True,
        "ship": True,
    },
    "noimage_max_retries": 6,   # how many picks to try before skipping the cycle

    # Teams integration
    "team_api": TEAMAPI_DEFAULT.copy(),

    # Active round state (runtime)
    "active": {
        "title": None,
        "posted_message_id": None,
        "posted_channel_id": None,
        "started_at": 0,
        "expired": False,
        "half_hint_sent": False,
    },
}

# User-level stats (simple win counter)
DEFAULT_USER = {
    "wins": 0
}
