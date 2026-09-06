"""
engine/prediction/shots.py

Team-level and match-level shots-on-target prediction.

CRITICAL CONSTRAINT: this module must NEVER expose an "under" probability
anywhere in its public interface. The user's bookmaker doesn't offer
Under bets on shots on target, so there is no legitimate use for that
number downstream, and exposing it at all creates a foot-gun for a
future edit to accidentally surface it in the output. Only Over
probabilities are computed and returned.

Approach: shots on target are modeled as a Negative Binomial count
variable (more appropriate than plain Poisson for this stat, which
tends to be overdispersed - variance exceeds the mean - in real match
data), fit per team from historical for/against averages, home/away
split, then combined for the match total.

NOTE: This model has NOT yet been fit against real historical shots
data (that requires the API-Football statistics endpoint, unverified
as of this writing - see engine/sources/api_football_source.py). The
fitting logic below is written and tested against synthetic data with
known overdispersion, but real-world parameters (team averages, the
negative binomial dispersion parameter) still need to be estimated
once real data is flowing.
"""

from dataclasses import dataclass

import numpy as np
from scipy.stats import nbinom, poisson

import config


@dataclass
class TeamShotsProfile:
    """Average shots on target for/against, split by home/away."""
    team: str
    home_for_avg: float
    home_against_avg: float
    away_for_avg: float
    away_against_avg: float


def _moments_to_nbinom_params(mean: float, variance: float) -> tuple[float, float]:
    """Convert a (mean, variance) pair to the (n, p) parameterization
    scipy.stats.nbinom expects. Requires variance > mean (overdispersion);
    falls back to a Poisson-equivalent (via a very large n) if variance
    is at or below the mean, since Negative Binomial isn't defined for
    that case with the parameterization below.
    """
    if variance <= mean:
        # No overdispersion detected - approximate a Poisson via
        # a Negative Binomial with a very large n (converges to Poisson)
        n = 1e6
        p = n / (n + mean)
        return n, p
    p = mean / variance
    n = mean * p / (1 - p)
    return n, p


def generate_lines(expected_value: float, min_line: float = None, step: float = None) -> list[float]:
    """Generate a dynamic list of team-level Over lines for a given
    expected value: start at min_line, step by `step`, up through the
    half-line at or below (expected_value + 3.0).

    See config.py's SHOTS_MIN_LINE/STEP comment and corners.py's
    identical helper (engine/prediction/corners.py's generate_lines())
    for the full rationale, including why this uses
    floor(expected_value + 3.0) + 0.5 rather than round(expected_value
    + 3.0) - the latter uses banker's rounding and would produce a
    result one line short of the spec's own worked example at exact
    .5 boundaries (duplicated here rather than shared to keep shots.py
    independent of corners.py - see corners.py's module docstring on
    why that independence matters given corners' live-pipeline status).
    """
    import math

    min_line = min_line if min_line is not None else config.SHOTS_MIN_LINE
    step = step if step is not None else config.SHOTS_STEP

    max_line = math.floor(expected_value + 3.0) + 0.5
    if max_line < min_line:
        max_line = min_line

    lines = []
    current = min_line
    while current <= max_line + 1e-9:
        lines.append(round(current, 1))
        current += step
    return lines


def generate_match_lines(expected_total: float, step: float = None) -> list[float]:
    """Generate dynamic Over lines for MATCH-level (combined both teams')
    shots on target. Per the spec, match-level lines start at a higher
    floor (3.0, i.e. first line is Over 3.0->2.5-adjusted to 2.5... see
    note below) than team-level lines (1.5), since two teams combined
    almost always clear a low single-team-sized threshold - a 1.5 floor
    at the match level would be uninformative (~100% probability),
    unlike at the team level where it's a meaningful, sometimes close
    to 50/50 line for a genuinely weak team.

    NOTE ON THE ".5" CONVENTION: all Over lines elsewhere in this
    codebase are half-lines (2.5, 3.5, ...) to avoid push/tie
    ambiguity, consistent with standard bookmaker convention. The spec
    text says "generate lines from 3.0" for match totals - interpreted
    here as the same half-line convention applied starting from the
    lowest half-line at or above 3.0, i.e. 3.5, for consistency with
    every other line in this codebase (a bare "Over 3.0" line is
    unusual precisely because 3-3 combined shots pushes rather than
    settling the bet, which is the exact ambiguity half-lines exist to
    avoid) rather than introducing a whole-number-line special case
    used nowhere else in the system.

    Uses the same floor(expected_total + 3.0) + 0.5 ceiling formula as
    generate_lines() above, for the same round()-boundary reason.
    """
    import math

    step = step if step is not None else config.SHOTS_STEP
    min_line = 3.5

    max_line = math.floor(expected_total + 3.0) + 0.5
    if max_line < min_line:
        max_line = min_line

    lines = []
    current = min_line
    while current <= max_line + 1e-9:
        lines.append(round(current, 1))
        current += step
    return lines


