class TeamsBridge:
    """
    Safe adapter. If you later add a Teams cog, you can extend this.
    """
    def __init__(self, bot):
        self.bot = bot

    async def award_win(self, ctx, member, points: int) -> bool:
        return False

    async def team_of(self, guild, member):
        return None
