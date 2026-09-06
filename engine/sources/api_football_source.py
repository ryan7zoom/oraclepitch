"""
engine/sources/api_football_source.py

Data source backed by API-Football (api-sports.io / RapidAPI).

VERIFICATION STATUS (read before trusting this blindly):
- The `/fixtures` and `/fixtures/statistics` endpoints are real, documented
  endpoints per API-Football's own docs and confirmed via their support
  forum for corner-kick data specifically.
- The exact statistic "type" strings (e.g. "Corner Kicks", "Shots on Goal")
  are taken from widely-reproduced community examples of the API's response
  shape, but have NOT been confirmed against a live response by either this
  assistant or the user as of this writing. The get_fixture_statistics()
  method below defensively looks up stats by matching on substrings of the
  "type" field (case-insensitive) rather than assuming exact strings, to
  reduce the chance of silent failure if the real field names differ
  slightly (e.g. "Shots on Target" vs "Shots on Goal").
- Free tier is 100 requests/day. This module does not implement caching
  itself - callers (main.py, backtest/simulator.py) are responsible for
  caching fetched data to disk (see data/historical/) so re-runs don't
  burn quota re-fetching the same fixtures.
- Historical depth on the free tier is not confirmed. Before relying on
  BACKTEST_START_SEASON in config.py, run a smoke test fetching fixtures
  for that season and confirm results come back non-empty.

If any of the above turns out to be wrong once tested against a real key,
update this docstring and the STAT_KEY_MAP below - don't just patch around
it silently.
"""

import time
from datetime import date, datetime
from typing import Optional

import requests

import config
from engine.sources.base import DataSource, MatchResult


# Maps our internal stat names to (lowercase) substrings we'll look for in
# the API's "type" field for each statistic entry. Using substring matching
# because the exact casing/wording of these type strings has not been
# verified against a live response.
STAT_KEY_MAP = {
    "shots_on_target": ["shots on goal", "shots on target"],
    "corners": ["corner"],
}


