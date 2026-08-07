from __future__ import annotations

import discord

#
# Economy
#

STARTING_BALANCE = 250
DAILY_INCOME = 250

#
# Auction Defaults
#

DEFAULT_AUCTION_DURATION = 120  # seconds
DEFAULT_AUCTION_INTERVAL = 86400  # 24 hours
DEFAULT_STARTING_BID = 1

#
# Live auction behaviour
#
GOING_ONCE_SECONDS = 5
GOING_TWICE_SECONDS = 10
GOING_THREE_SECONDS = 15
NO_BID_CLOSE_SECONDS = 15

#
# Anti Troll
#

MINIMUM_BID_INCREMENT = 5
BID_COOLDOWN = 2  # seconds

ANTI_SNIPE_THRESHOLD = 10  # seconds remaining
ANTI_SNIPE_EXTENSION = 10  # seconds added
MAX_ANTI_SNIPE = 60  # maximum extra seconds

INVALID_BID_LIMIT = 10

#
# Economy
#

AUCTION_TAX = 0.05  # 5%

#
# Character Settings
#

CHARACTER_DATA_FILE = "data/characters.json"

#
# Embed Colours
#

COLOR_DEFAULT = discord.Color.blue()
COLOR_SUCCESS = discord.Color.green()
COLOR_ERROR = discord.Color.red()
COLOR_WARNING = discord.Color.orange()
COLOR_AUCTION = discord.Color.gold()

#
# Emojis
#

EMOJI_BERRI = "💰"
EMOJI_GAVEL = "🔨"
EMOJI_CHEST = "📦"
EMOJI_CLOCK = "⏰"
EMOJI_CHARACTER = "👤"
EMOJI_CROWN = "👑"

#
# Rarities
#

RARITIES = (
    "Common",
    "Uncommon",
    "Rare",
    "Epic",
    "Legendary",
)

RARITY_COLORS = {
    "Common": discord.Color.light_grey(),
    "Uncommon": discord.Color.green(),
    "Rare": discord.Color.blue(),
    "Epic": discord.Color.purple(),
    "Legendary": discord.Color.gold(),
}