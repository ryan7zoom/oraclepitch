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
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from engine.sources.base import MatchResult

logger = logging.getLogger(__name__)

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season_folder}/{league_code}.json"

# Maps our internal league identifier to (openfootball file code, xgabora
# Division code). Verified directly against real, current data for all
# five leagues (not from documentation) - see the per-league name
# mapping dicts below for the verification details specific to each.
LEAGUE_CODES = {
    "epl": {"openfootball": "en.1", "xgabora_division": "E0"},
    "la_liga": {"openfootball": "es.1", "xgabora_division": "SP1"},
    "bundesliga": {"openfootball": "de.1", "xgabora_division": "D1"},
    "serie_a": {"openfootball": "it.1", "xgabora_division": "I1"},
    "ligue_1": {"openfootball": "fr.1", "xgabora_division": "F1"},
}


def _season_folder(start_year: int) -> str:
    """e.g. 2026 -> '2026-27'."""
    end_year_short = (start_year + 1) % 100
    return f"{start_year}-{end_year_short:02d}"


def _normalize_team_name(name: str, league: str = "epl") -> str:
    """Convert an openfootball team name to the short-name convention
    used by xgabora/Club-Football-Match-Data for the given league.
    Without this, team-name lookups between the two sources would
    silently return zero matching history for every team, since the
    analyzer does exact string matching on team name.

    VERIFICATION: every mapping table below was built from REAL,
    CURRENT data fetched directly from both sources (not guessed or
    taken from documentation) - openfootball names extracted from each
    league's actual 2026-27 season JSON file, xgabora names extracted
    from the most recent ~100 rows of that league's division code in
    the live Matches.csv file. The non-English leagues required
    materially more mapping work than English did: xgabora's naming is
    much more heavily abbreviated for these leagues (e.g. "1. FC Köln"
    -> "FC Koln", "FC Bayern München" -> "Bayern Munich", "Athletic
    Club" -> "Ath Bilbao", "Paris Saint-Germain FC" -> "Paris SG") -
    none of these would be caught by a generic suffix-stripping or
    accent-removal rule, which is exactly why an explicit per-league
    table is used rather than a general heuristic.

    CAVEAT: each league's table only covers the ~18-23 teams observed
    in the specific samples checked (current season for openfootball,
    most recent ~100 rows for xgabora) - a team that was relegated
    before or promoted after those samples may not appear in a table
    and will pass through unmapped (see the "Teams not in this
    mapping" note below for what happens then).

    Teams not in the mapping for the given league are returned
    unchanged, which will correctly result in zero streak history
    being found for them rather than crashing - a visible "no history
    found" rather than a silent mismatch.
    """
    mapping = _TEAM_NAME_MAPPINGS.get(league, {})
    return mapping.get(name, name)


