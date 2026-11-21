"""
engine/data/football_data.py

football-data.co.uk season CSVs: the backbone of the match table.
Free, no key, verified reachable from GitHub Actions. Provides full-time and
half-time goals, shots, shots on target, corners, yellow and red cards.
Offsides are NOT in current files (older seasons may have HO/AO - parsed if
present) - ESPN supplies them (engine/data/espn.py).

Team names here are the project's canonical names (e.g. "Man United",
"Celta", "Paris SG").
"""

import csv
import io
import logging
from datetime import datetime
from typing import Optional

from engine.data import http
from engine.data.model import Match

logger = logging.getLogger("engine.data.football_data")

URL = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"

DIVISIONS = {
    "epl": "E0",
    "la_liga": "SP1",
    "bundesliga": "D1",
    "serie_a": "I1",
    "ligue_1": "F1",
}

COLUMN_MAP = {
    "FTHG": "ft_h", "FTAG": "ft_a", "HTHG": "ht_h", "HTAG": "ht_a",
    "HS": "sh_h", "AS": "sh_a", "HST": "sot_h", "AST": "sot_a",
    "HC": "co_h", "AC": "co_a", "HY": "yc_h", "AY": "yc_a",
    "HR": "rc_h", "AR": "rc_a", "HO": "of_h", "AO": "of_a",
}


def season_code(start_year: int) -> str:
    """2026 -> '2627'."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def parse_date(s: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def _int(v) -> Optional[int]:
    try:
        v = (v or "").strip()
        return int(float(v)) if v != "" else None
    except ValueError:
        return None


def parse_csv(text: str, league: str, season: int) -> list:
    """Finished matches only (a row needs a date, both teams and a final score)."""
    matches = []
    for row in csv.DictReader(io.StringIO(text)):
        d = parse_date(row.get("Date", ""))
        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        if not (d and home and away):
            continue
        m = Match(league=league, season=season, date=d, home=home, away=away)
        for col, field_name in COLUMN_MAP.items():
            setattr(m, field_name, _int(row.get(col)))
        if not m.finished:
            continue
        m.add_source("fd")
        matches.append(m)
    return matches


def fetch_season(league: str, season: int, session) -> list:
    """All finished matches of one league-season. Returns [] on 404 (season
    file does not exist); raises on network failure."""
    url = URL.format(code=season_code(season), div=DIVISIONS[league])
    r = http.get(session, url)
    if r.status_code != 200:
        logger.warning(f"[{league}] {url} -> HTTP {r.status_code}")
        return []
    text = r.content.decode("utf-8-sig", errors="replace")
    return parse_csv(text, league, season)
