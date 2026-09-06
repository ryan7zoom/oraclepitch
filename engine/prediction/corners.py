"""
engine/prediction/corners.py

Team-level corners prediction, Over-only per config.BET_TYPES["team_corners"].

STATUS: this model is built and testable, and CAN be used for
backtesting (football-data.co.uk provides real historical corners - see
engine/sources/football_data_co_uk_source.py's HC/AC columns). It is
NOT wired into the live daily pipeline (engine/main.py) or the
ensemble (engine/prediction/ensemble.py), because config.py's
BET_TYPES["team_corners"] is still False - no live data source for
upcoming fixtures' corners has been confirmed (see config.py's comment
on this). Enabling this for live predictions requires: (1) a live
corners data source, and (2) wiring CornersModel into ensemble.py and
main.py's pipeline the same way ShotsOnTargetModel is wired in.

Approach mirrors shots.py exactly (same Negative Binomial treatment,
same for/against home-away-split averaging), per the original project
spec's Part 2.2: expected corners = (team's own for-average +
opponent's against-average) / 2, regressed toward a league mean via the
Negative Binomial's overdispersion rather than a separate explicit
shrinkage step.
"""

from dataclasses import dataclass

import numpy as np
from scipy.stats import nbinom

import config


@dataclass
class TeamCornersProfile:
    """Average corners for/against, split by home/away."""
    team: str
    home_for_avg: float
    home_against_avg: float
    away_for_avg: float
    away_against_avg: float


def _moments_to_nbinom_params(mean: float, variance: float) -> tuple[float, float]:
    """Identical logic to shots.py's helper of the same name - duplicated
    rather than imported to keep corners.py fully independent and easy
    to delete/disable without touching shots.py, given corners' live
    pipeline is explicitly not enabled (see module docstring). If this
    duplication becomes a maintenance burden later, both could move to
    a shared engine/prediction/_count_distributions.py helper module.
    """
    if variance <= mean:
        n = 1e6
        p = n / (n + mean)
        return n, p
    p = mean / variance
    n = mean * p / (1 - p)
    return n, p


def generate_lines(expected_value: float, min_line: float = None, step: float = None) -> list[float]:
    """Generate a dynamic list of Over lines for a given expected value:
    start at min_line, step by `step`, up through the half-line at or
    below (expected_value + 3.0).

    This replaces the old fixed CORNERS_LINES list. Rationale: a fixed
    [3.5, 4.5, 5.5, 6.5] range was uninformative for a weak team
    averaging ~3.5 corners (Over 3.5 is close to a coin flip, and there
    was no lower line to show real value at, e.g. Over 1.5) and capped
    too low for a strong team averaging ~7.5 (Over 6.5 was the ceiling,
    losing all signal on the higher lines where a strong team's real
    edge shows up). Anchoring the top of the range to expected_value+3
    keeps the range wide enough to capture "clearly likely" through
    "clearly unlikely" for any given team, rather than a one-size range
    that's miscalibrated for teams far from the league average.

    NOTE ON ROUNDING: the spec that introduced this describes the
    ceiling as "round(team_avg + 3.0)" and gives a worked example: a
    weak team averaging 3.5 should get lines up to 6.5. Taken
    literally, Python's round() uses banker's rounding, so
    round(3.5 + 3.0) == round(6.5) == 6 (rounds to even), which would
    cap the range at 5.5 - one line short of the spec's own stated
    example. Since the worked example is the clearer statement of
    intent, this implementation uses floor(expected_value + 3.0) + 0.5
    instead of round(), which reproduces the spec's example exactly
    (3.5 -> ceiling 6.5, 7.5 -> ceiling 10.5) without relying on
    round-half-to-even behavior that would silently shift results
    depending on whether the input lands exactly on a .5 boundary.

    Always includes min_line even if expected_value is very low (e.g. a
    team averaging 1.0 corners still gets a floor line to report on,
    even though its Over probability there will correctly come out low).
    """
    import math

    min_line = min_line if min_line is not None else config.CORNERS_MIN_LINE
    step = step if step is not None else config.CORNERS_STEP

    max_line = math.floor(expected_value + 3.0) + 0.5
    if max_line < min_line:
        max_line = min_line

    lines = []
    current = min_line
    # Small epsilon guards against float accumulation (e.g. 1.5 + 1.0*6
    # landing at 7.499999999 instead of 7.5) causing the loop to drop
    # the final intended line.
    while current <= max_line + 1e-9:
        lines.append(round(current, 1))
        current += step
    return lines


