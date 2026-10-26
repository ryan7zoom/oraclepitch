"""
engine/kickoff.py

Kickoff times in GMT+8.

openfootball lists each match's kickoff as a plain "HH:MM" in the LOCAL
time of the league's country (e.g. "20:00" for an EPL match means
20:00 in London). This file converts that to GMT+8, handling daylight
saving automatically.

Matches with no time in the data (common for La Liga, Serie A,
Bundesliga and Ligue 1) return None and are shown as "TBC".
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("engine.kickoff")

GMT8 = timezone(timedelta(hours=8))

# League key (see LEAGUE_CODES in engine/sources/openfootball_source.py)
# -> timezone that league's listed kickoff times are in.
LEAGUE_TZ = {
    "epl": "Europe/London",
    "la_liga": "Europe/Madrid",
    "bundesliga": "Europe/Berlin",
    "serie_a": "Europe/Rome",
    "ligue_1": "Europe/Paris",
}

KICKOFF_CSS = """
  .kickoff { display: inline-block; min-width: 44px; margin-right: 8px; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }
  .kickoff.tbc { color: var(--text-dim); font-weight: 400; }
"""

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})")


def kickoff_gmt8(fixture, league: str) -> Optional[datetime]:
    """Kickoff as a GMT+8 datetime, or None if the data has no time."""
    raw = getattr(fixture, "raw", None) or {}
    match = _TIME_RE.match(str(raw.get("time") or ""))
    if not match:
        return None
    try:
        local = datetime(
            fixture.date.year, fixture.date.month, fixture.date.day,
            int(match.group(1)), int(match.group(2)),
            tzinfo=ZoneInfo(LEAGUE_TZ[league]),
        )
        return local.astimezone(GMT8)
    except Exception as e:
        logger.warning(f"Could not convert kickoff time for {league} {fixture.date}: {e}")
        return None


def format_kickoff(kickoff: Optional[datetime]) -> str:
    """HTML for the kickoff badge: '04:00' or 'TBC'."""
    if kickoff is None:
        return '<span class="kickoff tbc">TBC</span>'
    return f'<span class="kickoff">{kickoff.strftime("%H:%M")}</span>'
