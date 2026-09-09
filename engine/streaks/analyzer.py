"""
engine/streaks/analyzer.py

Core streak analysis engine for the double-streak mismatch dashboard.

DESIGN NOTE ON DATA SOURCE: this module is built against
engine.sources.football_data_co_uk_source.HistoricalMatchOdds (goals,
shots on target, corners, with home/away splits) rather than assuming
a specific fetch source. The spec that requested this feature named
football-data.co.uk as the data source, but that site has an extended
history of unreliability this session (see engine/backtest/simulator.py's
module docstring for the full diagnostic trail - confirmed genuinely
down via curl from two independent networks, not a transient blip).
The actively-used data source as of this writing is
engine.sources.xgabora_match_data_source.XgaboraMatchDataSource, which
produces the exact same HistoricalMatchOdds records. This module works
identically regardless of which of the two fetches the data, since
both produce the same record type - swapping the underlying source
(e.g. back to football-data.co.uk if it stabilizes) requires no changes
here.

CORE CONCEPTS:
- "for" streak: how often a team's OWN stat met a threshold (e.g.
  "Arsenal had 5+ shots on target in 7 of their last 8 home games").
- "against" streak: how often a team ALLOWED their opponent to meet a
  threshold (e.g. "Chelsea allowed 5+ shots on target in 6 of their
  last 8 away games" - this describes Chelsea's defensive vulnerability
  when they play away, not Chelsea's own attacking output).
- H2H streak: same "for"/"against" concept, but restricted to matches
  specifically between two named teams, not each team's matches against
  anyone.
- A "double-streak mismatch" is when Team A's "for" streak and Team B's
  "against" streak both clear a threshold for the SAME stat/threshold -
  i.e. both sides' recent histories point toward the same outcome.

This module deliberately does NOT recommend bets, compute stake sizes,
or apply Kelly Criterion - per the spec, this is a decision-support
surface for the user's own judgment, not an automated betting system.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

import config
from engine.sources.football_data_co_uk_source import HistoricalMatchOdds


StatName = Literal["shots_on_target", "corners", "goals", "goals_conceded"]
Direction = Literal["for", "against"]
FilterType = Literal["all", "home_only", "away_only"]


@dataclass
class StreakResult:
    team: str
    stat: str
    threshold: float
    window: int
    direction: str
    filter_type: str
    count: int
    total: int
    percentage: float
    strength_label: str
    matches: list = field(default_factory=list)
    opponent: Optional[str] = None

    def describe(self) -> str:
        stat_label = _STAT_DISPLAY_NAMES.get(self.stat, self.stat)
        direction_word = "allowed" if self.direction == "against" else ""
        filter_word = {
            "home_only": "home ", "away_only": "away ", "all": "",
        }[self.filter_type]
        opponent_clause = f" vs {self.opponent}" if self.opponent else ""

        parts = [f"{self.threshold:g}+ {stat_label}"]
        if direction_word:
            parts.append(direction_word)
        parts.append(f"in {self.count} of last {self.total} {filter_word}games{opponent_clause}")
        parts.append(f"({self.percentage:.1%})")
        return " ".join(parts)


@dataclass
class Mismatch:
    home_team: str
    away_team: str
    stat: str
    threshold: float
    window: int
    filter_type: str
    for_streak: StreakResult
    against_streak: StreakResult
    combined_percentage: float
    strength_label: str
    suggested_bet: str
    is_h2h: bool = False
    # Populated only for recent-form (is_h2h=False) mismatches, when a
    # head-to-head streak exists for the same for_team/stat/threshold -
    # see find_mismatches()'s "enrichment" step. Used to compute
    # `alignment` below, per the pivot spec's requirement to show
    # recent-form AND head-to-head together with a verdict on whether
    # they agree.
    h2h_streak: Optional[StreakResult] = None
    alignment: Optional[str] = None  # "Aligned" | "Mixed" | "Conflict" | None

    def describe(self) -> str:
        lines = [
            f"{self.for_streak.team}: {self.for_streak.describe()}",
            f"{self.against_streak.team} allowed: {self.against_streak.describe()}",
        ]
        if self.h2h_streak is not None:
            lines.append(f"H2H: {self.h2h_streak.describe()}")
            if self.alignment:
                lines.append(f"Alignment: {self.alignment}")
        lines.append(f"Combined: {self.combined_percentage:.1%} -> {self.strength_label}")
        return "\n".join(lines)


_STAT_DISPLAY_NAMES = {
    "shots_on_target": "SOT",
    "corners": "corners",
    "goals": "goals",
    "goals_conceded": "goals conceded",
}


def get_stat_value(match: HistoricalMatchOdds, team: str, stat: str) -> Optional[float]:
    """Extract a single stat value for `team` from `match`, correctly
    picking the home_ or away_ field depending on which side `team`
    played on. Returns None if the match doesn't involve `team` at all,
    or if the relevant stat is missing data (distinguishing "team
    didn't play this match" from "stat wasn't recorded" matters for
    correct filtering upstream, so both return None rather than 0).
    """
    if match.home_team == team:
        side = "home"
    elif match.away_team == team:
        side = "away"
    else:
        return None

    other_side = "away" if side == "home" else "home"

    if stat == "shots_on_target":
        return getattr(match, f"{side}_shots_on_target")
    elif stat == "corners":
        return getattr(match, f"{side}_corners")
    elif stat == "goals":
        return getattr(match, f"{side}_goals")
    elif stat == "goals_conceded":
        return getattr(match, f"{other_side}_goals")
    else:
        raise ValueError(f"Unknown stat: {stat!r}")


def get_opponent_stat_value(match: HistoricalMatchOdds, team: str, stat: str) -> Optional[float]:
    """The mirror of get_stat_value(): extract the OPPONENT's stat value
    in a match where `team` played - i.e. what `team` "allowed" that
    match. Used for "against" streaks.
    """
    if match.home_team == team:
        side = "away"
    elif match.away_team == team:
        side = "home"
    else:
        return None

    other_side = "away" if side == "home" else "home"

    if stat == "shots_on_target":
        return getattr(match, f"{side}_shots_on_target")
    elif stat == "corners":
        return getattr(match, f"{side}_corners")
    elif stat == "goals":
        return getattr(match, f"{side}_goals")
    elif stat == "goals_conceded":
        return getattr(match, f"{other_side}_goals")
    else:
        raise ValueError(f"Unknown stat: {stat!r}")


def _strength_label(percentage: float) -> str:
    """Per spec section 2.3's threshold table."""
    if percentage >= 0.80:
        return "Strong"
    elif percentage >= 0.65:
        return "Solid"
    elif percentage >= 0.50:
        return "Watch"
    else:
        return "Ignore"


