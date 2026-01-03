import discord

# Custom heart emojis (replace names if yours differ; IDs must match your server emojis)
FULL_HEART = "<:full_heart:1379318858279551027>"
HALF_HEART = "<:half_heart:1379318888906489897>"
EMPTY_HEART = "<:empty_heart:1379318910809018408>"

def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def _hp_bar(hp: int, max_hp: int, width: int = 13) -> str:
    max_hp = max(1, int(max_hp))
    hp = _clamp(int(hp), 0, max_hp)
    # Hearts bar: full / half / empty
    ratio = hp / max_hp
    units = ratio * width

    full = int(units)
    half = 1 if (units - full) >= 0.5 else 0
    full = _clamp(full, 0, width)
    if full == width:
        half = 0
    empty = width - full - half
    empty = _clamp(empty, 0, width)

    return f"{FULL_HEART * full}{HALF_HEART * half}{EMPTY_HEART * empty}"

def battle_embed(p1, p2, hp1: int, hp2: int, max_hp1: int, max_hp2: int, log_text: str) -> discord.Embed:
    """
    Signature must match how crewbattles.py calls it.
    """
    hp1 = int(hp1)
    hp2 = int(hp2)
    max_hp1 = max(1, int(max_hp1))
    max_hp2 = max(1, int(max_hp2))

    # pick vibe based on state
    if hp1 <= 0 or hp2 <= 0:
        title = "🏁 Battle Concluded!"
        color = discord.Color.green()
    else:
        title = "⚔️ Crew Battle!"
        color = discord.Color.red()

    e = discord.Embed(
        title=title,
        description="🎌 **Duel in progress…** May the strongest pirate win!",
        color=color,
    )

    # Player blocks
    p1_name = getattr(p1, "display_name", "Player 1")
    p2_name = getattr(p2, "display_name", "Player 2")

    p1_line = f"❤️ **HP:** `{hp1}/{max_hp1}`\n{_hp_bar(hp1, max_hp1)}"
    p2_line = f"❤️ **HP:** `{hp2}/{max_hp2}`\n{_hp_bar(hp2, max_hp2)}"

    e.add_field(name=f"🏴‍☠️ {p1_name}", value=p1_line, inline=True)
    e.add_field(name=f"🏴‍☠️ {p2_name}", value=p2_line, inline=True)

    # Combat log
    log_text = (log_text or "").strip()
    if not log_text:
        log_text = "—"
    e.add_field(name="📜 Combat Log", value=log_text[-1000:], inline=False)

    # Footer tips (keeps it “gamey”)
    e.set_footer(text="✨ Armament = CRIT • Observation = DODGE • Conqueror = COUNTER CRIT")
    return e
