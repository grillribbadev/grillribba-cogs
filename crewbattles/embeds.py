import discord

def hp_bar(hp, max_hp):
    filled = int((hp / max_hp) * 10) if max_hp > 0 else 0
    filled = max(0, min(10, filled))
    return "█" * filled + "░" * (10 - filled)

def battle_embed(p1, p2, hp1, hp2, max_hp1, max_hp2, last=None):
    e = discord.Embed(title="⚔️ Crew Battle", color=discord.Color.blurple())
    # p1 field
    e.add_field(
        name=f"{p1.display_name}",
        value=f"HP: **{hp1:,} / {max_hp1:,}**\n{hp_bar(hp1, max_hp1)}",
        inline=True,
    )
    # p2 field
    e.add_field(
        name=f"{p2.display_name}",
        value=f"HP: **{hp2:,} / {max_hp2:,}**\n{hp_bar(hp2, max_hp2)}",
        inline=True,
    )

    if last:
        e.add_field(name="Recent Log", value=last, inline=False)

    e.set_footer(text="Crew Battles • Progress is saved")
    return e
