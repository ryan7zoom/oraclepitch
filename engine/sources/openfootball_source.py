"""
engine/sources/openfootball_source.py

Fixture discovery source using openfootball/football.json - a free,
no-key, GitHub-hosted, public-domain dataset of football results and
fixtures, actively maintained (980+ stars, updated regularly).

WHY THIS EXISTS: API-Football's free tier does not allow access to the
current season's fixtures at all - confirmed via a real production
error: {'plan': 'Free plans do not have access to this season, try
from 2022 to 2024.'}. This makes API-Football's free tier unusable for
"what EPL matches are happening today" (fixture discovery), even
though it still works fine for other things this project uses it for
historically. openfootball has no such restriction and is used here
specifically to replace API-Football for fixture discovery only.

VERIFICATION STATUS: unlike several earlier candidate sources explored
in this project that turned out to be dead ends (wrong URLs, stale
data, schema mismatches), this one was verified properly before
writing any code:
- Confirmed https://raw.githubusercontent.com/openfootball/football.json/master/{season}/en.1.json
  returns 200 with real data (checked directly via curl, not just
  documentation).
- Confirmed the file's early matches (already played) have accurate,
  real-world-verified scores (cross-checked Liverpool 4-2 Bournemouth,
  2025-08-15, against multiple independent sports news sources -
  exact match).
- Confirmed played vs. unplayed matches are distinguishable: matches
  with a "score" key have already happened; matches with NO "score"
  key at all are genuinely upcoming fixtures. This is the key property
  this module relies on to answer "what's happening on date X."

SCHEMA NOTES:
- Team names are full club names ("Liverpool FC", "Manchester United FC"),
  not short names ("Liverpool", "Man United") - this must match
  whatever naming convention other data sources in this pipeline use,
  or streak/prediction lookups keyed by team name will silently fail
  to find a team's history. See _normalize_team_name() below - as of
  this writing this returns the name UNCHANGED (no normalization
  applied), which is flagged explicitly because the other data sources
  in this project (xgabora, API-Football) may use different naming
  conventions for the same clubs, and this mismatch has NOT yet been
  verified to line up. This is the single most likely integration bug
  if this module is wired into a pipeline that cross-references team
  names against those other sources - check this first if streak
  lookups mysteriously return no data for a team that should have
  history.
- The "score" field's shape is inconsistent even for played matches:
  most entries use {"ht": [x,y], "ft": [x,y]}, but at least one
  observed entry used a bare [x,y] array with no "ht"/"ft" keys at
  all (Aston Villa vs Newcastle, 2025-08-16, in the 2025-26 file).
  Both shapes are handled below.
- Season folder naming is "{start_year}-{end_year_short}", e.g.
  "2026-27" for the season starting August 2026.
"""

import logging
from datetime import date, datetime
from typing import Optional

import requests

from engine.sources.base import MatchResult

logger = logging.getLogger(__name__)

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season_folder}/en.1.json"


def _season_folder(start_year: int) -> str:
    """e.g. 2026 -> '2026-27'."""
    end_year_short = (start_year + 1) % 100
    return f"{start_year}-{end_year_short:02d}"


def _normalize_team_name(name: str) -> str:
    """Convert an openfootball team name to the short-name convention
    used by xgabora/Club-Football-Match-Data (the dataset the streak
    analyzer's historical lookups are keyed against - see
    engine/streaks/analyzer.py). Without this, team-name lookups
    between the two sources would silently return zero matching
    history for every team, since the analyzer does exact string
    matching on team name.

    VERIFICATION: both team-name lists below were pulled from REAL,
    CURRENT data fetched directly (not from documentation or guesses):
    - openfootball names: extracted from the actual 2026-27 season
      JSON file's real match entries.
    - xgabora names: extracted from the actual, current EPL ("E0") rows
      in the live Matches.csv file (most recent ~400 EPL rows checked).

    CAVEAT: "West Ham United FC"/"Wolverhampton Wanderers FC" and their
    mapped short names are NOT directly confirmed the way the other 20
    entries are - neither club appeared in the specific 2026-27/recent-
    xgabora samples checked (both were outside the Premier League in
    the checked period), so these two entries are a reasonable
    best-guess based on common naming convention, not a verified
    match. If either club is promoted back to the league, confirm
    their exact spelling in both sources before relying on this
    mapping for them specifically.

    A naive rule (e.g. "strip FC/AFC suffix") would incorrectly miss
    three real, confirmed cases: "Manchester City FC" / "Manchester
    United FC" (xgabora abbreviates to "Man City" / "Man United", an
    entirely different first word, not a suffix issue) and
    "Nottingham Forest FC" (xgabora uses the abbreviated "Nott'm
    Forest"). This is exactly the kind of mismatch that would silently
    corrupt streak data with no error - hence an explicit mapping
    table rather than a general suffix-stripping heuristic.

    Teams not in this mapping (e.g. a newly-promoted club in a future
    season not yet seen in either dataset) are returned unchanged,
    which will correctly result in zero streak history being found for
    them (since they won't match xgabora's naming either) rather than
    crashing - this is a reasonable degradation, not a silent
    corruption, since "no history found" is visibly different from "an
    incorrect team's history was used."
    """
    mapping = {
        "AFC Bournemouth": "Bournemouth",
        "Arsenal FC": "Arsenal",
        "Aston Villa FC": "Aston Villa",
        "Brentford FC": "Brentford",
        "Brighton & Hove Albion FC": "Brighton",
        "Chelsea FC": "Chelsea",
        "Coventry City FC": "Coventry",
        "Crystal Palace FC": "Crystal Palace",
        "Everton FC": "Everton",
        "Fulham FC": "Fulham",
        "Hull City AFC": "Hull",
        "Ipswich Town FC": "Ipswich",
        "Leeds United FC": "Leeds",
        "Liverpool FC": "Liverpool",
        "Manchester City FC": "Man City",
        "Manchester United FC": "Man United",
        "Newcastle United FC": "Newcastle",
        "Nottingham Forest FC": "Nott'm Forest",
        "Sunderland AFC": "Sunderland",
        "Tottenham Hotspur FC": "Tottenham",
        "West Ham United FC": "West Ham",
        "Wolverhampton Wanderers FC": "Wolves",
        "Burnley FC": "Burnley",
    }
    return mapping.get(name, name)


