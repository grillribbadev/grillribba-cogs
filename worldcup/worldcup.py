import aiohttp
import discord
from datetime import datetime, timezone
from redbot.core import commands


API_URL = "https://worldcup26.ir/get/games"


class WorldCup(commands.Cog):
    """FIFA World Cup 2026 live scores and schedule."""

    def __init__(self, bot):
        self.bot = bot

    async def fetch_games(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=20) as response:
                if response.status != 200:
                    raise Exception(f"API returned status {response.status}")
                return await response.json()

    def get_value(self, data, *keys, default=None):
        for key in keys:
            if isinstance(data, dict) and key in data and data[key] not in [None, ""]:
                return data[key]
        return default

    def get_team_name(self, team):
        if isinstance(team, dict):
            return (
                team.get("name_en")
                or team.get("name")
                or team.get("team")
                or team.get("title")
                or "Unknown"
            )
        return str(team) if team else "Unknown"

    def get_match_time(self, game):
        raw = self.get_value(
            game,
            "date",
            "datetime",
            "time",
            "kickoff",
            "match_date",
            "start_time",
            default=""
        )

        if not raw:
            return "Unknown time"

        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y, %H:%M")
        except Exception:
            return str(raw)

    def get_status(self, game):
        status = str(
            self.get_value(
                game,
                "status",
                "match_status",
                "state",
                default=""
            )
        ).lower()

        return status

    def is_live(self, game):
        status = self.get_status(game)
        return status in ["live", "in_play", "playing", "first_half", "second_half", "halftime"]

    def is_finished(self, game):
        status = self.get_status(game)
        return status in ["finished", "complete", "completed", "ft", "fulltime"]

    def is_upcoming(self, game):
        status = self.get_status(game)
        return status in ["scheduled", "upcoming", "not_started", "pending", ""]

    def get_home_team(self, game):
        return self.get_team_name(
            self.get_value(game, "home_team", "home", "team_home", "team1", default={})
        )

    def get_away_team(self, game):
        return self.get_team_name(
            self.get_value(game, "away_team", "away", "team_away", "team2", default={})
        )

    def get_home_score(self, game):
        return self.get_value(
            game,
            "home_score",
            "score_home",
            "home_goals",
            "team1_score",
            default=0
        )

    def get_away_score(self, game):
        return self.get_value(
            game,
            "away_score",
            "score_away",
            "away_goals",
            "team2_score",
            default=0
        )

    def get_minute(self, game):
        return self.get_value(
            game,
            "minute",
            "elapsed",
            "time_elapsed",
            "match_minute",
            default="?"
        )

    def get_goals(self, game):
        events = self.get_value(game, "events", "goals", "match_events", default=[])

        if not isinstance(events, list):
            return []

        goals = []

        for event in events:
            if not isinstance(event, dict):
                continue

            event_type = str(event.get("type", event.get("event", ""))).lower()

            if "goal" not in event_type and event_type not in ["g"]:
                continue

            minute = event.get("minute") or event.get("time") or event.get("elapsed") or "?"
            player = event.get("player") or event.get("player_name") or event.get("scorer") or "Unknown scorer"
            team = event.get("team") or event.get("team_name") or ""

            if isinstance(player, dict):
                player = player.get("name") or player.get("name_en") or "Unknown scorer"

            if isinstance(team, dict):
                team = team.get("name") or team.get("name_en") or ""

            goals.append(f"{minute}' — {player} {f'({team})' if team else ''}")

        return goals

    def sort_key(self, game):
        raw = self.get_value(game, "date", "datetime", "time", "kickoff", "match_date", "start_time", default="")
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return datetime.max.replace(tzinfo=timezone.utc)

    @commands.group()
    async def wc(self, ctx):
        """World Cup 2026 commands."""
        pass

    @wc.command()
    async def current(self, ctx):
        """Show live World Cup match."""
        try:
            games = await self.fetch_games()
        except Exception as e:
            return await ctx.send(f"Could not fetch World Cup data: `{e}`")

        if isinstance(games, dict):
            games = games.get("data") or games.get("games") or games.get("matches") or []

        live_games = [game for game in games if self.is_live(game)]

        if not live_games:
            return await ctx.send("No World Cup match is live right now.")

        for game in live_games[:3]:
            home = self.get_home_team(game)
            away = self.get_away_team(game)
            home_score = self.get_home_score(game)
            away_score = self.get_away_score(game)
            minute = self.get_minute(game)
            goals = self.get_goals(game)

            embed = discord.Embed(
                title=f"{home} {home_score} - {away_score} {away}",
                description=f"⏱️ Minute: **{minute}'**",
                color=discord.Color.green()
            )

            if goals:
                embed.add_field(
                    name="Goals",
                    value="\n".join(goals[:10]),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Goals",
                    value="No goal data available yet.",
                    inline=False
                )

            await ctx.send(embed=embed)

    @wc.command()
    async def next(self, ctx):
        """Show next upcoming World Cup match."""
        try:
            games = await self.fetch_games()
        except Exception as e:
            return await ctx.send(f"Could not fetch World Cup data: `{e}`")

        if isinstance(games, dict):
            games = games.get("data") or games.get("games") or games.get("matches") or []

        upcoming = sorted(
            [game for game in games if self.is_upcoming(game)],
            key=self.sort_key
        )

        if not upcoming:
            return await ctx.send("No upcoming World Cup matches found.")

        game = upcoming[0]

        embed = discord.Embed(
            title="Next World Cup Match",
            color=discord.Color.blue()
        )

        embed.add_field(
            name=f"{self.get_home_team(game)} vs {self.get_away_team(game)}",
            value=f"🕒 {self.get_match_time(game)}",
            inline=False
        )

        await ctx.send(embed=embed)

    @wc.command()
    async def schedule(self, ctx, amount: int = 10):
        """Show upcoming World Cup schedule."""
        amount = max(1, min(amount, 20))

        try:
            games = await self.fetch_games()
        except Exception as e:
            return await ctx.send(f"Could not fetch World Cup data: `{e}`")

        if isinstance(games, dict):
            games = games.get("data") or games.get("games") or games.get("matches") or []

        upcoming = sorted(
            [game for game in games if self.is_upcoming(game)],
            key=self.sort_key
        )[:amount]

        if not upcoming:
            return await ctx.send("No upcoming World Cup matches found.")

        embed = discord.Embed(
            title=f"Next {len(upcoming)} World Cup Matches",
            color=discord.Color.gold()
        )

        for game in upcoming:
            embed.add_field(
                name=f"{self.get_home_team(game)} vs {self.get_away_team(game)}",
                value=f"🕒 {self.get_match_time(game)}",
                inline=False
            )

        await ctx.send(embed=embed)