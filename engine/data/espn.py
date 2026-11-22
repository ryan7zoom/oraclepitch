"""
engine/data/espn.py

ESPN's unofficial public JSON API. No key. Verified from GitHub Actions: the
match summary returns corners, yellow/red cards, shots, shots on target and
OFFSIDES for all five leagues (offsides are the reason this module exists -
football-data.co.uk does not have them).

Cost: 1 request per match day per league (scoreboard) + 1 per match (summary),
so every call is counted against a budget (see EspnClient).

Half-time goals: the scoreboard's line scores came back empty in testing, so
half-time goals are derived from the goal events (best effort, and only
accepted if the goals add up to the final score).
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from engine.data import http

logger = logging.getLogger("engine.data.espn")

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

SLUGS = {
    "epl": "eng.1",
    "la_liga": "esp.1",
    "bundesliga": "ger.1",
    "serie_a": "ita.1",
    "ligue_1": "fra.1",
}

# ESPN team-statistic name -> our field stem (<stem>_h / <stem>_a)
STAT_NAMES = {
    "wonCorners": "co",
    "cornerKicks": "co",
    "yellowCards": "yc",
    "redCards": "rc",
    "totalShots": "sh",
    "shotsOnTarget": "sot",
    "offsides": "of",
}


class BudgetExhausted(Exception):
    """The per-run ESPN request budget is used up."""


@dataclass
class EspnEvent:
    id: str
    date: object            # datetime.date (UTC date of kickoff)
    completed: bool
    home_name: str
    away_name: str
    home_score: Optional[int]
    away_score: Optional[int]
    ht: Optional[tuple]     # (home, away) half-time goals, or None
    kickoff: Optional[datetime] = None   # aware UTC datetime of kickoff, when ESPN gives it
    state: str = ""                      # ESPN status: "pre" (not started), "in", "post"


class EspnClient:
    def __init__(self, budget: int, delay: float = 0.25, session=None):
        self.budget = budget
        self.used = 0
        self.delay = delay
        self.session = session or http.make_session()

    def _get_json(self, url: str) -> Optional[dict]:
        if self.used >= self.budget:
            raise BudgetExhausted()
        self.used += 1
        time.sleep(self.delay)
        r = http.get(self.session, url)
        if r.status_code != 200:
            logger.warning(f"ESPN HTTP {r.status_code}: {url} {r.text[:120]!r}")
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def scoreboard(self, league: str, day) -> list:
        data = self._get_json(f"{BASE}/{SLUGS[league]}/scoreboard?dates={day:%Y%m%d}")
        return parse_scoreboard(data) if data else []

    def summary(self, league: str, event_id: str) -> Optional[dict]:
        data = self._get_json(f"{BASE}/{SLUGS[league]}/summary?event={event_id}")
        return parse_summary_stats(data) if data else None


# ---------------------------------------------------------------------------
# Parsing (pure functions, unit-tested with sample payloads)
# ---------------------------------------------------------------------------
def _int(v) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None


def _is_first_half(detail: dict) -> Optional[bool]:
    clock = detail.get("clock") or {}
    m = re.match(r"\s*(\d+)", str(clock.get("displayValue") or ""))
    if m:
        return int(m.group(1)) <= 45   # "45'+3'" counts as first half
    v = clock.get("value")
    if isinstance(v, (int, float)):
        return v <= 2700
    return None


def half_time_from_details(details: list, home_id: str, away_id: str,
                           final_h: int, final_a: int) -> Optional[tuple]:
    """Half-time score from goal events. Accepted only if the goals add up to
    the final score; own goals are tried both ways (ESPN's team on an own
    goal is ambiguous)."""
    goals = []
    for d in details or []:
        if not d.get("scoringPlay"):
            continue
        first = _is_first_half(d)
        team_id = str((d.get("team") or {}).get("id", ""))
        if first is None or team_id not in (str(home_id), str(away_id)):
            return None
        goals.append((first, team_id == str(home_id), bool(d.get("ownGoal"))))

    for flip_own in (False, True):
        tot = [0, 0]
        half = [0, 0]
        for first, is_home, own in goals:
            side = 0 if (is_home != (own and flip_own)) else 1
            tot[side] += 1
            if first:
                half[side] += 1
        if tot == [final_h, final_a]:
            return half[0], half[1]
    return None


def parse_event(ev: dict) -> Optional[EspnEvent]:
    try:
        comp = ev["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = sides["home"], sides["away"]
        completed = bool(ev.get("status", {}).get("type", {}).get("completed"))
        d = datetime.strptime(ev["date"][:10], "%Y-%m-%d").date()
        hs, as_ = _int(home.get("score")), _int(away.get("score"))
        ht = None
        if completed and hs is not None and as_ is not None:
            ht = half_time_from_details(
                comp.get("details"), home["team"]["id"], away["team"]["id"], hs, as_)
        kickoff = None
        try:
            kickoff = datetime.fromisoformat(str(ev["date"]).replace("Z", "+00:00"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        state = str(ev.get("status", {}).get("type", {}).get("state") or "")
        return EspnEvent(
            id=str(ev["id"]), date=d, completed=completed,
            home_name=home["team"]["displayName"], away_name=away["team"]["displayName"],
            home_score=hs, away_score=as_, ht=ht, kickoff=kickoff, state=state,
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def parse_scoreboard(data: dict) -> list:
    events = [parse_event(e) for e in data.get("events", [])]
    return [e for e in events if e is not None]


def parse_summary_stats(data: dict) -> Optional[dict]:
    """{'sh_h': 12, 'sh_a': 13, 'of_h': 3, ...} from a match summary."""
    teams = (data.get("boxscore") or {}).get("teams") or []
    out = {}
    for t in teams:
        side = {"home": "h", "away": "a"}.get(t.get("homeAway"))
        if side is None:
            continue
        for s in t.get("statistics", []):
            stem = STAT_NAMES.get(s.get("name"))
            val = _int(s.get("displayValue"))
            if stem and val is not None:
                out.setdefault(f"{stem}_{side}", val)
    return out or None