class CornersModel:
    """Predicts team-level corners Over probabilities. Over-only, per
    config.BET_TYPES["team_corners"] - matches the same bookmaker-driven
    Over-only constraint applied to shots on target (see shots.py), even
    though the original spec did not explicitly restrict corners to
    Over-only the way it did for shots. This module still only
    implements Over, both for consistency with the rest of this
    codebase's markets and because the live corners feature isn't
    enabled regardless (see module docstring) - Under support can be
    added later without disrupting anything if it turns out to be
    needed once/if live corners data is found.

    Lines are now DYNAMIC per team (see generate_lines()) rather than a
    fixed shared list - each side's Over lines are generated from that
    side's own expected corners, so a weak team's home lines and a
    strong team's away lines in the same match can span entirely
    different ranges.
    """

    def __init__(self, league_avg_home: float = 5.5, league_avg_away: float = 4.5,
                 overdispersion_factor: float = 1.3):
        self.league_avg_home = league_avg_home
        self.league_avg_away = league_avg_away
        self.overdispersion_factor = overdispersion_factor
        self.profiles: dict[str, TeamCornersProfile] = {}

    def fit(self, profiles: list[TeamCornersProfile]):
        self.profiles = {p.team: p for p in profiles}

    def _get_profile(self, team: str) -> TeamCornersProfile:
        if team in self.profiles:
            return self.profiles[team]
        return TeamCornersProfile(
            team=team,
            home_for_avg=self.league_avg_home,
            home_against_avg=self.league_avg_away,
            away_for_avg=self.league_avg_away,
            away_against_avg=self.league_avg_home,
        )

    def expected_corners(self, home_team: str, away_team: str) -> tuple[float, float]:
        home_profile = self._get_profile(home_team)
        away_profile = self._get_profile(away_team)

        expected_home = (home_profile.home_for_avg + away_profile.away_against_avg) / 2
        expected_away = (away_profile.away_for_avg + home_profile.home_against_avg) / 2
        return expected_home, expected_away

    def team_over_probs(self, home_team: str, away_team: str, lines: list[float] = None) -> dict:
        """Compute Over probabilities for both teams.

        lines: if provided, used for BOTH sides (kept for backward
            compatibility / explicit-line testing). If None (the normal
            case), each side gets its OWN dynamically generated line
            list based on that side's expected corners - see
            generate_lines(). This means home and away line lists can
            differ in both range and length, which is why the return
            value's "home" and "away" dicts may have different keys.
        """
        expected_home, expected_away = self.expected_corners(home_team, away_team)

        def over_probs_for(mean, line_list):
            variance = mean * self.overdispersion_factor
            n, p = _moments_to_nbinom_params(mean, variance)
            max_corners = 30
            probs = nbinom.pmf(np.arange(max_corners + 1), n, p)
            out = {}
            for line in line_list:
                threshold = int(line + 0.5)
                out[f"over_{line}"] = float(probs[threshold:].sum())
            return out

        home_lines = lines if lines is not None else generate_lines(expected_home)
        away_lines = lines if lines is not None else generate_lines(expected_away)

        return {
            "home": over_probs_for(expected_home, home_lines),
            "away": over_probs_for(expected_away, away_lines),
        }


def build_corners_profiles_from_historical(historical_rows) -> list[TeamCornersProfile]:
    """Aggregate per-team home/away corners for/against averages from a
    list of records exposing home_team, away_team, home_corners,
    away_corners (works directly with
    engine.sources.football_data_co_uk_source.HistoricalMatchOdds
    without importing that module here, to avoid a hard dependency
    between prediction/ and sources/ - any object with these four
    attributes works, e.g. in a unit test with plain mock objects).

    Rows missing corners data (home_corners or away_corners is None)
    are skipped rather than treated as zero, consistent with how
    engine/main.py's _build_shots_profiles() handles missing shots data
    - a missing stat is "unknown," not "zero corners," and treating it
    as zero would silently drag every affected team's average down.
    """
    from collections import defaultdict

    home_for = defaultdict(list)
    home_against = defaultdict(list)
    away_for = defaultdict(list)
    away_against = defaultdict(list)

    rows_with_data = 0
    for r in historical_rows:
        if getattr(r, "home_corners", None) is None or getattr(r, "away_corners", None) is None:
            continue
        rows_with_data += 1
        home_for[r.home_team].append(r.home_corners)
        home_against[r.home_team].append(r.away_corners)
        away_for[r.away_team].append(r.away_corners)
        away_against[r.away_team].append(r.home_corners)

    if rows_with_data == 0:
        return []

    teams = set(home_for.keys()) | set(away_for.keys())
    profiles = []
    for team in teams:
        def avg(lst):
            return sum(lst) / len(lst) if lst else None

        h_for = avg(home_for.get(team, []))
        h_against = avg(home_against.get(team, []))
        a_for = avg(away_for.get(team, []))
        a_against = avg(away_against.get(team, []))

        if None in (h_for, h_against, a_for, a_against):
            continue

        profiles.append(TeamCornersProfile(
            team=team, home_for_avg=h_for, home_against_avg=h_against,
            away_for_avg=a_for, away_against_avg=a_against,
        ))
    return profiles