_EPL_TEAM_NAME_MAPPING = {
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

# La Liga: verified against openfootball's 2026-27 season file and
# xgabora's most recent ~100 SP1 rows (see module docstring). NOTE:
# the openfootball sample included some team names (Elche, Malaga,
# Racing Santander) that don't look like a self-consistent current
# season alongside Barcelona/Real Madrid/Atletico - this wasn't
# investigated further since the mapping only needs the name STRING to
# be correct when/if that team appears, not to validate openfootball's
# own season data - but it's flagged here in case a given matchday's
# fixtures look unexpectedly a stale/mixed season during live use.
_LA_LIGA_TEAM_NAME_MAPPING = {
    "Athletic Club": "Ath Bilbao",
    "CA Osasuna": "Osasuna",
    "Club Atlético de Madrid": "Ath Madrid",
    "Deportivo Alavés": "Alaves",
    "Elche CF": "Elche",
    "FC Barcelona": "Barcelona",
    "Getafe CF": "Getafe",
    "Levante UD": "Levante",
    "Málaga CF": "Malaga",
    "RC Celta de Vigo": "Celta",
    "RC Deportivo La Coruña": "La Coruna",
    "RCD Espanyol de Barcelona": "Espanol",
    "Rayo Vallecano de Madrid": "Vallecano",
    "Real Betis Balompié": "Betis",
    "Real Madrid CF": "Real Madrid",
    "Real Racing Club de Santander": "Santander",
    "Real Sociedad de Fútbol": "Sociedad",
    "Sevilla FC": "Sevilla",
    "Valencia CF": "Valencia",
    "Villarreal CF": "Villarreal",
    # Seen in xgabora's recent sample but not in the specific
    # openfootball 2026-27 sample checked (may be a different season /
    # promoted-relegated club) - included as a reasonable best guess,
    # not independently confirmed the way the above 20 are.
    "Girona FC": "Girona",
    "RCD Mallorca": "Mallorca",
    "Real Oviedo": "Oviedo",
}

# Bundesliga: verified against openfootball's 2026-27 season file and
# xgabora's most recent ~100 D1 rows. Heavy abbreviation on both German
# club prefixes ("1. FC", "FC", "TSG", "SV") and city names.
_BUNDESLIGA_TEAM_NAME_MAPPING = {
    "1. FC Köln": "FC Koln",
    "1. FC Union Berlin": "Union Berlin",
    "1. FSV Mainz 05": "Mainz",
    "Bayer 04 Leverkusen": "Leverkusen",
    "Borussia Dortmund": "Dortmund",
    "Borussia Mönchengladbach": "M'gladbach",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "FC Augsburg": "Augsburg",
    "FC Bayern München": "Bayern Munich",
    "FC Schalke 04": "Schalke 04",
    "Hamburger SV": "Hamburg",
    "RB Leipzig": "RB Leipzig",
    "SC Freiburg": "Freiburg",
    "SC Paderborn 07": "Paderborn",
    "SV 07 Elversberg": "Elversberg",
    "SV Werder Bremen": "Werder Bremen",
    "TSG 1899 Hoffenheim": "Hoffenheim",
    "VfB Stuttgart": "Stuttgart",
    # Seen in xgabora's recent sample but not in the specific
    # openfootball 2026-27 sample checked - best guess, not confirmed.
    "1. FC Heidenheim 1846": "Heidenheim",
    "FC St. Pauli": "St Pauli",
    "VfL Wolfsburg": "Wolfsburg",
}

# Serie A: verified against openfootball's 2026-27 season file and
# xgabora's most recent ~100 I1 rows.
_SERIE_A_TEAM_NAME_MAPPING = {
    "AC Milan": "Milan",
    "AC Monza": "Monza",
    "ACF Fiorentina": "Fiorentina",
    "AS Roma": "Roma",
    "Atalanta BC": "Atalanta",
    "Bologna FC 1909": "Bologna",
    "Cagliari Calcio": "Cagliari",
    "Como 1907": "Como",
    "FC Internazionale Milano": "Inter",
    "Frosinone Calcio": "Frosinone",
    "Genoa CFC": "Genoa",
    "Juventus FC": "Juventus",
    "Parma Calcio 1913": "Parma",
    "SS Lazio": "Lazio",
    "SSC Napoli": "Napoli",
    "Torino FC": "Torino",
    "US Lecce": "Lecce",
    "US Sassuolo Calcio": "Sassuolo",
    "Udinese Calcio": "Udinese",
    "Venezia FC": "Venezia",
    # Seen in xgabora's recent sample but not in the specific
    # openfootball 2026-27 sample checked - best guess, not confirmed.
    "Hellas Verona FC": "Verona",
    "US Cremonese": "Cremonese",
    "AC Pisa 1909": "Pisa",
}

# Ligue 1: verified against openfootball's 2026-27 season file and
# xgabora's most recent ~100 F1 rows.
_LIGUE_1_TEAM_NAME_MAPPING = {
    "AJ Auxerre": "Auxerre",
    "AS Monaco FC": "Monaco",
    "Angers SCO": "Angers",
    "ES Troyes AC": "Troyes",
    "FC Lorient": "Lorient",
    "Le Havre AC": "Le Havre",
    "Le Mans FC": "Le Mans",
    "Lille OSC": "Lille",
    "OGC Nice": "Nice",
    "Olympique Lyonnais": "Lyon",
    "Olympique de Marseille": "Marseille",
    "Paris FC": "Paris FC",
    "Paris Saint-Germain FC": "Paris SG",
    "RC Strasbourg Alsace": "Strasbourg",
    "Racing Club de Lens": "Lens",
    "Stade Brestois 29": "Brest",
    "Stade Rennais FC 1901": "Rennes",
    "Toulouse FC": "Toulouse",
    # Seen in xgabora's recent sample but not in the specific
    # openfootball 2026-27 sample checked - best guess, not confirmed.
    "FC Nantes": "Nantes",
    "FC Metz": "Metz",
}

_TEAM_NAME_MAPPINGS = {
    "epl": _EPL_TEAM_NAME_MAPPING,
    "la_liga": _LA_LIGA_TEAM_NAME_MAPPING,
    "bundesliga": _BUNDESLIGA_TEAM_NAME_MAPPING,
    "serie_a": _SERIE_A_TEAM_NAME_MAPPING,
    "ligue_1": _LIGUE_1_TEAM_NAME_MAPPING,
}


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
    """Fetches fixtures (played and upcoming) for any of the supported
    top-5 leagues (see LEAGUE_CODES) from openfootball's free,
    GitHub-hosted dataset. Used specifically for fixture discovery
    (finding what matches are happening on a given date) - NOT a
    replacement for historical stats/odds sources, since this dataset
    only provides team names, dates, and final scores, nothing else
    (no shots, corners, or odds).
    """

    def __init__(self):
        self._cache: dict = {}

    def _fetch_season_raw(self, start_year: int, league: str = "epl") -> list:
        """Fetch and cache the raw match list for one season/league.
        Raises requests.HTTPError if the season file doesn't exist
        (e.g. requesting a season too far in the future that hasn't
        been created yet, or an unsupported league code).
        """
        cache_key = (start_year, league)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if league not in LEAGUE_CODES:
            raise ValueError(f"Unsupported league: {league!r}. Supported: {list(LEAGUE_CODES.keys())}")

        folder = _season_folder(start_year)
        league_code = LEAGUE_CODES[league]["openfootball"]
        url = BASE_URL.format(season_folder=folder, league_code=league_code)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        matches = data.get("matches", [])
        self._cache[cache_key] = matches
        logger.info(f"Fetched {len(matches)} matches for {league} season {folder} from openfootball")
        return matches

    def get_fixtures_for_date(
        self, target_date: date, league: str = "epl",
        season_start_year: Optional[int] = None, window_days: int = 2,
    ) -> list:
        """Return all matches in the given league scheduled or played
        between target_date and target_date + window_days (inclusive).

        WHY A WINDOW, NOT AN EXACT DATE: originally this matched only
        target_date exactly. A real production issue surfaced this as
        a bug: the user is in Bangladesh (UTC+6), the GitHub Actions
        runner computes "today" in UTC, and with an exact-date match, a
        fixture happening "tomorrow" from the user's perspective could
        fall entirely outside what a single day's exact match would
        ever check - not because the data was missing (verified real
        matches did exist in the source for the affected dates), but
        because of the date-boundary/timezone mismatch between how the
        runner computes "today" and the user's actual local day. A
        multi-day window is a robust fix that doesn't depend on getting
        every timezone edge case exactly right - see engine/main.py's
        _today_bd() for the actual local-date fix which addresses the
        root cause; this window is a second, complementary layer of
        robustness (also naturally gives the user a few days' advance
        visibility into upcoming fixtures, which is a reasonable
        feature in its own right, not just a bug workaround).

        league: one of the keys in LEAGUE_CODES (e.g. "epl", "la_liga").
        season_start_year: if not provided, inferred the same way
            engine.sources.api_football_source.ApiFootballSource does
            (season "starts" in July, labeled by its starting year).
        window_days: how many additional days beyond target_date to
            include (default 2, giving a 3-day total window: today,
            tomorrow, day after tomorrow).
        """
        if season_start_year is None:
            season_start_year = target_date.year if target_date.month >= 7 else target_date.year - 1

        try:
            raw_matches = self._fetch_season_raw(season_start_year, league)
        except Exception as e:
            logger.error(f"Failed to fetch {league} season {season_start_year} from openfootball: {e}")
            return []

        window_end = target_date + timedelta(days=window_days)

        results = []
        for m in raw_matches:
            match_date = _parse_match_date(m.get("date", ""))
            if match_date is None or not (target_date <= match_date <= window_end):
                continue

            home_goals, away_goals = _extract_score(m)
            status = "finished" if home_goals is not None else "scheduled"

            home_team = _normalize_team_name(m.get("team1", ""), league)
            away_team = _normalize_team_name(m.get("team2", ""), league)
            synthetic_id = f"openfootball:{league}:{match_date.isoformat()}:{home_team}:{away_team}"

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

    def get_todays_fixtures(self, league: str = "epl") -> list:
        """Convenience method matching ApiFootballSource's interface."""
        return self.get_fixtures_for_date(date.today(), league=league)
