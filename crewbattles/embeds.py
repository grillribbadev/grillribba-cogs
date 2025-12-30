import discord

HP_BAR_LEN = 20


def _hp_bar(current: int, maximum: int) -> str:
    if maximum <= 0:
        return "░" * HP_BAR_LEN
    ratio = max(0, min(1, current / maximum))
    filled = int(ratio * HP_BAR_LEN)
    return "█" * filled + "░" * (HP_BAR_LEN - filled)


def battle_embed(
    member1: discord.Member,
    member2: discord.Member,
    hp1: int,
    hp2: int,
    max_hp: int,
    log_text: str | None = None,
):
    """
    Main battle embed renderer.
    IMPORTANT:
    - Battle log MUST be a string, never a list
    - Log is rendered in description to avoid per-character wrapping bugs
    """

    emb = discord.Embed(
        title="⚔️ Crew Battle",
        color=discord.Color.red(),
    )

    # --- HP DISPLAY ---
    emb.add_field(
        name=member1.display_name,
        value=f"❤️ {hp1}/{max_hp}\n{_hp_bar(hp1, max_hp)}",
        inline=True,
    )

    emb.add_field(
        name=member2.display_name,
        value=f"❤️ {hp2}/{max_hp}\n{_hp_bar(hp2, max_hp)}",
        inline=True,
    )

    # --- BATTLE LOG ---
    if log_text:
        emb.description = (
            "📜 **Battle Log**\n"
            f"{log_text}"
        )
    else:
        emb.description = "📜 **Battle Log**\nThe battle begins!"

    emb.set_footer(text="Crew Battles • Live Combat")

    return emb
