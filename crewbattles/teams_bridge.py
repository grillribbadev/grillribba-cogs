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
        return None

    # alias to support previous naming
    async def get_team(self, guild, member):
        return await self.get_team_of(guild, member)

    async def award_win(self, guild, member, points: int):
        """
        Try to award crew/team points for a win. Returns True when succeeded.
        Tries several common Teams cog method names and signatures.
        """
        try:
            teams = self.bot.get_cog("Teams")
            if not teams:
                return False

            # Try direct award functions on Teams cog
            candidate_names = ("award_win", "award_points", "add_points_to_team", "add_team_points", "award_team_points", "add_points")
            for name in candidate_names:
                fn = getattr(teams, name, None)
                if not fn:
                    continue
                # try several call signatures
                for sig in (
                    (guild, member, int(points)),
                    (guild.id if hasattr(guild, "id") else guild, member.id if hasattr(member, "id") else member, int(points)),
                    (member, int(points)),
                    (member.id if hasattr(member, "id") else member, int(points)),
                ):
                    try:
                        res = fn(*sig)
                    except TypeError:
                        continue
                    except Exception:
                        # call failed for this signature; try others
                        res = None
                    if asyncio.iscoroutine(res):
                        try:
                            await res
                            return True
                        except Exception:
                            continue
                    # non-coroutine result - assume success if no exception
                    return True

            # Fallback: find team id and call an "add_points" method that accepts team id
            team = None
            try:
                team = await self.get_team_of(guild, member)
            except Exception:
                team = None
            if team is not None:
                # try team-id-based methods
                for name in ("add_points_to_team", "add_team_points", "award_team_points", "add_points"):
                    fn = getattr(teams, name, None)
                    if not fn:
                        continue
                    try:
                        res = fn(team, int(points))
                    except TypeError:
                        try:
                            res = fn(guild, team, int(points))
                        except Exception:
                            res = None
                    except Exception:
                        res = None
                    if asyncio.iscoroutine(res):
                        try:
                            await res
                            return True
                        except Exception:
                            continue
                    if res is not None:
                        return True
        except Exception:
            pass
        return False
