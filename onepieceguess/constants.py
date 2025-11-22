from __future__ import annotations

COLOR_EMBED = 0x00BFFF
COLOR_OK    = 0x32CD32
COLOR_WARN  = 0xFFA500
COLOR_ERR   = 0xCC3333

INTERVAL_DEFAULT = 1800  # seconds
REWARD_DEFAULT   = 1000

DEFAULT_GUILD = {
    "enabled": False,
    "channel_id": None,
    "interval": INTERVAL_DEFAULT,
    "reward": REWARD_DEFAULT,
    "characters": [],          # list[str]
    "aliases": {},             # title -> list[str]
    "hint_enabled": True,      # include text hint (extract)
    "hint_max_chars": 200,
    "blur": {                  # image blur settings
        "mode": "gaussian",    # "gaussian" or "pixelate"
        "strength": 8          # gaussian radius OR pixel block size
    },
    "active": {                # active round state
        "title": None,
        "posted_message_id": None,
        "posted_channel_id": None,
        "started_at": 0
    }
}

DEFAULT_USER = {
    "wins": 0
}
