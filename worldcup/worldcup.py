import json
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

                data = await response.json()

        if isinstance(data, dict):
            return (
                data.get("data")
                or data.get("games")
                or data.get("matches")
                or data.get("response")
                or []
            )

        return data if isinstance(data, list) else []

    def v(self, data, *keys, default=None):
        for key in keys:
            if isinstance(data, dict) and key in data and data[key] not in [None, ""]:
                return data[key]
        return default

    def team_name(self, team):
        if isinstance(team, dict):
            return (
                team.get("name_en")
                or team.get("name")
                or team.get("team")
                or team.get("title")
                or "Unknown"
            )
        return str(team) if team else "Unknown"

    def home_team(self, game):
        return self.team_name(self.v(game, "home_team", "home", "team_home", "team1", default={}))

    def away_team(self, game):
        return self.team_name(self.v(game, "away_team", "away", "team_away", "team2", default={}))

    def home_score(self, game):
        return self.v(game, "home_score", "score_home", "home_goals", "team1_score", default=0)

    def away_score(self, game):
        return self.v(game, "away_score", "score_away", "away_goals", "team2_score", default=0)

    def minute(self, game):
        return self.v(game, "minute", "elapsed", "time_elapsed", "match_minute", default="?")

    def status(self, game):
        return str(
            self.v(
                game,
                "status",
                "match_status",
                "state",
                "status_short",
                "status_long",
                "game_status",
                default="",
            )
        ).lower()

    def is_finished(self, game):
        return self.status(game) in [
            "finished", "complete", "completed", "ft", "fulltime", "full-time"
        ]

    def is_live(self, game):
        status = self.status(game)

        if status in [
            "live", "in_play", "playing", "first_half", "second_half",
            "halftime", "1h", "2h", "ht", "et", "p"
        ]:
            return True

        minute = self.minute(game)

        try:
            minute = int(str(minute).replace("'", "").strip())
            return 0 < minute < 130 and not self.is_finished(game)
        except Exception:
            return False

    def is_upcoming(self, game):
        status = self.status(game)

        if self.is_live(game) or self.is_finished(game):
            return False

        return status in [
            "", "scheduled", "upcoming", "not_started", "not started",
            "pending", "ns", "tbd"
        ]

    def sort_key(self, game):
        raw = self.v(game, "date", "datetime", "time", "kickoff", "match_date", "start_time", default="")
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return datetime.max.replace(tzinfo=timezone.utc)

    def match_time(self, game):
        raw = self.v(game, "date", "datetime", "time", "kickoff", "match_date", "start_time", default="Unknown")
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y, %H:%M")
        except Exception:
            return str(raw)

    def goals(self, game):
        events = self.v(game, "events", "goals", "match_events", default=[])

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

            goals.append(f"⚽ **{minute}'** — {player} {f'({team})' if team else ''}")

        return goals

    def base_embed(self, title, color):
        embed = discord.Embed(title=title, color=color)
        embed.set_footer(text="FIFA World Cup 2026")
        return embed

    @commands.group()
    async def wc(self, ctx):
        """World Cup 2026 commands."""
        pass

    @wc.command()
    async def current(self, ctx):
        """Show current live World Cup match."""
        try:
            games = await self.fetch_games()
        except Exception as e:
            return await ctx.send(f"❌ Could not fetch World Cup data: `{e}`")

        live_games = [game for game in games if self.is_live(game)]

        if not live_games:
            embed = self.base_embed("No live match right now", discord.Color.red())
            embed.description = "I could not find any match marked as live by the API."
            embed.add_field(
                name="Tip",
                value="Run `.wc debug` to check what the API is actually returning.",
                inline=False,
            )
            return await ctx.send(embed=embed)

        for game in live_games[:3]:
            home = self.home_team(game)
            away = self.away_team(game)
            hs = self.home_score(game)
            as_ = self.away_score(game)
            minute = self.minute(game)
            goals = self.goals(game)

            embed = self.base_embed(
                f"🔴 LIVE — {home} {hs} - {as_} {away}",
                discord.Color.green(),
            )

            embed.add_field(name="⏱️ Minute", value=f"**{minute}'**", inline=True)
            embed.add_field(name="📌 Status", value=f"`{self.status(game) or 'live'}`", inline=True)

            embed.add_field(
                name="⚽ Goals",
                value="\n".join(goals) if goals else "No goal data available.",
                inline=False,
            )

            await ctx.send(embed=embed)

    @wc.command()
    async def next(self, ctx):
        """Show next upcoming World Cup match."""
        try:
            games = await self.fetch_games()
        except Exception as e:
            return await ctx.send(f"❌ Could not fetch World Cup data: `{e}`")

        upcoming = sorted([game for game in games if self.is_upcoming(game)], key=self.sort_key)

        if not upcoming:
            return await ctx.send("No upcoming World Cup matches found.")

        game = upcoming[0]

        embed = self.base_embed("⏭️ Next World Cup Match", discord.Color.blue())
        embed.add_field(
            name=f"{self.home_team(game)} vs {self.away_team(game)}",
            value=f"🕒 **{self.match_time(game)}**",
            inline=False,
        )
        embed.add_field(name="Status", value=f"`{self.status(game) or 'scheduled'}`", inline=True)

        await ctx.send(embed=embed)

    @wc.command()
    async def schedule(self, ctx, amount: int = 10):
        """Show upcoming World Cup schedule."""
        amount = max(1, min(amount, 20))

        try:
            games = await self.fetch_games()
        except Exception as e:
            return await ctx.send(f"❌ Could not fetch World Cup data: `{e}`")

        upcoming = sorted([game for game in games if self.is_upcoming(game)], key=self.sort_key)[:amount]

        if not upcoming:
            return await ctx.send("No upcoming World Cup matches found.")

        embed = self.base_embed(f"📅 Next {len(upcoming)} World Cup Matches", discord.Color.gold())

        for game in upcoming:
            embed.add_field(
                name=f"{self.home_team(game)} vs {self.away_team(game)}",
                value=f"🕒 {self.match_time(game)}",
                inline=False,
            )

        await ctx.send(embed=embed)

    @wc.command()
    async def debug(self, ctx):
        """Show raw API data for debugging."""
        try:
            games = await self.fetch_games()
        except Exception as e:
            return await ctx.send(f"❌ Could not fetch World Cup data: `{e}`")

        text = json.dumps(games[:2], indent=2, ensure_ascii=False)

        if len(text) > 1900:
            text = text[:1900]

        await ctx.send(f"```json\n{text}\n```")
