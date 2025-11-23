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
    "mode": "teamscog",            # "teamscog" (direct to Teams cog) or "http"
    "win_points": 1,
    "timeout_points": 0,

    # HTTP fields are harmless if unused
    "base_url": "",
    "token": "",
    "endpoint_path": "/api/onepieceguess/event",
}

# Guild-level config schema
DEFAULT_GUILD = {
    "enabled": False,
    "channel_id": None,

    # current game mode: "character" | "fruit" | "ship"
    "mode": "character",

    # posting cadence & per-round timeout
    "interval": INTERVAL_DEFAULT,  # seconds between round posts
    "roundtime": ROUND_DEFAULT,    # seconds until a round times out

    # optional local reward (set to 0 to noop)
    "reward": REWARD_DEFAULT,

    # --- POOLS (per-mode) ---
    # Characters
    "characters": [],              # list[str]
    "aliases": {},                 # title -> list[str]
    "hints": {},                   # title -> str (optional override)

    # Devil Fruits
    "fruits": [],
    "fruit_aliases": {},
    "fruit_hints": {},

    # Ships
    "ships": [],
    "ship_aliases": {},
    "ship_hints": {},

    # hints (wiki extract or custom)
    "hint_enabled": True,
    "hint_max_chars": 200,

    # Image settings (blur + optional black & white)
    "blur": {
        "mode": "gaussian",        # "gaussian" or "pixelate"
        "strength": 8,             # clamped in code up to 250
        "bw": False,               # black & white toggle
    },

    # NEW: image failsafe controls
    "require_image": {          # per-mode toggle
        "character": False,
        "fruit": True,
        "ship": True,
    },
    "noimage_max_retries": 6,   # how many picks to try before skipping the cycle

    # Teams integration block
    "team_api": TEAMAPI_DEFAULT.copy(),

    # Active round state (runtime)
    "active": {
        "title": None,
        "posted_message_id": None,
        "posted_channel_id": None,
        "started_at": 0,
        "expired": False,
        # whether we already posted the mid-round quote/initials
        "half_hint_sent": False,
    },
}

# User-level stats (simple win counter)
DEFAULT_USER = {
    "wins": 0
}