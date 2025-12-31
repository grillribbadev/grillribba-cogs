import discord

def hp_bar(hp: int, max_hp: int, length: int = 18) -> str:
    hp = max(0, int(hp))
    max_hp = max(1, int(max_hp))
    filled = int((hp / max_hp) * length)
    return "█" * filled + "░" * (length - filled)

def battle_embed(p1, p2, hp1, hp2, max_hp1, max_hp2, log_text: str):
    """
    p1/p2 are discord.Member-like objects (for display). log_text is recent log.
    """
    emb = discord.Embed(
        title="⚔️ Crew Battle",
        color=discord.Color.blurple()
    )
    emb.add_field(
        name=f"{p1.display_name} — HP",
        value=f"{hp_bar(hp1, max_hp1)}\n{hp1}/{max_hp1}",
        inline=True
    )
    emb.add_field(
        name=f"{p2.display_name} — HP",
        value=f"{hp_bar(hp2, max_hp2)}\n{hp2}/{max_hp2}",
        inline=True
    )
    emb.add_field(name="Battle Log", value=log_text or "—", inline=False)
    return emb
