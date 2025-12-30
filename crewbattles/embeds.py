import discord


def _hp_bar(hp: int, max_hp: int) -> str:
    filled = int((hp / max_hp) * 10)
    return "█" * filled + "░" * (10 - filled)


def battle_embed(p1, p2, hp1, hp2, max_hp, log_lines=None):
    log_lines = log_lines or []

    embed = discord.Embed(
        title="⚔️ Crew Battle",
        color=discord.Color.red(),
    )

    embed.add_field(
        name=p1.display_name,
        value=f"❤️ {hp1}/{max_hp}\n{_hp_bar(hp1, max_hp)}",
        inline=True,
    )

    embed.add_field(
        name=p2.display_name,
        value=f"❤️ {hp2}/{max_hp}\n{_hp_bar(hp2, max_hp)}",
        inline=True,
    )

    if log_lines:
        embed.add_field(
            name="📜 Battle Log",
            value="\n".join(log_lines[-8:]),  # keep last 8 lines
            inline=False,
        )

    embed.set_footer(text="Crew Battles • Live Combat")

    return embed