"""
engine/streaks/expected.py

One expected number per team per stat for an upcoming fixture, plus a
compact set of over/under lines around it.

HOW THE EXPECTED VALUE IS BUILT (home team's corners as the example):
    attack   = home team's average corners in its last 10 HOME games
    defense  = away team's average corners ALLOWED in its last 10 AWAY games
    baseline = league-wide average home-team corners (last year)
    expected = attack * defense / baseline

Home and away are always kept separate. The away team's number is built
the same way from its away attack and the home team's home defense.

Small samples are pulled slightly toward the league average (SHRINK).
Percentages are Poisson probabilities for each line - a rough guide, not
exact odds.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from engine.streaks.analyzer import get_stat_value, get_opponent_stat_value

# ---- Settings (change here) ------------------------------------------------
WINDOW = 10          # most recent same-venue games used for each team
MIN_GAMES = 5        # fewer usable games than this -> stat is skipped
SHRINK = 3           # small-sample pull toward the league average
H2H_WINDOW = 10      # last N meetings at this venue
H2H_MIN = 3          # show H2H only if at least this many meetings exist
LEAGUE_DAYS = 365    # league averages use matches from the last N days
# -----------------------------------------------------------------------------

STATS = ("shots_on_target", "corners", "goals")


@dataclass
class StatExpectation:
    stat: str
    home_expected: float
    away_expected: float
    home_lines: list = field(default_factory=list)   # [(side, line, pct), ...]
    away_lines: list = field(default_factory=list)
    total_expected: float = 0.0
    total_lines: list = field(default_factory=list)
    h2h: Optional[dict] = None                       # {"n", "home_avg", "away_avg"}


def _poisson_cdf(k: int, mean: float) -> float:
    if k < 0:
        return 0.0
    term = math.exp(-mean)
    total = term
    for i in range(1, k + 1):
        term *= mean / i
        total += term
    return min(total, 1.0)


def _lines_for(expected: float) -> list:
    """Three half-lines around the expectation, each with its more
    likely side: [("Over", 4.5, 0.82), ("Over", 5.5, 0.64), ("Under", 6.5, 0.58)].
    """
    center = math.floor(expected) + 0.5
    start = max(0.5, center - 1)
    out = []
    for line in (start, start + 1, start + 2):
        p_over = 1.0 - _poisson_cdf(int(math.floor(line)), expected)
        if p_over >= 0.5:
            out.append(("Over", line, p_over))
        else:
            out.append(("Under", line, 1.0 - p_over))
    return out


def _league_avgs(matches: list, stat: str, as_of: date) -> Optional[tuple]:
    """(avg home-side value, avg away-side value) across the league."""
    cutoff = as_of - timedelta(days=LEAGUE_DAYS)

    def collect(only_recent: bool):
        h, a = [], []
        for m in matches:
            if m.date is None or m.date >= as_of:
                continue
            if only_recent and m.date < cutoff:
                continue
            hv, av = getattr(m, f"home_{stat}"), getattr(m, f"away_{stat}")
            if hv is not None and av is not None:
                h.append(hv)
                a.append(av)
        return h, a

    h, a = collect(True)
    if len(h) < 50:
        h, a = collect(False)
    if len(h) < 50:
        return None
    return sum(h) / len(h), sum(a) / len(a)


def _shrunk_avg(values: list, baseline: float) -> Optional[float]:
    if len(values) < MIN_GAMES:
        return None
    return (sum(values) + SHRINK * baseline) / (len(values) + SHRINK)


def _values(matches: list, team: str, stat: str, allowed: bool) -> list:
    fn = get_opponent_stat_value if allowed else get_stat_value
    vals = [fn(m, team, stat) for m in matches]
    return [v for v in vals if v is not None]


def _h2h(analyzer, home_team: str, away_team: str, stat: str, as_of: date) -> Optional[dict]:
    # Same venue only: meetings where this home team was also at home.
    meetings = [
        m for m in analyzer._h2h_matches_before(home_team, away_team, as_of)
        if m.home_team == home_team
    ][:H2H_WINDOW]
    h_vals, a_vals = [], []
    for m in meetings:
        hv = get_stat_value(m, home_team, stat)
        av = get_stat_value(m, away_team, stat)
        if hv is not None and av is not None:
            h_vals.append(hv)
            a_vals.append(av)
    if len(h_vals) < H2H_MIN:
        return None
    return {"n": len(h_vals), "home_avg": sum(h_vals) / len(h_vals), "away_avg": sum(a_vals) / len(a_vals)}


def compute_expected(analyzer, home_team: str, away_team: str, as_of: date) -> Optional[dict]:
    """Returns {"home_team", "away_team", "stats": [StatExpectation, ...]}
    or None if no stat has enough data.
    """
    home_games = analyzer._team_matches_before(home_team, as_of, "home_only")[:WINDOW]
    away_games = analyzer._team_matches_before(away_team, as_of, "away_only")[:WINDOW]

    results = []
    for stat in STATS:
        league = _league_avgs(analyzer.matches, stat, as_of)
        if league is None:
            continue
        league_home, league_away = league
        if league_home <= 0 or league_away <= 0:
            continue

        home_att = _shrunk_avg(_values(home_games, home_team, stat, allowed=False), league_home)
        away_def = _shrunk_avg(_values(away_games, away_team, stat, allowed=True), league_home)
        away_att = _shrunk_avg(_values(away_games, away_team, stat, allowed=False), league_away)
        home_def = _shrunk_avg(_values(home_games, home_team, stat, allowed=True), league_away)
        if None in (home_att, away_def, away_att, home_def):
            continue

        home_exp = home_att * away_def / league_home
        away_exp = away_att * home_def / league_away
        total_exp = home_exp + away_exp
        results.append(StatExpectation(
            stat=stat,
            home_expected=home_exp, away_expected=away_exp,
            home_lines=_lines_for(home_exp), away_lines=_lines_for(away_exp),
            total_expected=total_exp, total_lines=_lines_for(total_exp),
            h2h=_h2h(analyzer, home_team, away_team, stat, as_of),
        ))

    if not results:
        return None
    return {"home_team": home_team, "away_team": away_team, "stats": results}
