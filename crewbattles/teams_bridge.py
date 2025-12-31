import asyncio

class TeamsBridge:
    """
    Bridge to interact with a Teams cog if present.

    This implementation matches the Teams integration snippet you posted:
      team := next(t for t in self.teams[guild_id].values() if member in t.members)
      await team.add_points(amount, member, ctx.author)

    Provides:
      - get_team_of(guild, member) -> team object or None
      - award_win(ctx, member, points) -> True on success
    """

    def __init__(self, bot):
        self.bot = bot

    def _get_teams_cog(self):
        return self.bot.get_cog("Teams")

    def _find_team(self, teams_cog, guild_id: int, member):
        """Return the team object from TeamsCog.teams[guild_id] that contains member."""
        try:
            guild_teams = (getattr(teams_cog, "teams", {}) or {}).get(guild_id, {}) or {}
        except Exception:
            return None

        # member match supports members stored as Member objects OR ids
        mid = getattr(member, "id", member)
        for t in guild_teams.values():
            members = getattr(t, "members", None)
            if not members:
                continue
            try:
                if member in members or mid in members:
                    return t
            except Exception:
                # fallback: iterate and compare ids/strings
                for m in members:
                    try:
                        if getattr(m, "id", None) == mid or m == mid or str(m) == str(mid):
                            return t
                    except Exception:
                        continue
        return None

    async def get_team_of(self, guild, member):
        """Return the team object (not just a name/id), or None."""
        teams = self._get_teams_cog()
        if not teams or not guild or not member:
            return None
        return self._find_team(teams, guild.id, member)

    async def award_win(self, ctx, member, points: int) -> bool:
        """
        Award points to the winner's team using team.add_points(points, member, ctx.author).

        Returns True if points were successfully added.
        """
        try:
            points = int(points or 0)
        except Exception:
            return False
        if points <= 0:
            return False

        if not ctx or not getattr(ctx, "guild", None):
            return False

        teams = self._get_teams_cog()
        if not teams:
            return False

        team = self._find_team(teams, ctx.guild.id, member)
        if not team:
            # winner not in any team
            return False

        try:
            added_by = getattr(ctx, "author", None) or member
            res = team.add_points(points, member, added_by)
            if asyncio.iscoroutine(res):
                await res
            return True
        except RuntimeError as e:
            # Teams cog uses RuntimeError for rule failures (per your snippet)
            print(f"[CrewBattles] Teams add_points blocked: {e}")
            return False
        except Exception as e:
            print(f"[CrewBattles] Teams add_points failed: {e}")
            return False