class ShotsOnTargetModel:
    """Predicts team and match-level shots-on-target Over probabilities.

    Lines are now DYNAMIC (see generate_lines()/generate_match_lines())
    rather than a fixed shared list - see corners.py's identical change
    for the full rationale (weak teams need a lower floor to show real
    signal, strong teams need higher lines to not cap out early).
    """

    def __init__(self, league_avg_home: float = 4.8, league_avg_away: float = 3.9,
                 overdispersion_factor: float = 1.3):
        """
        league_avg_home / league_avg_away: fallback league-average shots on
            target for home/away teams, used to regress small-sample team
            estimates toward the mean and as defaults for teams with no
            history (e.g. promoted teams).
        overdispersion_factor: variance = mean * overdispersion_factor.
            1.3 is a reasonable starting assumption for shots-on-target
            data (mild overdispersion); should be re-estimated once real
            historical data is available (see module docstring).
        """
        self.league_avg_home = league_avg_home
        self.league_avg_away = league_avg_away
        self.overdispersion_factor = overdispersion_factor
        self.profiles: dict[str, TeamShotsProfile] = {}

    def fit(self, profiles: list[TeamShotsProfile]):
        """Load per-team shots profiles (computed elsewhere from historical
        match data - this class only handles the probability modeling,
        not the aggregation of raw stats into averages).
        """
        self.profiles = {p.team: p for p in profiles}

    def _get_profile(self, team: str) -> TeamShotsProfile:
        if team in self.profiles:
            return self.profiles[team]
        # Unknown team (e.g. newly promoted) - fall back to league averages
        return TeamShotsProfile(
            team=team,
            home_for_avg=self.league_avg_home,
            home_against_avg=self.league_avg_away,
            away_for_avg=self.league_avg_away,
            away_against_avg=self.league_avg_home,
        )

    def expected_shots_on_target(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Return (expected_home_sot, expected_away_sot) as the average of
        the attacking team's for-average and the defending team's
        against-average, per the spec's stated approach.
        """
        home_profile = self._get_profile(home_team)
        away_profile = self._get_profile(away_team)

        expected_home = (home_profile.home_for_avg + away_profile.away_against_avg) / 2
        expected_away = (away_profile.away_for_avg + home_profile.home_against_avg) / 2
        return expected_home, expected_away

    def team_over_probs(self, home_team: str, away_team: str, lines: list[float] = None) -> dict:
        """OVER-ONLY probabilities for each team's shots on target.

        lines: if provided, used for BOTH sides (kept for backward
            compatibility / explicit-line testing). If None (the normal
            case), each side gets its OWN dynamically generated line
            list based on that side's expected shots on target - see
            generate_lines(). Home and away line lists may therefore
            differ in both range and length.
        """
        expected_home, expected_away = self.expected_shots_on_target(home_team, away_team)

        def over_probs_for(mean, line_list):
            variance = mean * self.overdispersion_factor
            n, p = _moments_to_nbinom_params(mean, variance)
            max_shots = 35
            probs = nbinom.pmf(np.arange(max_shots + 1), n, p)
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

    def match_total_over_probs(self, home_team: str, away_team: str, lines: list[float] = None) -> dict:
        """OVER-ONLY probabilities for combined match shots on target.

        Uses a convolution of the two teams' negative binomial
        distributions (computed via direct summation, which is fine at
        this scale) rather than assuming the sum is itself negative
        binomial, since that's only true under specific parameter
        constraints that won't generally hold here.

        lines: if provided, used directly (backward-compat / explicit
            testing). If None (the normal case), a dynamic range is
            generated from the combined expected total via
            generate_match_lines() - see that function's docstring for
            why match-level lines use a different floor (3.5) than
            team-level lines (1.5).
        """
        expected_home, expected_away = self.expected_shots_on_target(home_team, away_team)

        max_shots = 35
        home_var = expected_home * self.overdispersion_factor
        away_var = expected_away * self.overdispersion_factor
        n_h, p_h = _moments_to_nbinom_params(expected_home, home_var)
        n_a, p_a = _moments_to_nbinom_params(expected_away, away_var)

        home_probs = nbinom.pmf(np.arange(max_shots + 1), n_h, p_h)
        away_probs = nbinom.pmf(np.arange(max_shots + 1), n_a, p_a)

        max_total = 2 * max_shots
        total_probs = np.convolve(home_probs, away_probs)[: max_total + 1]

        lines = lines if lines is not None else generate_match_lines(expected_home + expected_away)

        result = {}
        for line in lines:
            threshold = int(line + 0.5)
            result[f"over_{line}"] = float(total_probs[threshold:].sum())
        return result
