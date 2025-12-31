import asyncio

class TeamsBridge:
    """
    Bridge to interact with a Teams cog if present.
    Provides:
      - get_team_of(guild, member) -> normalized team id/name or None
      - award_win(guild, member, points) -> True on success
    """
    def __init__(self, bot):
        self.bot = bot

    async def get_team_of(self, guild, member):
        teams = self.bot.get_cog("Teams")
        if not teams:
            return None

        # try common method names / signatures
        candidates = ("get_team_of", "get_member_team", "get_team", "member_team", "team_of", "fetch_member_team")
        for name in candidates:
            fn = getattr(teams, name, None)
            if not fn:
                continue
            # try several call shapes
            for args in (
                (guild, member),
                (guild, member.id if hasattr(member, "id") else member),
                (guild.id if hasattr(guild, "id") else guild, member),
                (member,),
                (member.id if hasattr(member, "id") else member,),
            ):
                try:
                    res = fn(*args)
                except TypeError:
                    continue
                except Exception:
                    res = None
                if asyncio.iscoroutine(res):
                    try:
                        res = await res
                    except Exception:
                        res = None
                if res is None:
                    continue
                # normalize scalar/dict/object into string id
                if isinstance(res, dict):
                    for key in ("id", "team_id", "name", "team"):
                        if key in res and res[key] is not None:
                            return str(res[key]).strip().lower()
                    # fallback: first string/int value
                    for v in res.values():
                        if isinstance(v, (str, int)):
                            return str(v).strip().lower()
                if isinstance(res, (str, int)):
                    return str(res).strip().lower()
                # object with id/name
                if hasattr(res, "id") or hasattr(res, "name"):
                    val = getattr(res, "id", None) or getattr(res, "name", None)
                    if val is not None:
                        return str(val).strip().lower()
                # list/tuple -> first scalar candidate
                if isinstance(res, (list, tuple)) and len(res):
                    for item in res:
                        if isinstance(item, (str, int)):
                            return str(item).strip().lower()
                        if hasattr(item, "id") or hasattr(item, "name"):
                            v = getattr(item, "id", None) or getattr(item, "name", None)
                            if v is not None:
                                return str(v).strip().lower()
        return None

    async def award_win(self, guild, member, points: int):
        """
        Try to award team/crew points for a member's win.
        Returns True when awarding succeeded (best-effort).
        """
        try:
            points = int(points or 0)
        except Exception:
            return False
        if points <= 0:
            return False

        teams = self.bot.get_cog("Teams")
        if not teams:
            return False

        candidate_names = (
            "award_win",
            "award_points",
            "add_points_to_team",
            "add_team_points",
            "award_team_points",
            "add_points",
            "give_points",
        )

        # Attempt to call direct functions on Teams cog
        for name in candidate_names:
            fn = getattr(teams, name, None)
            if not fn:
                continue
            for sig in (
                (guild, member, points),
                (guild.id if hasattr(guild, "id") else guild, member, points),
                (member, points),
                (member.id if hasattr(member, "id") else member, points),
            ):
                try:
                    res = fn(*sig)
                except TypeError:
                    continue
                except Exception:
                    res = None
                if asyncio.iscoroutine(res):
                    try:
                        await res
                        return True
                    except Exception:
                        continue
                # If function returned without raising, assume success
                return True

        # Fallback: get the team id and call a team-id based method
        team = None
        try:
            team = await self.get_team_of(guild, member)
        except Exception:
            team = None

        if team is not None:
            for name in ("add_points_to_team", "add_team_points", "award_team_points", "add_points", "give_points"):
                fn = getattr(teams, name, None)
                if not fn:
                    continue
                for sig in (
                    (team, points),
                    (guild, team, points),
                    (guild.id if hasattr(guild, "id") else guild, team, points),
                ):
                    try:
                        res = fn(*sig)
                    except TypeError:
                        continue
                    except Exception:
                        res = None
                    if asyncio.iscoroutine(res):
                        try:
                            await res
                            return True
                        except Exception:
                            continue
                    return True

        return False
