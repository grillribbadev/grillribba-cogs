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
        if not teams or not guild or not member:
            return None

        # Try common Teams-cog helper methods first (if they exist)
        candidate_names = (
            "get_team_of",
            "get_member_team",
            "get_team",
            "member_team",
            "team_of",
            "fetch_member_team",
        )
        for name in candidate_names:
            fn = getattr(teams, name, None)
            if not fn:
                continue
            for args in (
                (guild, member),
                (guild, member.id),
                (guild.id, member),
                (guild.id, member.id),
                (member,),
                (member.id,),
            ):
                try:
                    res = fn(*args)
                except TypeError:
                    continue
                except Exception:
                    continue
                if asyncio.iscoroutine(res):
                    try:
                        res = await res
                    except Exception:
                        continue
                team_id = self._normalize_team(res)
                if team_id:
                    return team_id

        # Fallback: inspect common in-memory structure teams.teams[guild.id] and team.members
        try:
            guild_map = getattr(teams, "teams", None)
            if isinstance(guild_map, dict):
                guild_teams = guild_map.get(guild.id, {}) or {}
                for team in guild_teams.values():
                    members = getattr(team, "members", None)
                    if not members:
                        continue
                    try:
                        in_team = (member in members) or (member.id in members)
                    except Exception:
                        in_team = False
                        for m in members:
                            try:
                                if getattr(m, "id", None) == member.id or m == member.id or str(m) == str(member.id):
                                    in_team = True
                                    break
                            except Exception:
                                continue
                    if in_team:
                        tid = getattr(team, "id", None) or getattr(team, "team_id", None) or getattr(team, "name", None) or getattr(team, "display_name", None)
                        return str(tid).strip().lower() if tid is not None else None
        except Exception:
            pass

        return None

    async def award_win(self, guild, member, points: int) -> bool:
        """
        Award points to the winner's team. Returns True if something succeeded.
        This is best-effort because Teams cogs differ a lot.
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

        # 1) Try explicit award/add APIs on the Teams cog
        candidate_names = (
            "award_win",
            "award_points",
            "award_team_points",
            "add_team_points",
            "add_points_to_team",
            "add_points",
            "give_points",
        )
        for name in candidate_names:
            fn = getattr(teams, name, None)
            if not fn:
                continue

            # Try common signatures (guild/member/team variants)
            call_sigs = (
                (guild, member, points),
                (guild, member.id, points),
                (member, points),
                (member.id, points),
            )
            for sig in call_sigs:
                try:
                    res = fn(*sig)
                except TypeError:
                    continue
                except Exception as e:
                    print(f"[CrewBattles] Teams.{name}{sig} failed: {e}")
                    continue
                if asyncio.iscoroutine(res):
                    try:
                        await res
                    except Exception as e:
                        print(f"[CrewBattles] Teams.{name} await failed: {e}")
                        continue
                return True

        # 2) Fallback: find team id, then try team-id based methods
        team_id = await self.get_team_of(guild, member)
        if team_id:
            for name in ("add_points_to_team", "add_team_points", "award_team_points", "add_points", "give_points"):
                fn = getattr(teams, name, None)
                if not fn:
                    continue
                for sig in (
                    (team_id, points),
                    (guild, team_id, points),
                    (guild.id, team_id, points),
                ):
                    try:
                        res = fn(*sig)
                    except TypeError:
                        continue
                    except Exception as e:
                        print(f"[CrewBattles] Teams.{name}{sig} failed: {e}")
                        continue
                    if asyncio.iscoroutine(res):
                        try:
                            await res
                        except Exception as e:
                            print(f"[CrewBattles] Teams.{name} await failed: {e}")
                            continue
                    return True

        print("[CrewBattles] Teams award failed: no compatible Teams method found (or user has no team).")
        return False

    def _normalize_team(self, res):
        """Normalize various return shapes into a simple lowercase string id/name."""
        if res is None:
            return None
        if isinstance(res, (str, int)):
            return str(res).strip().lower()
        if isinstance(res, dict):
            for k in ("id", "team_id", "name", "team"):
                v = res.get(k)
                if v is not None:
                    return str(v).strip().lower()
        if hasattr(res, "id") or hasattr(res, "name"):
            v = getattr(res, "id", None) or getattr(res, "name", None)
            if v is not None:
                return str(v).strip().lower()
        return None
