"""
engine/sources/xgabora_match_data_source.py

Historical EPL match data + odds from xgabora/Club-Football-Match-Data,
a GitHub-hosted, actively-updated dataset covering multiple leagues
from 2000 through the current season. Built as a fallback/replacement
for football-data.co.uk after that site experienced an extended outage
(confirmed via direct browser visit and curl from two independent
networks - genuinely down, not blocked).

VERIFICATION STATUS: unlike earlier candidate sources in this project,
this one was checked properly before writing any parser code:
- Confirmed the file exists and is reachable: a HEAD request to
  https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data/main/data/Matches.csv
  returned 200 (not 404, unlike two other candidates that turned out to
  be dead ends at the paths initially suggested for them).
- Confirmed the actual column headers via a ranged GET request (first
  ~2000 bytes), rather than assuming a schema from a description.
- Confirmed RECENT data is populated (not just old seasons with empty
  stat columns): a sample near the end of the file showed real EPL
  ("E0") rows dated April 2026 with shots, shots on target, corners,
  and odds all filled in - e.g. Brentford vs Everton, 2026-04-11.

SCHEMA DIFFERENCES FROM football-data.co.uk (the source this replaces):
This is NOT the same column schema. Column name mapping used below:
  FTHome/FTAway      -> home_goals/away_goals   (was FTHG/FTAG)
  HomeTarget/AwayTarget -> shots on target       (was HST/AST)
  HomeCorners/AwayCorners -> corners             (was HC/AC)
  OddHome/OddDraw/OddAway -> match-winner odds   (was B365H/B365D/B365A -
                                                   NOTE: this dataset does
                                                   not specify which
                                                   bookmaker OddHome/etc.
                                                   come from; treat as
                                                   "a" bookmaker's odds,
                                                   not confirmed as Bet365
                                                   specifically)
  Over25/Under25     -> 2.5 goals odds            (was B365>2.5/B365<2.5)
  MatchDate          -> ISO format (YYYY-MM-DD), NOT football-data.co.uk's
                         DD/MM/YY format - do not reuse that source's
                         date parser here.
  Division           -> "E0" for EPL, same convention as football-data.co.uk

FILE SIZE NOTE: the full Matches.csv is ~45MB and contains every match
across every league/season since 2000, not just EPL. This module
downloads the whole file (there is no server-side filtering available
for a raw GitHub file) and filters to Division == "E0" and the
requested season's date range client-side. On a GitHub Actions runner
this is a single ~45MB download, which is slower than football-data.co.uk's
per-season-per-league files but still well within Actions' resource
limits and, more importantly, ran against GitHub's own infrastructure
rather than a small site prone to outages.
"""

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests

from engine.sources.football_data_co_uk_source import HistoricalMatchOdds

logger = logging.getLogger(__name__)

MATCHES_CSV_URL = (
    "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data/"
    "main/data/Matches.csv"
)
EPL_DIVISION_CODE = "E0"


def _parse_float(value: str) -> Optional[float]:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int_from_float_string(value: str) -> Optional[int]:
    """This dataset stores integer-like stats (goals, corners, shots) as
    floats with a trailing ".0" (e.g. "3.0"), not as plain integers -
    confirmed from the sampled rows. Convert via float() first rather
    than int() directly, which would raise on the decimal point.
    """
    if value is None or value.strip() == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_match_date(value: str) -> Optional[date]:
    """This dataset uses ISO format (YYYY-MM-DD) - confirmed from sampled
    rows (e.g. "2026-04-11"). This is a DIFFERENT format from
    football-data.co.uk's DD/MM/YY, so this is a distinct parser, not
    a shared one - reusing the wrong parser here would silently
    misinterpret dates (e.g. reading "2026-04-11" as day=2026, an
    obviously-wrong value that would likely raise rather than silently
    corrupt data, but the risk of a subtler mix-up isn't worth taking).
    """
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        logger.warning(f"Could not parse date: {value!r}")
        return None


