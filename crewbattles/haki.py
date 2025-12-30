haki = p["haki"]

haki_lines = [
    f"🛡️ **Armament:** {haki.get('armament', 0)}",
    f"👁️ **Observation:** {haki.get('observation', 0)}",
    f"👑 **Conqueror’s:** {'Unlocked' if haki.get('conquerors') else 'Locked'}",
]

embed = discord.Embed(
    title=f"🏴‍☠️ {member.display_name}'s Crew Battle Profile",
    color=discord.Color.gold(),
)

embed.add_field(
    name="📊 Stats",
    value=(
        f"**Level:** {p['level']}\n"
        f"**Wins:** {p['wins']} • **Losses:** {p['losses']}\n"
        f"**Win Rate:** "
        f"{(p['wins'] / max(1, p['wins'] + p['losses']) * 100):.1f}%"
    ),
    inline=False,
)

embed.add_field(
    name="🍈 Devil Fruit",
    value=p["fruit"] if p["fruit"] else "None",
    inline=False,
)

embed.add_field(
    name="✨ Haki",
    value="\n".join(haki_lines),
    inline=False,
)

embed.set_footer(text="Crew Battles • Progress is saved")

await ctx.reply(embed=embed)

