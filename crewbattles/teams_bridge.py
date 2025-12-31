import asyncio

class TeamsBridge:
    """
    Wrapper bridge that tries to call the real Teams cog on the bot.
    If no Teams cog exists, methods return None. This minimal wrapper
    is safe and matches the call shapes the main cog expects.
    """
    def __init__(self, bot):
        self.bot = bot

    async def get_team_of(self, guild, member):
        teams = self.bot.get_cog("Teams")
        if not teams:
            return None
        # many Teams implementations expose helper functions or a teams mapping
        # try common call signatures
        if hasattr(teams, "get_team_of"):
            res = teams.get_team_of(guild, member)
            if asyncio.iscoroutine(res):
                return await res
            return res
        if hasattr(teams, "get_member_team"):
            res = teams.get_member_team(guild, member)
            if asyncio.iscoroutine(res):
                return await res
            return res
        # fallback: no usable method
        return None

    # alias to support previous naming
    async def get_team(self, guild, member):
        return await self.get_team_of(guild, member)

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