class XgaboraMatchDataSource:
    """Fetches and parses EPL match data (results, shots, corners, and
    betting odds) from the xgabora/Club-Football-Match-Data GitHub
    dataset. Produces the same HistoricalMatchOdds records as
    FootballDataCoUkSource, so this can be used as a drop-in
    replacement or fallback without changing any downstream code
    (Dixon-Coles fitting, Kelly staking, the backtest report, etc.).
    """

    def __init__(self, division_code: str = EPL_DIVISION_CODE):
        self.division_code = division_code
        self._cached_rows: Optional[list[dict]] = None

    def _fetch_all_rows(self) -> list[dict]:
        """Download and parse the full CSV once, caching the raw parsed
        rows in memory so fetch_season() can be called multiple times
        (e.g. once per season in a multi-season backtest) without
        re-downloading the ~45MB file each time within the same run.
        """
        if self._cached_rows is not None:
            return self._cached_rows

        logger.info(f"Downloading {MATCHES_CSV_URL} (this is a large file, ~45MB)...")
        response = requests.get(MATCHES_CSV_URL, timeout=120)
        response.raise_for_status()

        try:
            text = response.content.decode("utf-8")
        except UnicodeDecodeError:
            text = response.content.decode("latin-1")

        reader = csv.DictReader(io.StringIO(text))
        self._cached_rows = list(reader)
        logger.info(f"Downloaded and parsed {len(self._cached_rows)} total rows (all leagues, all seasons)")
        return self._cached_rows

    def fetch_season(self, start_year: int) -> list[HistoricalMatchOdds]:
        """Fetch and parse one EPL season, e.g. fetch_season(2024) for
        the 2024-25 season. Matches the same method signature as
        FootballDataCoUkSource.fetch_season() so the two are
        interchangeable from calling code's perspective.

        A season is defined here as matches with Division == "E0" and
        MatchDate falling between August 1 of start_year and July 31 of
        start_year+1 - the EPL season doesn't align with calendar years,
        so a plain year filter would incorrectly split a season in two.
        """
        all_rows = self._fetch_all_rows()

        season_start = date(start_year, 8, 1)
        season_end = date(start_year + 1, 7, 31)

        results = []
        skipped = 0

        for row in all_rows:
            if row.get("Division", "").strip() != self.division_code:
                continue

            match_date = _parse_match_date(row.get("MatchDate", ""))
            if match_date is None or not (season_start <= match_date <= season_end):
                continue

            home_team = row.get("HomeTeam", "").strip()
            away_team = row.get("AwayTeam", "").strip()
            if not home_team or not away_team:
                skipped += 1
                continue

            results.append(HistoricalMatchOdds(
                date=match_date,
                home_team=home_team,
                away_team=away_team,
                home_goals=_parse_int_from_float_string(row.get("FTHome", "")),
                away_goals=_parse_int_from_float_string(row.get("FTAway", "")),
                home_shots_on_target=_parse_int_from_float_string(row.get("HomeTarget", "")),
                away_shots_on_target=_parse_int_from_float_string(row.get("AwayTarget", "")),
                home_corners=_parse_int_from_float_string(row.get("HomeCorners", "")),
                away_corners=_parse_int_from_float_string(row.get("AwayCorners", "")),
                odds_home_win=_parse_float(row.get("OddHome", "")),
                odds_draw=_parse_float(row.get("OddDraw", "")),
                odds_away_win=_parse_float(row.get("OddAway", "")),
                odds_over_2_5=_parse_float(row.get("Over25", "")),
                odds_under_2_5=_parse_float(row.get("Under25", "")),
                # This dataset does not document which bookmaker OddHome/
                # OddDraw/OddAway come from (unlike football-data.co.uk,
                # which prefixes odds columns with the bookmaker name,
                # e.g. "B365H"). Labeled generically rather than
                # claiming "Bet365" without confirmation.
                bookmaker_used="unspecified" if row.get("OddHome") else None,
                raw=dict(row),
            ))

        if skipped:
            logger.warning(f"Skipped {skipped} {self.division_code} rows with missing team names in season {start_year}")

        logger.info(f"Season {start_year}: {len(results)} {self.division_code} matches found")
        return results
