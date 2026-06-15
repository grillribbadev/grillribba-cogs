import aiohttp
import discord
from datetime import datetime, timezone
from redbot.core import commands, Config


BASE_URL = "https://v3.football.api-sports.io"


class WorldCup(commands.Cog):
    """FIFA World Cup 2026 scores, live matches and schedule."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=202606261001)
        self.config.register_global(
            api_key=None,
            league_id=1,
            season=2026,
            timezone="Europe/Oslo",
        )

    async def api_get(self, endpoint, params=None):
        api_key = await self.config.api_key()
        if not api_key:
            raise RuntimeError("No API key set. Use `.wcset key YOUR_API_KEY` first.")

        headers = {"x-apisports-key": api_key}

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(f"{BASE_URL}{endpoint}", params=params or {}, timeout=25) as resp:
                data = await resp.json(content_type=None)

                if resp.status != 200:
                    raise RuntimeError(f"API returned status {resp.status}: {data}")

                if data.get("errors"):
                    raise RuntimeError(str(data["errors"]))

                return data.get("response", [])

    def make_embed(self, title, color=discord.Color.gold()):
        embed = discord.Embed(title=title, color=color)
        embed.set_footer(text="FIFA World Cup 2026 • API-Football")
        return embed

    def fixture_time(self, fixture):
        raw = fixture["fixture"]["date"]
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y • %H:%M")
        except Exception:
            return raw

    def score_line(self, fixture):
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        hg = fixture["goals"]["home"]
        ag = fixture["goals"]["away"]

        hg = 0 if hg is None else hg
        ag = 0 if ag is None else ag

        return home, away, hg, ag

    def status_text(self, fixture):
        status = fixture["fixture"]["status"]
        short = status.get("short") or "?"
        elapsed = status.get("elapsed")

        if elapsed is not None:
            return f"{short} • {elapsed}'"

        return short

    async def get_goal_events(self, fixture_id):
        events = await self.api_get("/fixtures/events", {"fixture": fixture_id})
        goals = []

        for event in events:
            if event.get("type") != "Goal":
                continue

            minute = event["time"].get("elapsed", "?")
            extra = event["time"].get("extra")
            player = event.get("player", {}).get("name") or "Unknown"
            team = event.get("team", {}).get("name") or ""
            detail = event.get("detail") or "Goal"

            minute_text = f"{minute}'"
            if extra:
                minute_text = f"{minute}+{extra}'"

            goals.append(f"⚽ **{minute_text}** — **{player}** ({team}) • {detail}")

        return goals

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
        """Set league ID."""
        await self.config.league_id.set(league_id)
        await ctx.send(f"✅ League ID set to `{league_id}`.")

    @wcset.command()
    async def season(self, ctx, season: int):
        """Set season."""
        await self.config.season.set(season)
        await ctx.send(f"✅ Season set to `{season}`.")

    @wcset.command()
    async def timezone(self, ctx, timezone: str):
        """Set timezone, example: Europe/Oslo."""
        await self.config.timezone.set(timezone)
        await ctx.send(f"✅ Timezone set to `{timezone}`.")

    @wcset.command()
    async def settings(self, ctx):
        """Show current settings."""
        league_id = await self.config.league_id()
        season = await self.config.season()
        timezone = await self.config.timezone()
        api_key = await self.config.api_key()

        embed = self.make_embed("⚙️ World Cup Settings", discord.Color.blue())
        embed.add_field(name="API key", value="✅ Set" if api_key else "❌ Missing", inline=False)
        embed.add_field(name="League ID", value=f"`{league_id}`", inline=True)
        embed.add_field(name="Season", value=f"`{season}`", inline=True)
        embed.add_field(name="Timezone", value=f"`{timezone}`", inline=False)

        await ctx.send(embed=embed)

    @wcset.command()
    async def findleague(self, ctx, *, search: str = "world cup"):
        """Find league IDs."""
        try:
            leagues = await self.api_get("/leagues", {"search": search})
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not leagues:
            return await ctx.send("No leagues found.")

        embed = self.make_embed(f"🔎 League search: {search}", discord.Color.blue())

        for item in leagues[:10]:
            league = item.get("league", {})
            country = item.get("country", {})
            seasons = item.get("seasons", [])

            latest = seasons[-1]["year"] if seasons else "?"
            embed.add_field(
                name=f"{league.get('name')} — ID `{league.get('id')}`",
                value=f"Country: {country.get('name', 'Unknown')} • Latest season: `{latest}`",
                inline=False,
            )

        await ctx.send(embed=embed)

    @wc.command()
    async def current(self, ctx):
        """Show live World Cup match, score, minute and scorers."""
        league_id = await self.config.league_id()
        season = await self.config.season()
        timezone = await self.config.timezone()

        try:
            fixtures = await self.api_get(
                "/fixtures",
                {
                    "live": "all",
                    "league": league_id,
                    "season": season,
                    "timezone": timezone,
                },
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            embed = self.make_embed("🔴 No live World Cup match right now", discord.Color.red())
            embed.description = "API-Football returned no live fixtures for the configured league and season."
            embed.add_field(
                name="Check settings",
                value="Run `.wcset settings` or `.wcset findleague world cup`.",
                inline=False,
            )
            return await ctx.send(embed=embed)

        for fixture in fixtures[:3]:
            fixture_id = fixture["fixture"]["id"]
            home, away, hg, ag = self.score_line(fixture)
            status = fixture["fixture"]["status"]
            elapsed = status.get("elapsed", "?")

            embed = self.make_embed(
                f"🔴 LIVE: {home} {hg} - {ag} {away}",
                discord.Color.green(),
            )

            embed.add_field(name="⏱️ Minute", value=f"**{elapsed}'**", inline=True)
            embed.add_field(name="📌 Status", value=f"`{status.get('long', 'Live')}`", inline=True)
            embed.add_field(name="🏟️ Venue", value=fixture["fixture"].get("venue", {}).get("name") or "Unknown", inline=False)

            try:
                goals = await self.get_goal_events(fixture_id)
            except Exception:
                goals = []

            embed.add_field(
                name="⚽ Goals",
                value="\n".join(goals) if goals else "No goals yet, or goal events not available.",
                inline=False,
            )

            await ctx.send(embed=embed)

    @wc.command()
    async def next(self, ctx):
        """Show next World Cup match."""
        league_id = await self.config.league_id()
        season = await self.config.season()
        timezone = await self.config.timezone()

        try:
            fixtures = await self.api_get(
                "/fixtures",
                {
                    "league": league_id,
                    "season": season,
                    "next": 1,
                    "timezone": timezone,
                },
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            return await ctx.send("No upcoming match found.")

        fixture = fixtures[0]
        home, away, _, _ = self.score_line(fixture)

        embed = self.make_embed("⏭️ Next World Cup Match", discord.Color.blue())
        embed.add_field(name="Match", value=f"**{home} vs {away}**", inline=False)
        embed.add_field(name="🕒 Kickoff", value=self.fixture_time(fixture), inline=False)
        embed.add_field(name="📌 Status", value=f"`{self.status_text(fixture)}`", inline=True)
        embed.add_field(name="🏟️ Venue", value=fixture["fixture"].get("venue", {}).get("name") or "Unknown", inline=False)

        await ctx.send(embed=embed)

    @wc.command()
    async def schedule(self, ctx, amount: int = 10):
        """Show upcoming World Cup schedule."""
        amount = max(1, min(amount, 20))

        league_id = await self.config.league_id()
        season = await self.config.season()
        timezone = await self.config.timezone()

        try:
            fixtures = await self.api_get(
                "/fixtures",
                {
                    "league": league_id,
                    "season": season,
                    "next": amount,
                    "timezone": timezone,
                },
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            return await ctx.send("No upcoming matches found.")

        embed = self.make_embed(f"📅 Next {len(fixtures)} World Cup Matches", discord.Color.gold())

        for fixture in fixtures:
            home, away, _, _ = self.score_line(fixture)
            league_round = fixture["league"].get("round", "World Cup")
            embed.add_field(
                name=f"{home} vs {away}",
                value=f"🕒 {self.fixture_time(fixture)}\n🏆 {league_round}",
                inline=False,
            )

        await ctx.send(embed=embed)

    @wc.command()
    async def today(self, ctx):
        """Show today's World Cup matches."""
        league_id = await self.config.league_id()
        season = await self.config.season()
        timezone_name = await self.config.timezone()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            fixtures = await self.api_get(
                "/fixtures",
                {
                    "league": league_id,
                    "season": season,
                    "date": today,
                    "timezone": timezone_name,
                },
            )
        except Exception as e:
            return await ctx.send(f"❌ `{e}`")

        if not fixtures:
            return await ctx.send("No World Cup matches found today.")

        embed = self.make_embed("📆 Today's World Cup Matches", discord.Color.purple())

        for fixture in fixtures:
            home, away, hg, ag = self.score_line(fixture)
            status = fixture["fixture"]["status"].get("short", "?")

            score = f"{hg} - {ag}" if status not in ["NS", "TBD"] else "vs"

            embed.add_field(
                name=f"{home} {score} {away}",
                value=f"🕒 {self.fixture_time(fixture)}\n📌 `{self.status_text(fixture)}`",
                inline=False,
            )

        await ctx.send(embed=embed)
