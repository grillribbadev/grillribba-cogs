class TeamsBridge:
    """
    Adapter for a Teams cog that stores teams like:
      teams_cog.teams[guild_id] -> dict[team_id, team_obj]
    and each team_obj has:
      - members (iterable of member IDs or Member objects)
      - display_name (str)
      - async add_points(amount, member, actor)
    """

    def __init__(self, bot):
        self.bot = bot

    def _teams_cog(self):
        # Try common cog names
        return self.bot.get_cog("Teams") or self.bot.get_cog("teams")

    def _iter_guild_teams(self, guild_id: int):
        cog = self._teams_cog()
        if not cog:
            return []

        teams_map = getattr(cog, "teams", None)

        # Expected: dict[guild_id] -> dict[team_id] -> team
        if isinstance(teams_map, dict):
            guild_teams = teams_map.get(guild_id, {})
            if isinstance(guild_teams, dict):
                return list(guild_teams.values())
            # sometimes stored as list
            if isinstance(guild_teams, list):
                return guild_teams

        # Fallback: if cog itself behaves like a dict (rare)
        try:
            guild_teams = cog.get(guild_id, {})
            if isinstance(guild_teams, dict):
                return list(guild_teams.values())
        except Exception:
            pass

        return []

    def _member_in_team(self, team, member):
        try:
            mids = getattr(team, "members", None)
            if mids is None:
                return False

            # members might be Member objects or ints
            if member in mids:
                return True
            mid = getattr(member, "id", None)
            if mid is not None and mid in mids:
                return True
        except Exception:
            return False
        return False

    def _team_key(self, team):
        # stable comparable key for "same team" checks
        for attr in ("id", "team_id", "name", "display_name"):
            v = getattr(team, attr, None)
            if v:
                return str(v)
        return str(team)

    async def team_of(self, guild, member):
        """Return a stable team key, or None if not in a team / Teams cog missing."""
        if not guild or not member:
            return None
        for team in self._iter_guild_teams(guild.id):
            if self._member_in_team(team, member):
                return self._team_key(team)
        return None

    async def award_win(self, ctx, member, points: int) -> bool:
        """Award points to member's team. Returns False if no Teams cog or member not in a team."""
        if not ctx or not getattr(ctx, "guild", None):
            return False

        points = int(points or 0)
        if points <= 0:
            return False

        for team in self._iter_guild_teams(ctx.guild.id):
            if not self._member_in_team(team, member):
                continue

            try:
                # matches your snippet: await team.add_points(amount, member, ctx.author)
                await team.add_points(points, member, ctx.author)
                return True
            except Exception:
                return False

        return False