def _extract_score(match_json: dict) -> tuple:
    """Return (home_goals, away_goals) or (None, None) if the match has
    no score at all (i.e. hasn't been played yet). Handles both
    observed score shapes: {"ht": [...], "ft": [...]} and a bare
    [home, away] array with no keys - see module docstring.
    """
    score = match_json.get("score")
    if score is None:
        return None, None

    if isinstance(score, dict):
        ft = score.get("ft")
        if ft and len(ft) == 2:
            return ft[0], ft[1]
        return None, None

    if isinstance(score, list) and len(score) == 2:
        return score[0], score[1]

    return None, None


def _parse_match_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        logger.warning(f"Could not parse date: {value!r}")
        return None


class OpenFootballSource:
    """Fetches EPL fixtures (played and upcoming) from openfootball's
    free, GitHub-hosted dataset. Used specifically for fixture
    discovery (finding what matches are happening on a given date) -
    NOT a replacement for historical stats/odds sources, since this
    dataset only provides team names, dates, and final scores, nothing
    else (no shots, corners, or odds).
    """

    def __init__(self):
        self._cache: dict = {}

    def _fetch_season_raw(self, start_year: int) -> list:
        """Fetch and cache the raw match list for one season. Raises
        requests.HTTPError if the season file doesn't exist (e.g.
        requesting a season too far in the future that hasn't been
        created yet).
        """
        if start_year in self._cache:
            return self._cache[start_year]

        folder = _season_folder(start_year)
        url = BASE_URL.format(season_folder=folder)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        matches = data.get("matches", [])
        self._cache[start_year] = matches
        logger.info(f"Fetched {len(matches)} matches for season {folder} from openfootball")
        return matches

    def get_fixtures_for_date(self, target_date: date, season_start_year: Optional[int] = None) -> list:
        """Return all EPL matches scheduled or played on target_date.

        season_start_year: if not provided, inferred the same way
            engine.sources.api_football_source.ApiFootballSource does
            (season "starts" in July, labeled by its starting year) -
            duplicated here rather than imported to avoid a hard
            dependency between this module and api_football_source.py.
        """
        if season_start_year is None:
            season_start_year = target_date.year if target_date.month >= 7 else target_date.year - 1

        try:
            raw_matches = self._fetch_season_raw(season_start_year)
        except Exception as e:
            logger.error(f"Failed to fetch season {season_start_year} from openfootball: {e}")
            return []

        results = []
        for m in raw_matches:
            match_date = _parse_match_date(m.get("date", ""))
            if match_date != target_date:
                continue

            home_goals, away_goals = _extract_score(m)
            status = "finished" if home_goals is not None else "scheduled"

            home_team = _normalize_team_name(m.get("team1", ""))
            away_team = _normalize_team_name(m.get("team2", ""))
            synthetic_id = f"openfootball:{match_date.isoformat()}:{home_team}:{away_team}"

            results.append(MatchResult(
                fixture_id=synthetic_id,
                date=match_date,
                season=season_start_year,
                home_team=home_team,
                away_team=away_team,
                home_goals=home_goals,
                away_goals=away_goals,
                status=status,
                raw=m,
            ))

        return results

    def get_todays_fixtures(self) -> list:
        """Convenience method matching ApiFootballSource's interface."""
        return self.get_fixtures_for_date(date.today())
