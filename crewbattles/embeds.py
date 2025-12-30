import discord

def hp_bar(hp, max_hp):
    filled = int((hp / max_hp) * 10)
    return "█" * filled + "░" * (10 - filled)

def battle_embed(p1, p2, hp1, hp2, max_hp, last=None):
    e = discord.Embed(title="⚔️ Crew Battle")
    e.add_field(
        name=p1.display_name,
        value=f"{hp_bar(hp1, max_hp)} {hp1}/{max_hp}",
        inline=True,
    )
    e.add_field(
        name=p2.display_name,
        value=f"{hp_bar(hp2, max_hp)} {hp2}/{max_hp}",
        inline=True,
    )
    if last:
        e.add_field(name="Last Move", value=last, inline=False)
    return e
