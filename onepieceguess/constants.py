from __future__ import annotations

COLOR_EMBED = 0x00BFFF
COLOR_OK    = 0x32CD32
COLOR_WARN  = 0xFFA500
COLOR_ERR   = 0xCC3333

# Timing defaults
INTERVAL_DEFAULT = 1800   # seconds between posts (cadence)
REWARD_DEFAULT   = 1000
ROUND_DEFAULT    = 120    # seconds a round stays open before timing out

DEFAULT_GUILD = {
    "enabled": False,
    "channel_id": None,
    "interval": INTERVAL_DEFAULT,   # cadence
    "reward": REWARD_DEFAULT,
    "characters": [],               # list[str]
    "aliases": {},                  # title -> list[str]
    "hint_enabled": True,
    "hint_max_chars": 200,

    # Image handling
    "blur": {
        "mode": "gaussian",         # "gaussian" or "pixelate"
        "strength": 8,              # clamp now raised to 250
        "bw": False                 # NEW: black-and-white toggle
    },

    "roundtime": ROUND_DEFAULT,     # per-round timeout (seconds)

    # Active round state
    "active": {
        "title": None,
        "posted_message_id": None,
        "posted_channel_id": None,
        "started_at": 0,
        "expired": False
    }
}

DEFAULT_USER = {
    "wins": 0
}