class ApiFootballSource(DataSource):
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or config.API_FOOTBALL_KEY
        if not self.api_key:
            raise ValueError(
                "API_FOOTBALL_KEY is not set. Set it as an environment "
                "variable or pass it explicitly."
            )
        self.headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": config.API_FOOTBALL_HOST,
        }
        self.base_url = config.API_FOOTBALL_BASE_URL
        self._request_count = 0

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------
    def _get(self, endpoint: str, params: dict) -> dict:
        """Make a rate-limited GET request and return the parsed JSON body.

        Raises requests.HTTPError on non-200 responses and RuntimeError if
        the API itself reports errors in the response body (API-Football
        returns HTTP 200 even for some error conditions, e.g. bad params).
        """
        url = f"{self.base_url}/{endpoint}"
        response = requests.get(url, headers=self.headers, params=params, timeout=15)
        self._request_count += 1
        response.raise_for_status()

        body = response.json()
        errors = body.get("errors")
        if errors:
            # API-Football returns "errors": [] when fine, or a dict/list
            # with content when something's wrong (bad key, rate limit, etc.)
            if isinstance(errors, dict) and len(errors) > 0:
                raise RuntimeError(f"API-Football error on {endpoint}: {errors}")
            if isinstance(errors, list) and len(errors) > 0:
                raise RuntimeError(f"API-Football error on {endpoint}: {errors}")

        # Politely throttle - free tier is 100 req/day, no need to hammer it
        time.sleep(config.API_FOOTBALL_REQUEST_DELAY_SECONDS)
        return body

    @staticmethod
    def _parse_fixture(entry: dict) -> MatchResult:
        """Convert one entry from /fixtures into a MatchResult with whatever
        is available (goals usually are, shots/corners are not present on
        this endpoint - those require a separate /fixtures/statistics call).
        """
        fixture = entry["fixture"]
        teams = entry["teams"]
        goals = entry.get("goals", {})
        league = entry.get("league", {})

        status_short = fixture.get("status", {}).get("short", "")
        # API-Football status codes: "FT" = full time (finished),
        # "NS" = not started, "1H"/"2H"/"HT" = in play, etc.
        if status_short == "FT":
            status = "finished"
        elif status_short == "NS":
            status = "scheduled"
        elif status_short in ("1H", "2H", "HT", "ET", "P", "LIVE"):
            status = "in_play"
        else:
            status = status_short.lower() or "unknown"

        fixture_date = fixture.get("date", "")
        try:
            parsed_date = datetime.fromisoformat(fixture_date.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            parsed_date = None

        return MatchResult(
            fixture_id=str(fixture["id"]),
            date=parsed_date,
            season=league.get("season"),
            home_team=teams["home"]["name"],
            away_team=teams["away"]["name"],
            home_goals=goals.get("home"),
            away_goals=goals.get("away"),
            status=status,
            raw=entry,
        )

    def _match_stat_value(self, stats_list: list[dict], stat_key: str) -> Optional[int]:
        """Search a team's statistics array for a stat matching stat_key
        (via STAT_KEY_MAP substrings) and return its integer value, or None
        if not found / null.
        """
        substrings = STAT_KEY_MAP.get(stat_key, [])
        for item in stats_list:
            type_str = str(item.get("type", "")).lower()
            if any(sub in type_str for sub in substrings):
                value = item.get("value")
                if value is None:
                    return None
                try:
                    return int(value)
                except (ValueError, TypeError):
                    return None
        return None

    # -----------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------
    def get_fixtures(
        self, season: int, date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[MatchResult]:
        params = {"league": config.LEAGUE_ID, "season": season}
        if date_from:
            params["from"] = date_from.isoformat()
        if date_to:
            params["to"] = date_to.isoformat()

        body = self._get("fixtures", params)
        return [self._parse_fixture(entry) for entry in body.get("response", [])]

    def get_fixture_statistics(self, fixture_id: str) -> MatchResult:
        # First get the base fixture info (goals, teams, date)
        fixture_body = self._get("fixtures", {"id": fixture_id})
        fixture_entries = fixture_body.get("response", [])
        if not fixture_entries:
            raise ValueError(f"No fixture found for id {fixture_id}")
        result = self._parse_fixture(fixture_entries[0])

        # Then get statistics (shots, corners, etc.)
        stats_body = self._get("fixtures/statistics", {"fixture": fixture_id})
        stats_entries = stats_body.get("response", [])

        if len(stats_entries) < 2:
            # Statistics not available for this fixture (common for very
            # old or lower-tier matches) - return result with stats as None
            # rather than guessing or raising, so callers can decide to skip.
            return result

        home_team_name = result.home_team
        for entry in stats_entries:
            team_name = entry.get("team", {}).get("name", "")
            stats_list = entry.get("statistics", [])
            sot = self._match_stat_value(stats_list, "shots_on_target")
            corners = self._match_stat_value(stats_list, "corners")

            if team_name == home_team_name:
                result.home_shots_on_target = sot
                result.home_corners = corners
            else:
                result.away_shots_on_target = sot
                result.away_corners = corners

        return result

    def get_todays_fixtures(self) -> list[MatchResult]:
        today = date.today()
        current_season = self._infer_current_season(today)
        return self.get_fixtures(
            season=current_season, date_from=today, date_to=today
        )

    @staticmethod
    def _infer_current_season(as_of: date) -> int:
        """API-Football labels an EPL season by its starting year
        (e.g. the 2025/26 season is season=2025). The EPL season starts
        around August, so before ~July we're still in the previous
        labeled season.
        """
        if as_of.month >= 7:
            return as_of.year
        return as_of.year - 1

    @property
    def request_count(self) -> int:
        """Number of API calls made by this instance so far - useful for
        respecting the 100/day free tier limit across a script run.
        """
        return self._request_count