def _mismatch_strength_label(combined_percentage: float, window: int) -> str:
    """Per spec section 3.3's scoring table - note this table is
    DIFFERENT from the single-streak table in _strength_label(): it
    also factors in window size (a given combined percentage rates
    lower confidence on a short window than a long one, since fewer
    matches means more sampling noise).
    """
    long_window = window >= 8
    if combined_percentage >= 0.80:
        return "Strong" if long_window else "Solid"
    elif combined_percentage >= 0.65:
        return "Solid" if long_window else "Watch"
    elif combined_percentage >= 0.50:
        return "Watch" if long_window else "Ignore"
    else:
        return "Ignore"


def _classify_alignment(recent_form_percentage: float, h2h_percentage: float) -> str:
    """Classify agreement between a recent-form streak percentage and
    the corresponding head-to-head streak percentage for the same
    stat/threshold, per the pivot spec's alignment rule:
    - Both clearly point the same way (both high, or both low) -> "Aligned"
    - They strongly contradict (one high, one low) -> "Conflict"
    - Anything in between (one moderate, mixed signal) -> "Mixed"

    The exact thresholds mirror the spec's own worked example (80% vs
    20% called "Conflict"; the spec's other example, 80% vs 60%,
    doesn't test the boundary between "Aligned" and "Mixed" directly,
    so this uses a straightforward "both clear the mismatch threshold
    in the same direction" rule for Aligned, a large gap for Conflict,
    and Mixed for everything in between - documented here explicitly
    since the spec described the concept but not exact numeric
    boundaries beyond its one worked example).
    """
    gap = abs(recent_form_percentage - h2h_percentage)
    both_high = recent_form_percentage >= config.MISMATCH_MIN_PERCENTAGE and h2h_percentage >= config.MISMATCH_MIN_PERCENTAGE
    both_low = recent_form_percentage < config.MISMATCH_MIN_PERCENTAGE and h2h_percentage < config.MISMATCH_MIN_PERCENTAGE

    if both_high or both_low:
        return "Aligned"
    if gap >= 0.5:
        return "Conflict"
    return "Mixed"


