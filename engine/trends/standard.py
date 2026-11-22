"""
engine/trends/standard.py

The STANDARD NUMBER of a team for one stat in one match, and the line band built on it.

standard = average of four things (each over the team's / opponent's last STD_GAMES games):
    1. the team's own average at this venue (Juventus at home)
    2. the team's own average over all games
    3. what the opponent allows at ITS venue (Venezia away)
    4. what the opponent allows over all games

For "total" it is the home standard plus the away standard.

A line is only worth showing when a bookmaker could offer it, i.e. when it sits inside
    standard x (1 - BAND_DOWN)  <=  line  <=  standard x (1 + BAND_UP)
so "Juventus corners Under 11.5" is dropped when Juventus' standard is 6.3 (limit 9.45).
"""

from typing import Dict, List, Optional, Tuple

from engine.trends import settings as S
from engine.trends.markets import _pair

STD_STATS = ("goals", "shots", "sot", "corners", "yellow", "offsides")


def _avg(games, stat: str, kind: str, venue: Optional[str] = None) -> Optional[float]:
    """games: [(Match, side)] newest first. kind: 'own' (the team's count) or 'allowed' (its opponent's)."""
    vals = []
    for m, side in games:
        if venue is not None and side != venue:
            continue
        h, a = _pair(m, stat)
        if h is None or a is None:
            continue
        own, other = (h, a) if side == "home" else (a, h)
        vals.append(own if kind == "own" else other)
        if len(vals) >= S.STD_GAMES:
            break
    return sum(vals) / len(vals) if vals else None


def _team_standard(own_games, opp_games, side: str, stat: str) -> Optional[float]:
    opp_side = "away" if side == "home" else "home"
    parts = [_avg(own_games, stat, "own", side), _avg(own_games, stat, "own"),
             _avg(opp_games, stat, "allowed", opp_side), _avg(opp_games, stat, "allowed")]
    parts = [p for p in parts if p is not None]
    return sum(parts) / len(parts) if len(parts) >= 3 else None


def standard_numbers(history: Dict[str, list], home: str, away: str, as_of) -> Dict[Tuple[str, str], float]:
    """{(stat, 'home'|'away'|'total'): standard number} for one fixture. Only games before `as_of`."""
    def games(team):
        return [(m, "home" if m.home == team else "away") for m in history.get(team, []) if m.date < as_of]
    hg, ag = games(home), games(away)
    out: Dict[Tuple[str, str], float] = {}
    for stat in STD_STATS:
        h = _team_standard(hg, ag, "home", stat)
        a = _team_standard(ag, hg, "away", stat)
        if h is not None:
            out[(stat, "home")] = h
        if a is not None:
            out[(stat, "away")] = a
        if h is not None and a is not None:
            out[(stat, "total")] = h + a
    return out


def line_in_band(stat: str, line: Optional[float], std: Optional[float]) -> bool:
    """True when a bookmaker could plausibly offer this line (or when we cannot tell)."""
    if line is None or std is None or (stat, line) in S.BAND_EXEMPT:
        return True
    return std * (1 - S.BAND_DOWN) <= line <= std * (1 + S.BAND_UP)
