class TeamsBridge:
    def __init__(self, bot):
        self.bot = bot

    async def award_win(self, guild, member, points):
        teams = self.bot.get_cog("Teams")
        if not teams:
            return None
        team = next(
            (t for t in teams.teams.get(guild.id, {}).values() if member in t.members),
            None,
        )
        if not team:
            return None
        await team.add_points(points, member, guild.me)
        return team.display_name