class StreakAnalyzer:
    """Analyzes historical matches to compute streaks and detect
    double-streak mismatches between two upcoming opponents.
    """

    def __init__(self, matches: list[HistoricalMatchOdds]):
        self.matches = matches

    def _team_matches_before(
        self, team: str, as_of: date, filter_type: str = "all",
    ) -> list[HistoricalMatchOdds]:
        """All of `team`'s matches strictly before `as_of`, sorted most
        recent first, optionally filtered to home-only or away-only.
        No-lookahead by construction: callers always pass the date of
        the upcoming fixture being analyzed, and this only considers
        matches before that date.
        """
        result = []
        for m in self.matches:
            if m.date is None or m.date >= as_of:
                continue
            if filter_type == "home_only" and m.home_team != team:
                continue
            if filter_type == "away_only" and m.away_team != team:
                continue
            if filter_type == "all" and team not in (m.home_team, m.away_team):
                continue
            result.append(m)
        result.sort(key=lambda m: m.date, reverse=True)
        return result

    def _h2h_matches_before(self, team_a: str, team_b: str, as_of: date) -> list[HistoricalMatchOdds]:
        """All matches strictly before `as_of` where team_a and team_b
        played each other, in either home/away configuration, sorted
        most recent first.
        """
        result = [
            m for m in self.matches
            if m.date is not None and m.date < as_of
            and {m.home_team, m.away_team} == {team_a, team_b}
        ]
        result.sort(key=lambda m: m.date, reverse=True)
        return result

    def get_streak(
        self, team: str, stat: str, threshold: float, window: int,
        direction: str, as_of: date, filter_type: str = "all",
    ) -> StreakResult:
        """Compute a single streak: how many of `team`'s last `window`
        matches (before `as_of`, optionally filtered by home/away) met
        `threshold` for `stat`, in the given `direction` ("for" = team's
        own stat, "against" = what the team allowed their opponent).

        Matches with missing stat data (None) are EXCLUDED from both
        the count and the total - they don't count as "met" or "not
        met," they're simply not usable evidence. This means `total`
        in the result can be less than `window` if some recent matches
        lack stat data - the result includes both `count` and `total`
        specifically so this is visible rather than silently treated
        as 0.
        """
        candidate_matches = self._team_matches_before(team, as_of, filter_type)[:window]

        count = 0
        total = 0
        used_dates = []
        for m in candidate_matches:
            value = (
                get_stat_value(m, team, stat) if direction == "for"
                else get_opponent_stat_value(m, team, stat)
            )
            if value is None:
                continue
            total += 1
            used_dates.append(m.date)
            if value >= threshold:
                count += 1

        percentage = count / total if total > 0 else 0.0
        return StreakResult(
            team=team, stat=stat, threshold=threshold, window=window,
            direction=direction, filter_type=filter_type,
            count=count, total=total, percentage=percentage,
            strength_label=_strength_label(percentage) if total > 0 else "Ignore",
            matches=used_dates,
        )

    def get_h2h_streak(
        self, team_a: str, team_b: str, stat: str, threshold: float,
        window: int, as_of: date, direction: str = "for",
    ) -> StreakResult:
        """Head-to-head version of get_streak(): only considers matches
        between team_a and team_b specifically, computing team_a's
        streak (direction="for": team_a's own stat; direction="against":
        what team_a allowed team_b in those H2H matches).
        """
        candidate_matches = self._h2h_matches_before(team_a, team_b, as_of)[:window]

        count = 0
        total = 0
        used_dates = []
        for m in candidate_matches:
            value = (
                get_stat_value(m, team_a, stat) if direction == "for"
                else get_opponent_stat_value(m, team_a, stat)
            )
            if value is None:
                continue
            total += 1
            used_dates.append(m.date)
            if value >= threshold:
                count += 1

        percentage = count / total if total > 0 else 0.0
        return StreakResult(
            team=team_a, stat=stat, threshold=threshold, window=window,
            direction=direction, filter_type="all",
            count=count, total=total, percentage=percentage,
            strength_label=_strength_label(percentage) if total > 0 else "Ignore",
            matches=used_dates, opponent=team_b,
        )

    def find_mismatches(self, home_team: str, away_team: str, as_of: date) -> list[Mismatch]:
        """Return all double-streak mismatches for the fixture
        home_team vs away_team, per spec section 3.2's four-step
        process: home "for" + away "against", away "for" + home
        "against", plus H2H mismatches in both directions.
        """
        mismatches = []

        for stat, thresholds in config.STREAK_THRESHOLDS.items():
            for threshold in thresholds:
                for window in config.STREAK_WINDOWS:
                    home_for = self.get_streak(home_team, stat, threshold, window, "for", as_of, "home_only")
                    away_against = self.get_streak(away_team, stat, threshold, window, "against", as_of, "away_only")
                    m = self._build_mismatch_if_qualifying(
                        home_team, away_team, stat, threshold, window, "home_only",
                        home_for, away_against, is_h2h=False,
                    )
                    if m:
                        self._enrich_with_h2h(m, as_of)
                        mismatches.append(m)

                    away_for = self.get_streak(away_team, stat, threshold, window, "for", as_of, "away_only")
                    home_against = self.get_streak(home_team, stat, threshold, window, "against", as_of, "home_only")
                    m = self._build_mismatch_if_qualifying(
                        away_team, home_team, stat, threshold, window, "away_only",
                        away_for, home_against, is_h2h=False,
                    )
                    if m:
                        self._enrich_with_h2h(m, as_of)
                        mismatches.append(m)

                for window in config.H2H_WINDOWS:
                    home_h2h_for = self.get_h2h_streak(home_team, away_team, stat, threshold, window, as_of, "for")
                    away_h2h_against = self.get_h2h_streak(away_team, home_team, stat, threshold, window, as_of, "against")
                    m = self._build_mismatch_if_qualifying(
                        home_team, away_team, stat, threshold, window, "all",
                        home_h2h_for, away_h2h_against, is_h2h=True,
                    )
                    if m:
                        mismatches.append(m)

                    away_h2h_for = self.get_h2h_streak(away_team, home_team, stat, threshold, window, as_of, "for")
                    home_h2h_against = self.get_h2h_streak(home_team, away_team, stat, threshold, window, as_of, "against")
                    m = self._build_mismatch_if_qualifying(
                        away_team, home_team, stat, threshold, window, "all",
                        away_h2h_for, home_h2h_against, is_h2h=True,
                    )
                    if m:
                        mismatches.append(m)

        return mismatches

    def _enrich_with_h2h(self, mismatch: Mismatch, as_of: date) -> None:
        """Mutate a recent-form Mismatch in place, attaching the
        corresponding head-to-head streak (same for_team/stat/threshold)
        and an alignment verdict, per the pivot spec's requirement to
        show recent-form and H2H together with an agreement flag.

        Uses the LARGEST configured H2H_WINDOWS value (most H2H history
        available) rather than every window size, since attaching one
        representative H2H figure per card is what the spec's sample
        output shows (one "Head-to-Head:" line per mismatch, not one
        per window size) - showing every H2H window on every recent-
        form card would be noisy repetition of information already
        available in the "All Streaks by Category" section below.

        Does nothing (leaves h2h_streak/alignment as None) if the H2H
        streak doesn't meet MISMATCH_MIN_WINDOW - a small H2H sample
        isn't meaningful evidence for or against alignment, and
        showing a false "Conflict" or "Aligned" verdict from 1-2
        H2H meetings would overstate the reliability of that signal.
        """
        h2h_window = max(config.H2H_WINDOWS)
        h2h = self.get_h2h_streak(
            mismatch.for_streak.team, mismatch.against_streak.team,
            mismatch.stat, mismatch.threshold, h2h_window, as_of, direction="for",
        )
        if h2h.total < config.MISMATCH_MIN_WINDOW:
            return

        mismatch.h2h_streak = h2h
        mismatch.alignment = _classify_alignment(mismatch.for_streak.percentage, h2h.percentage)

    def _build_mismatch_if_qualifying(
        self, for_team: str, against_team: str, stat: str, threshold: float,
        window: int, filter_type: str,
        for_streak: StreakResult, against_streak: StreakResult, is_h2h: bool,
    ) -> Optional[Mismatch]:
        """Apply the spec's qualification rules (section 3.2 step 3 and
        section 3.3's minimum sample size) and build a Mismatch if they
        pass, else return None.
        """
        if for_streak.total < config.MISMATCH_MIN_WINDOW or against_streak.total < config.MISMATCH_MIN_WINDOW:
            return None
        if for_streak.percentage < config.MISMATCH_MIN_PERCENTAGE:
            return None
        if against_streak.percentage < config.MISMATCH_MIN_PERCENTAGE:
            return None

        combined = (for_streak.percentage + against_streak.percentage) / 2
        strength = _mismatch_strength_label(combined, window)
        if strength == "Ignore":
            return None

        stat_label = _STAT_DISPLAY_NAMES.get(stat, stat)
        suggested_bet = f"{for_team} Total {stat_label} Over {threshold - 0.5:g}"

        return Mismatch(
            home_team=for_team if filter_type != "away_only" else against_team,
            away_team=against_team if filter_type != "away_only" else for_team,
            stat=stat, threshold=threshold, window=window, filter_type=filter_type,
            for_streak=for_streak, against_streak=against_streak,
            combined_percentage=combined, strength_label=strength,
            suggested_bet=suggested_bet, is_h2h=is_h2h,
        )
