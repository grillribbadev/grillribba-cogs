import discord

def hp_bar(hp, max_hp):
    filled = int((hp / max_hp) * 10)
    return "█" * filled + "░" * (10 - filled)

def battle_embed(p1, p2, log, winner):
    e = discord.Embed(title="⚔️ Crew Battle")
    for line in log[-5:]:
        e.add_field(name="Turn", value=line, inline=False)
    e.add_field(name="Winner", value=winner)
    return e
