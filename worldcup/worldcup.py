import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands


BASE_URL = "https://v3.football.api-sports.io"


class WorldCup(commands.Cog):
    """World Cup 2026 cog using API-Football."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=202606261337)

        self.config.register_global(
            api_key=None,
            league_id=1,
            season=2026,
            timezone="Europe/Oslo",
        )

        self.config.register_guild(
            notify_channel=None,
            notify_role=None,
            goal_notifications=False,
            live_watch_message=None,
            live_watch_channel=None,
            announced_goal_keys=[],
            goal_notify_dm_users=[],
            prediction_scores={},
        )

        self.goal_notification_loop.start()
        self.live_watch_loop.start()

    def cog_unload(self):
        self.goal_notification_loop.cancel()
        self.live_watch_loop.cancel()

    # -------------------------
    # API
    # -------------------------

    async def api_get(self, endpoint: str, params: Optional[dict] = None) -> List[dict]:
        api_key = await self.config.api_key()
        if not api_key:
            raise RuntimeError("No API key set. Use `.wcset key YOUR_API_KEY` first.")

        headers = {"x-apisports-key": api_key}

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                f"{BASE_URL}{endpoint}",
                params=params or {},
                timeout=25,
            ) as resp:
                data = await resp.json(content_type=None)

        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))

        return data.get("response", [])

    async def get_league_id(self):
        return await self.config.league_id()

    async def get_season(self):
        return await self.config.season()

    async def get_timezone_name(self):
        return await self.config.timezone()

    # -------------------------
    # Format helpers
    # -------------------------

    def embed(self, title: str, color: discord.Color = discord.Color.gold()) -> discord.Embed:
        e = discord.Embed(title=title, color=color)
        e.set_footer(text="FIFA World Cup 2026 • API-Football")
        return e

    def fixture_id(self, fixture: dict) -> int:
        return fixture["fixture"]["id"]

    def fixture_time(self, fixture: dict) -> str:
        raw = fixture["fixture"]["date"]
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y • %H:%M")
        except Exception:
            return raw

    def teams(self, fixture: dict) -> Tuple[str, str]:
        return fixture["teams"]["home"]["name"], fixture["teams"]["away"]["name"]

    def team_ids(self, fixture: dict) -> Tuple[int, int]:
        return fixture["teams"]["home"]["id"], fixture["teams"]["away"]["id"]

    def goals(self, fixture: dict) -> Tuple[int, int]:
        hg = fixture["goals"]["home"]
        ag = fixture["goals"]["away"]
        return 0 if hg is None else hg, 0 if ag is None else ag

    def status(self, fixture: dict) -> str:
        s = fixture["fixture"]["status"]
        short = s.get("short") or "?"
        elapsed = s.get("elapsed")
        if elapsed is not None:
            return f"{short} • {elapsed}'"
        return short

    def is_finished(self, fixture: dict) -> bool:
        return fixture["fixture"]["status"].get("short") in ["FT", "AET", "PEN"]

    def is_not_started(self, fixture: dict) -> bool:
        return fixture["fixture"]["status"].get("short") in ["NS", "TBD"]

    def match_title(self, fixture: dict, with_score: bool = True) -> str:
        home, away = self.teams(fixture)
        if with_score:
            hg, ag = self.goals(fixture)
            return f"{home} {hg} - {ag} {away}"
        return f"{home} vs {away}"

    def choose_team(self, teams: List[dict], search: str) -> Optional[dict]:
        search_norm = search.strip().lower()
        best = None

        for item in teams:
            team = item.get("team", {})
            name = team.get("name", "").lower()
            country = team.get("country", "").lower()
            national = bool(team.get("national"))

            if name == search_norm and national:
                return item
            if country == search_norm and national:
                return item
            if name == search_norm:
                best = item
            if national and (search_norm in name or search_norm in country):
                best = item

        if best:
            return best

        for item in teams:
            if bool(item.get("team", {}).get("national")):
                return item

        return teams[0] if teams else None

    async def send_goal_dm(self, user_id: int, embed: discord.Embed) -> None:
        user = guild_member = None
        try:
            guild_member = self.bot.get_user(user_id)
        except Exception:
            guild_member = None

        if guild_member is None:
            try:
                guild_member = await self.bot.fetch_user(user_id)
            except Exception:
                return

        try:
            await guild_member.send(embed=embed)
        except Exception:
            pass

    async def current_live_fixtures(self) -> List[dict]:
        return await self.api_get(
            "/fixtures",
            {
                "live": "all",
                "league": await self.get_league_id(),
                "season": await self.get_season(),
                "timezone": await self.get_timezone_name(),
            },
        )

    async def upcoming_fixtures(self, amount: int = 10) -> List[dict]:
        return await self.api_get(
            "/fixtures",
            {
                "league": await self.get_league_id(),
                "season": await self.get_season(),
                "next": amount,
                "timezone": await self.get_timezone_name(),
            },
        )

    async def today_fixtures(self) -> List[dict]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return await self.api_get(
            "/fixtures",
            {
                "league": await self.get_league_id(),
                "season": await self.get_season(),
                "date": today,
                "timezone": await self.get_timezone_name(),
            },
        )

    async def fixture_events(self, fixture_id: int) -> List[dict]:
        return await self.api_get("/fixtures/events", {"fixture": fixture_id})

    async def goal_lines(self, fixture_id: int) -> List[str]:
        events = await self.fixture_events(fixture_id)
        lines = []

        for event in events:
            if event.get("type") != "Goal":
                continue

            minute = event["time"].get("elapsed", "?")
            extra = event["time"].get("extra")
            player = event.get("player", {}).get("name") or "Unknown"
            team = event.get("team", {}).get("name") or "Unknown"
            detail = event.get("detail") or "Goal"

            minute_text = f"{minute}'"
            if extra:
                minute_text = f"{minute}+{extra}'"

            lines.append(f"⚽ **{minute_text}** — **{player}** ({team}) • {detail}")

        return lines

    # -------------------------
    # Settings
    # -------------------------

    @commands.group()
    async def wc(self, ctx):
        """World Cup commands."""
        pass

    @commands.group()
    @commands.is_owner()
    async def wcset(self, ctx):
        """World Cup settings."""
        pass

    @wcset.command()
    async def key(self, ctx, api_key: str):
        """Set API-Football key."""
        await self.config.api_key.set(api_key)
        await ctx.send("✅ API key saved.")

    @wcset.command()
    async def league(self, ctx, league_id: int):
        """Set World Cup league ID."""
        await self.config.league_id.set(league_id)
        await ctx.send(f"✅ League ID set to `{league_id}`.")

    @wcset.command()
    async def season(self, ctx, season: int):
        """Set season."""
        await self.config.season.set(season)
        await ctx.send(f"✅ Season set to `{season}`.")

    @wcset.command()
    async def timezone(self, ctx, timezone_name: str):
        """Set timezone, example: Europe/Oslo."""
        await self.config.timezone.set(timezone_name)
        await ctx.send(f"✅ Timezone set to `{timezone_name}`.")

    @wcset.command()
    async def settings(self, ctx):
        """Show settings."""
        e = self.embed("⚙️ World Cup Settings", discord.Color.blue())
        e.add_field(name="API key", value="✅ Set" if await self.config.api_key() else "❌ Missing")
        e.add_field(name="League ID", value=f"`{await self.get_league_id()}`")
        e.add_field(name="Season", value=f"`{await self.get_season()}`")
        e.add_field(name="Timezone", value=f"`{await self.get_timezone_name()}`")
        await ctx.send(embed=e)

    @wcset.command()
    async def findleague(self, ctx, *, search: str = "world cup"):
        """Find league IDs."""
        try:
            leagues = await self.api_get("/leagues", {"search": search})
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        e = self.embed(f"🔎 League search: {search}", discord.Color.blue())

        for item in leagues[:10]:
            league = item.get("league", {})
            country = item.get("country", {})
            seasons = item.get("seasons", [])
            latest = seasons[-1]["year"] if seasons else "?"
            e.add_field(
                name=f"{league.get('name')} — ID `{league.get('id')}`",
                value=f"Country: {country.get('name', 'Unknown')} • Latest season: `{latest}`",
                inline=False,
            )

        await ctx.send(embed=e)

    # -------------------------
    # Main commands
    # -------------------------

    @wc.command()
    async def current(self, ctx):
        """Show live match, score, minute and goals."""
        try:
            fixtures = await self.current_live_fixtures()
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            e = self.embed("🔴 No live World Cup match right now", discord.Color.red())
            e.description = "API-Football returned no live fixtures."
            return await ctx.send(embed=e)

        for f in fixtures[:3]:
            fid = self.fixture_id(f)
            home, away = self.teams(f)
            hg, ag = self.goals(f)
            status = f["fixture"]["status"]
            elapsed = status.get("elapsed", "?")

            e = self.embed(f"🔴 LIVE: {home} {hg} - {ag} {away}", discord.Color.green())
            e.add_field(name="⏱️ Minute", value=f"**{elapsed}'**", inline=True)
            e.add_field(name="📌 Status", value=f"`{status.get('long', 'Live')}`", inline=True)
            e.add_field(name="🏟️ Venue", value=f["fixture"].get("venue", {}).get("name") or "Unknown", inline=False)

            try:
                goals = await self.goal_lines(fid)
            except Exception:
                goals = []

            e.add_field(
                name="⚽ Goals",
                value="\n".join(goals) if goals else "No goals yet.",
                inline=False,
            )
            await ctx.send(embed=e)

    @wc.command()
    async def next(self, ctx):
        """Show next match."""
        try:
            fixtures = await self.upcoming_fixtures(1)
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            return await ctx.send("No upcoming match found.")

        f = fixtures[0]
        e = self.embed("⏭️ Next World Cup Match", discord.Color.blue())
        e.add_field(name="Match", value=f"**{self.match_title(f, with_score=False)}**", inline=False)
        e.add_field(name="🕒 Kickoff", value=self.fixture_time(f), inline=False)
        e.add_field(name="📌 Status", value=f"`{self.status(f)}`", inline=True)
        e.add_field(name="Fixture ID", value=f"`{self.fixture_id(f)}`", inline=True)
        await ctx.send(embed=e)

    @wc.command()
    async def schedule(self, ctx, amount: int = 10):
        """Show upcoming schedule."""
        amount = max(1, min(amount, 20))

        try:
            fixtures = await self.upcoming_fixtures(amount)
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            return await ctx.send("No upcoming matches found.")

        e = self.embed(f"📅 Next {len(fixtures)} World Cup Matches", discord.Color.gold())

        for f in fixtures:
            e.add_field(
                name=self.match_title(f, with_score=False),
                value=f"🕒 {self.fixture_time(f)}\n🏆 {f['league'].get('round', 'World Cup')}\n🆔 `{self.fixture_id(f)}`",
                inline=False,
            )

        await ctx.send(embed=e)

    @wc.command()
    async def today(self, ctx):
        """Show today's matches."""
        try:
            fixtures = await self.today_fixtures()
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            return await ctx.send("No World Cup matches today.")

        e = self.embed("📆 Today's World Cup Matches", discord.Color.purple())

        for f in fixtures:
            title = self.match_title(f, with_score=not self.is_not_started(f))
            e.add_field(
                name=title,
                value=f"🕒 {self.fixture_time(f)}\n📌 `{self.status(f)}`\n🆔 `{self.fixture_id(f)}`",
                inline=False,
            )

        await ctx.send(embed=e)

    # -------------------------
    # Standings / scorers / team
    # -------------------------

    @wc.command()
    async def standings(self, ctx, *, group: str):
        """Show standings for a specific group, e.g. `wc standings A` or `wc standings Group B`."""
        group_key = group.strip().lower()
        if group_key.startswith("group "):
            group_key = group_key.split("group ", 1)[1].strip()
        if group_key.startswith("g "):
            group_key = group_key.split("g ", 1)[1].strip()

        if not group_key:
            return await ctx.send("Please specify a group, for example `wc standings A`.")

        try:
            data = await self.api_get(
                "/standings",
                {"league": await self.get_league_id(), "season": await self.get_season()},
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not data:
            return await ctx.send("No standings found.")

        standings = data[0]["league"].get("standings", [])
        if not standings:
            return await ctx.send("No standings found.")

        selected_group = None
        selected_group_name = None
        available_groups = []

        for group_data in standings:
            if not group_data:
                continue

            group_name = group_data[0].get("group", "").strip()
            normalized = group_name.lower().replace("group", "").strip()
            if normalized:
                available_groups.append(group_name)

            if normalized == group_key:
                selected_group = group_data
                selected_group_name = group_name
                break

        if not selected_group:
            available = ", ".join(sorted(set(available_groups))) or "none"
            return await ctx.send(f"Group `{group}` not found. Available groups: {available}.")

        e = self.embed(f"🏆 {selected_group_name}", discord.Color.blue())
        lines = []
        for row in selected_group:
            team = row["team"]["name"]
            rank = row["rank"]
            pts = row["points"]
            played = row["all"]["played"]
            gd = row["goalsDiff"]
            lines.append(f"**{rank}. {team}** — {pts} pts | P{played} | GD {gd:+}")

        e.description = "\n".join(lines)
        await ctx.send(embed=e)

    @wc.command()
    async def scorers(self, ctx, amount: int = 10):
        """Show top scorers."""
        amount = max(1, min(amount, 20))

        try:
            players = await self.api_get(
                "/players/topscorers",
                {"league": await self.get_league_id(), "season": await self.get_season()},
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not players:
            return await ctx.send("No top scorers found.")

        e = self.embed(f"⚽ Top {amount} Scorers", discord.Color.gold())

        lines = []
        for i, p in enumerate(players[:amount], start=1):
            name = p["player"]["name"]
            team = p["statistics"][0]["team"]["name"]
            goals = p["statistics"][0]["goals"]["total"] or 0
            assists = p["statistics"][0]["goals"]["assists"] or 0
            lines.append(f"**{i}. {name}** ({team}) — {goals} goals, {assists} assists")

        e.description = "\n".join(lines)
        await ctx.send(embed=e)

    @wc.command()
    async def team(self, ctx, *, team_name: str):
        """Show team info by name."""
        try:
            teams = await self.api_get("/teams", {"search": team_name})
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not teams:
            return await ctx.send("No team found.")

        team_item = self.choose_team(teams, team_name)
        if not team_item:
            return await ctx.send("No team found.")

        team = team_item["team"]
        team_id = team["id"]

        try:
            fixtures = await self.api_get(
                "/fixtures",
                {
                    "team": team_id,
                    "league": await self.get_league_id(),
                    "season": await self.get_season(),
                    "timezone": await self.get_timezone_name(),
                },
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        e = self.embed(f"🌍 {team['name']}", discord.Color.blue())
        if team.get("logo"):
            e.set_thumbnail(url=team["logo"])

        upcoming = [f for f in fixtures if self.is_not_started(f)]
        finished = [f for f in fixtures if self.is_finished(f)]

        if upcoming:
            f = upcoming[0]
            e.add_field(
                name="Next match",
                value=f"**{self.match_title(f, with_score=False)}**\n🕒 {self.fixture_time(f)}",
                inline=False,
            )

        if finished:
            recent = finished[-3:]
            value = "\n".join([f"{self.match_title(f)} • `{self.status(f)}`" for f in recent])
            e.add_field(name="Recent matches", value=value, inline=False)

        e.add_field(name="Team ID", value=f"`{team_id}`", inline=True)
        await ctx.send(embed=e)

    # -------------------------
    # Events / stats / player / h2h / injuries / odds
    # -------------------------

    @wc.command()
    async def events(self, ctx, fixture_id: Optional[int] = None):
        """Show match events. Defaults to current live match."""
        try:
            if fixture_id is None:
                live = await self.current_live_fixtures()
                if not live:
                    return await ctx.send("No live match found. Use `.wc events <fixture_id>`.")
                fixture_id = self.fixture_id(live[0])

            events = await self.fixture_events(fixture_id)
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not events:
            return await ctx.send("No events found.")

        e = self.embed(f"📋 Match Events • Fixture {fixture_id}", discord.Color.purple())

        lines = []
        for ev in events[:25]:
            minute = ev["time"].get("elapsed", "?")
            extra = ev["time"].get("extra")
            minute_text = f"{minute}'" if not extra else f"{minute}+{extra}'"
            team = ev.get("team", {}).get("name", "Unknown")
            player = ev.get("player", {}).get("name", "Unknown")
            ev_type = ev.get("type", "Event")
            detail = ev.get("detail", "")
            icon = "⚽" if ev_type == "Goal" else "🟨" if "Yellow" in detail else "🟥" if "Red" in detail else "🔁" if ev_type == "subst" else "•"
            lines.append(f"{icon} **{minute_text}** — {player} ({team}) • {ev_type} {detail}")

        e.description = "\n".join(lines)
        await ctx.send(embed=e)

    @wc.command()
    async def stats(self, ctx, fixture_id: Optional[int] = None):
        """Show match statistics. Defaults to current live match."""
        try:
            if fixture_id is None:
                live = await self.current_live_fixtures()
                if not live:
                    return await ctx.send("No live match found. Use `.wc stats <fixture_id>`.")
                fixture_id = self.fixture_id(live[0])

            stats = await self.api_get("/fixtures/statistics", {"fixture": fixture_id})
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not stats or len(stats) < 2:
            return await ctx.send("No statistics found.")

        home = stats[0]
        away = stats[1]

        def stat_map(team_data):
            return {s["type"]: s["value"] for s in team_data.get("statistics", [])}

        hs = stat_map(home)
        aw = stat_map(away)

        keys = [
            "Ball Possession",
            "Total Shots",
            "Shots on Goal",
            "Corner Kicks",
            "Fouls",
            "Yellow Cards",
            "Red Cards",
        ]

        e = self.embed(f"📊 Match Stats • Fixture {fixture_id}", discord.Color.blue())
        e.add_field(name="Teams", value=f"**{home['team']['name']}** vs **{away['team']['name']}**", inline=False)

        lines = []
        for k in keys:
            lines.append(f"**{k}:** {hs.get(k, 0)} — {aw.get(k, 0)}")

        e.description = "\n".join(lines)
        await ctx.send(embed=e)

    @wc.command()
    async def player(self, ctx, *, name: str):
        """Search player tournament stats."""
        try:
            players = await self.api_get(
                "/players",
                {
                    "search": name,
                    "league": await self.get_league_id(),
                    "season": await self.get_season(),
                },
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not players:
            return await ctx.send("No player found.")

        p = players[0]
        player = p["player"]
        stat = p["statistics"][0]

        e = self.embed(f"👤 {player['name']}", discord.Color.blue())
        if player.get("photo"):
            e.set_thumbnail(url=player["photo"])

        e.add_field(name="Team", value=stat["team"]["name"], inline=True)
        e.add_field(name="Position", value=stat["games"].get("position") or "?", inline=True)
        e.add_field(name="Appearances", value=stat["games"].get("appearences") or 0, inline=True)
        e.add_field(name="Goals", value=stat["goals"].get("total") or 0, inline=True)
        e.add_field(name="Assists", value=stat["goals"].get("assists") or 0, inline=True)
        e.add_field(name="Rating", value=stat["games"].get("rating") or "N/A", inline=True)

        await ctx.send(embed=e)

    @wc.command()
    async def h2h(self, ctx, team1_id: int, team2_id: int):
        """Head-to-head by team IDs."""
        try:
            fixtures = await self.api_get("/fixtures/headtohead", {"h2h": f"{team1_id}-{team2_id}"})
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            return await ctx.send("No head-to-head fixtures found.")

        e = self.embed("🤝 Head-to-head", discord.Color.blue())

        for f in fixtures[:10]:
            e.add_field(
                name=self.match_title(f),
                value=f"🕒 {self.fixture_time(f)}\n🏆 {f['league'].get('name', 'Unknown')}",
                inline=False,
            )

        await ctx.send(embed=e)

    @wc.command()
    async def injuries(self, ctx):
        """Show injuries for the configured tournament."""
        try:
            injuries = await self.api_get(
                "/injuries",
                {"league": await self.get_league_id(), "season": await self.get_season()},
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not injuries:
            return await ctx.send("No injuries found, or this endpoint is not covered by your plan.")

        e = self.embed("🏥 Injuries / Suspensions", discord.Color.red())

        for item in injuries[:15]:
            player = item.get("player", {}).get("name", "Unknown")
            team = item.get("team", {}).get("name", "Unknown")
            reason = item.get("player", {}).get("reason") or item.get("reason") or "Unknown"
            e.add_field(name=player, value=f"{team} • {reason}", inline=False)

        await ctx.send(embed=e)

    @wc.command()
    async def odds(self, ctx, fixture_id: int):
        """Show odds for a fixture if your plan supports it."""
        try:
            odds = await self.api_get("/odds", {"fixture": fixture_id})
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not odds:
            return await ctx.send("No odds found, or odds are not included in your plan.")

        e = self.embed(f"💰 Odds • Fixture {fixture_id}", discord.Color.green())

        bookmakers = odds[0].get("bookmakers", [])[:3]
        for book in bookmakers:
            values = []
            for bet in book.get("bets", [])[:2]:
                vals = ", ".join([f"{v['value']}: {v['odd']}" for v in bet.get("values", [])[:3]])
                values.append(f"**{bet['name']}** — {vals}")
            e.add_field(name=book["name"], value="\n".join(values) or "No values", inline=False)

        await ctx.send(embed=e)

    # -------------------------
    # Watch live
    # -------------------------

    @wc.command()
    async def watch(self, ctx):
        """Create an auto-updating live message in this channel."""
        async with self.config.guild(ctx.guild).all() as data:
            data["live_watch_channel"] = ctx.channel.id
            data["live_watch_message"] = None

        msg = await ctx.send("✅ Live watch enabled in this channel. I’ll update a live embed when matches are live.")
        async with self.config.guild(ctx.guild).all() as data:
            data["live_watch_message"] = msg.id

    @wc.command()
    async def unwatch(self, ctx):
        """Disable live watch."""
        async with self.config.guild(ctx.guild).all() as data:
            data["live_watch_channel"] = None
            data["live_watch_message"] = None
        await ctx.send("✅ Live watch disabled.")

    async def build_live_watch_embed(self) -> discord.Embed:
        fixtures = await self.current_live_fixtures()

        if not fixtures:
            e = self.embed("⚽ World Cup Live Watch", discord.Color.red())
            e.description = "No match is live right now."
            return e

        e = self.embed("🔴 World Cup Live", discord.Color.green())

        for f in fixtures[:3]:
            fid = self.fixture_id(f)
            goals = []
            try:
                goals = await self.goal_lines(fid)
            except Exception:
                pass

            e.add_field(
                name=self.match_title(f),
                value=f"📌 `{self.status(f)}`\n" + ("\n".join(goals[-5:]) if goals else "No goals yet."),
                inline=False,
            )

        return e

    @tasks.loop(seconds=30)
    async def live_watch_loop(self):
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            data = await self.config.guild(guild).all()
            channel_id = data.get("live_watch_channel")
            message_id = data.get("live_watch_message")

            if not channel_id:
                continue

            channel = guild.get_channel(channel_id)
            if not channel:
                continue

            try:
                e = await self.build_live_watch_embed()

                if message_id:
                    try:
                        msg = await channel.fetch_message(message_id)
                        await msg.edit(embed=e, content=None)
                        continue
                    except Exception:
                        pass

                msg = await channel.send(embed=e)
                await self.config.guild(guild).live_watch_message.set(msg.id)

            except Exception:
                pass

    # -------------------------
    # Goal notifications
    # -------------------------

    @wc.group()
    async def notify(self, ctx):
        """Goal notification commands."""
        pass

    @notify.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        """Set notification channel."""
        await self.config.guild(ctx.guild).notify_channel.set(channel.id)
        await ctx.send(f"✅ Goal notifications channel set to {channel.mention}.")

    @notify.command()
    async def role(self, ctx, role: discord.Role):
        """Set notification role."""
        await self.config.guild(ctx.guild).notify_role.set(role.id)
        await ctx.send(f"✅ Notification role set to {role.mention}.")

    @notify.command()
    async def on(self, ctx):
        """Enable goal notifications."""
        await self.config.guild(ctx.guild).goal_notifications.set(True)
        await ctx.send("✅ Goal notifications enabled.")

    @notify.command()
    async def off(self, ctx):
        """Disable goal notifications."""
        await self.config.guild(ctx.guild).goal_notifications.set(False)
        await ctx.send("✅ Goal notifications disabled.")

    @notify.command()
    async def dm(self, ctx, mode: str):
        """Enable or disable goal DMs for yourself."""
        mode = mode.lower()
        if mode not in ("on", "off", "enable", "disable"):
            return await ctx.send("Usage: `.wc notify dm on` or `.wc notify dm off`.")

        async with self.config.guild(ctx.guild).all() as data:
            users = {str(uid) for uid in data.get("goal_notify_dm_users", [])}
            user_id = str(ctx.author.id)

            if mode in ("on", "enable"):
                users.add(user_id)
                await ctx.send("✅ Goal DMs enabled for you.")
            else:
                users.discard(user_id)
                await ctx.send("✅ Goal DMs disabled for you.")

            data["goal_notify_dm_users"] = list(users)

    @tasks.loop(seconds=60)
    async def goal_notification_loop(self):
        await self.bot.wait_until_ready()

        try:
            fixtures = await self.current_live_fixtures()
        except Exception:
            return

        if not fixtures:
            return

        for guild in self.bot.guilds:
            data = await self.config.guild(guild).all()
            if not data.get("goal_notifications"):
                continue

            channel = guild.get_channel(data.get("notify_channel"))
            role = guild.get_role(data.get("notify_role")) if data.get("notify_role") else None
            dm_users = [int(uid) for uid in data.get("goal_notify_dm_users", []) if uid is not None]
            announced = set(data.get("announced_goal_keys", []))

            for f in fixtures:
                fid = self.fixture_id(f)

                try:
                    events = await self.fixture_events(fid)
                except Exception:
                    continue

                for ev in events:
                    if ev.get("type") != "Goal":
                        continue

                    minute = ev["time"].get("elapsed", "?")
                    player = ev.get("player", {}).get("name", "Unknown")
                    team = ev.get("team", {}).get("name", "Unknown")
                    key = f"{fid}:{minute}:{player}:{team}"

                    if key in announced:
                        continue

                    announced.add(key)

                    e = self.embed("🚨 GOAL!", discord.Color.green())
                    e.add_field(name="Match", value=self.match_title(f), inline=False)
                    e.add_field(name="Scorer", value=f"⚽ **{player}** ({team})", inline=False)
                    e.add_field(name="Minute", value=f"**{minute}'**", inline=True)

                    content = role.mention if role else None
                    if channel:
                        await channel.send(content=content, embed=e)

                    for user_id in dm_users:
                        try:
                            user = guild.get_member(user_id) or self.bot.get_user(user_id)
                            if user:
                                await user.send(embed=e)
                        except Exception:
                            pass

            await self.config.guild(guild).announced_goal_keys.set(list(announced)[-200:])

    # -------------------------
    # Predictions
    # -------------------------

    @wc.group()
    async def predict(self, ctx):
        """Prediction game."""
        pass

    @predict.command(name="next")
    async def predict_next(self, ctx, home_goals: int, away_goals: int):
        """Predict the next match score."""
        fixtures = await self.upcoming_fixtures(1)
        if not fixtures:
            return await ctx.send("No upcoming match found.")

        f = fixtures[0]
        await self.save_prediction(ctx, f, home_goals, away_goals)

    @predict.command(name="match")
    async def predict_match(self, ctx, fixture_id: int, home_goals: int, away_goals: int):
        """Predict by fixture ID."""
        fixtures = await self.api_get("/fixtures", {"id": fixture_id})
        if not fixtures:
            return await ctx.send("Fixture not found.")

        f = fixtures[0]
        if not self.is_not_started(f):
            return await ctx.send("Predictions are closed for this match.")

        await self.save_prediction(ctx, f, home_goals, away_goals)

    async def save_prediction(self, ctx, fixture: dict, hg: int, ag: int):
        fid = str(self.fixture_id(fixture))
        user_id = str(ctx.author.id)

        async with self.config.guild(ctx.guild).prediction_scores() as data:
            data.setdefault("predictions", {})
            data.setdefault("points", {})
            data["predictions"].setdefault(fid, {})
            data["predictions"][fid][user_id] = {"home": hg, "away": ag}

        await ctx.send(f"✅ Prediction saved: **{self.match_title(fixture, False)}** — `{hg}-{ag}`")

    @predict.command()
    async def leaderboard(self, ctx):
        """Show prediction leaderboard."""
        await self.settle_predictions(ctx.guild)

        data = await self.config.guild(ctx.guild).prediction_scores()
        points = data.get("points", {})

        if not points:
            return await ctx.send("No prediction points yet.")

        sorted_users = sorted(points.items(), key=lambda x: x[1], reverse=True)[:10]

        e = self.embed("🏆 Prediction Leaderboard", discord.Color.gold())

        lines = []
        for i, (user_id, score) in enumerate(sorted_users, start=1):
            member = ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"User {user_id}"
            lines.append(f"**{i}. {name}** — {score} pts")

        e.description = "\n".join(lines)
        await ctx.send(embed=e)

    @predict.command()
    async def settle(self, ctx):
        """Manually settle finished predictions."""
        await self.settle_predictions(ctx.guild)
        await ctx.send("✅ Predictions settled.")

    async def settle_predictions(self, guild):
        data = await self.config.guild(guild).prediction_scores()
        predictions = data.get("predictions", {})
        points = data.get("points", {})
        settled = set(data.get("settled", []))

        for fid, user_preds in predictions.items():
            if fid in settled:
                continue

            try:
                fixtures = await self.api_get("/fixtures", {"id": fid})
            except Exception:
                continue

            if not fixtures:
                continue

            f = fixtures[0]
            if not self.is_finished(f):
                continue

            actual_h, actual_a = self.goals(f)

            for user_id, pred in user_preds.items():
                ph = pred["home"]
                pa = pred["away"]

                gained = 0

                if ph == actual_h and pa == actual_a:
                    gained = 3
                elif (ph - pa) == (actual_h - actual_a):
                    gained = 2
                elif (ph > pa and actual_h > actual_a) or (ph < pa and actual_h < actual_a) or (ph == pa and actual_h == actual_a):
                    gained = 1

                points[user_id] = points.get(user_id, 0) + gained

            settled.add(fid)

        data["points"] = points
        data["settled"] = list(settled)
        await self.config.guild(guild).prediction_scores.set(data)

    # -------------------------
    # Debug
    # -------------------------

    @wc.command()
    async def debug(self, ctx):
        """Debug current API data."""
        try:
            fixtures = await self.current_live_fixtures()
            if not fixtures:
                fixtures = await self.upcoming_fixtures(2)
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        text = json.dumps(fixtures[:1], indent=2, ensure_ascii=False)
        if len(text) > 1900:
            text = text[:1900]

        await ctx.send(f"```json\n{text}\n```")
