"""
tests/test_streak_analyzer.py

Tests for engine/streaks/analyzer.py. Given the complexity of the
"for"/"against" and home/away logic, these tests use hand-constructed
matches with known values so every count can be verified by hand,
rather than relying on random synthetic data.
"""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sources.football_data_co_uk_source import HistoricalMatchOdds
from engine.streaks.analyzer import (
    StreakAnalyzer, get_stat_value, get_opponent_stat_value,
    _strength_label, _mismatch_strength_label,
)
import config


def make_match(d, home, away, hg=1, ag=1, hst=4, ast=3, hc=5, ac=4):
    return HistoricalMatchOdds(
        date=d, home_team=home, away_team=away,
        home_goals=hg, away_goals=ag,
        home_shots_on_target=hst, away_shots_on_target=ast,
        home_corners=hc, away_corners=ac,
    )


def test_get_stat_value_picks_correct_side():
    m = make_match(date(2024, 1, 1), "Arsenal", "Chelsea", hst=6, ast=2)
    assert get_stat_value(m, "Arsenal", "shots_on_target") == 6
    assert get_stat_value(m, "Chelsea", "shots_on_target") == 2
    assert get_stat_value(m, "Liverpool", "shots_on_target") is None  # didn't play
    print("PASS: test_get_stat_value_picks_correct_side")


def test_get_stat_value_goals_conceded_is_opponents_goals():
    m = make_match(date(2024, 1, 1), "Arsenal", "Chelsea", hg=3, ag=1)
    # Arsenal's goals_conceded = Chelsea's goals = 1
    assert get_stat_value(m, "Arsenal", "goals_conceded") == 1
    # Chelsea's goals_conceded = Arsenal's goals = 3
    assert get_stat_value(m, "Chelsea", "goals_conceded") == 3
    print("PASS: test_get_stat_value_goals_conceded_is_opponents_goals")


def test_get_opponent_stat_value_is_the_mirror():
    m = make_match(date(2024, 1, 1), "Arsenal", "Chelsea", hst=6, ast=2)
    # What Arsenal ALLOWED = Chelsea's own stat
    assert get_opponent_stat_value(m, "Arsenal", "shots_on_target") == 2
    # What Chelsea ALLOWED = Arsenal's own stat
    assert get_opponent_stat_value(m, "Chelsea", "shots_on_target") == 6
    print("PASS: test_get_opponent_stat_value_is_the_mirror")


def test_strength_label_thresholds():
    assert _strength_label(0.85) == "Strong"
    assert _strength_label(0.80) == "Strong"
    assert _strength_label(0.70) == "Solid"
    assert _strength_label(0.65) == "Solid"
    assert _strength_label(0.55) == "Watch"
    assert _strength_label(0.50) == "Watch"
    assert _strength_label(0.30) == "Ignore"
    print("PASS: test_strength_label_thresholds")


def test_mismatch_strength_label_factors_in_window():
    # Same 85% combined, different window sizes -> different labels
    assert _mismatch_strength_label(0.85, window=8) == "Strong"
    assert _mismatch_strength_label(0.85, window=5) == "Solid"  # short window downgrades
    assert _mismatch_strength_label(0.70, window=10) == "Solid"
    assert _mismatch_strength_label(0.70, window=6) == "Watch"
    assert _mismatch_strength_label(0.40, window=18) == "Ignore"
    print("PASS: test_mismatch_strength_label_factors_in_window")


def test_get_streak_counts_correctly_for_known_data():
    """Hand-constructed: Arsenal's last 5 home matches, SOT values
    [6, 2, 5, 7, 1] -> threshold 5+ should match 3 of 5 (6, 5, 7).
    """
    matches = []
    sot_values = [6, 2, 5, 7, 1]
    start = date(2024, 1, 1)
    for i, sot in enumerate(sot_values):
        matches.append(make_match(start + timedelta(days=i * 7), "Arsenal", f"Opp{i}", hst=sot))

    analyzer = StreakAnalyzer(matches)
    as_of = start + timedelta(days=100)
    result = analyzer.get_streak("Arsenal", "shots_on_target", 5, window=5, direction="for", as_of=as_of, filter_type="home_only")

    assert result.count == 3, f"Expected 3 matches meeting threshold, got {result.count}"
    assert result.total == 5
    assert abs(result.percentage - 0.6) < 1e-9
    print(f"PASS: test_get_streak_counts_correctly_for_known_data ({result.describe()})")


def test_get_streak_excludes_missing_data_from_total():
    """A match with home_shots_on_target=None should be excluded from
    both count and total, not treated as 0 (which would wrongly count
    against the streak) or skipped silently without affecting total
    (which would wrongly inflate the denominator's implied reliability).
    """
    matches = [
        make_match(date(2024, 1, 1), "Arsenal", "OppA", hst=6),
        make_match(date(2024, 1, 8), "Arsenal", "OppB", hst=None),
        make_match(date(2024, 1, 15), "Arsenal", "OppC", hst=7),
    ]
    analyzer = StreakAnalyzer(matches)
    as_of = date(2024, 2, 1)
    result = analyzer.get_streak("Arsenal", "shots_on_target", 5, window=5, direction="for", as_of=as_of, filter_type="home_only")

    assert result.total == 2, f"Expected the None-stat match excluded from total, got total={result.total}"
    assert result.count == 2
    print("PASS: test_get_streak_excludes_missing_data_from_total")


def test_get_streak_respects_no_lookahead():
    """A match on or after as_of must never be included."""
    matches = [
        make_match(date(2024, 1, 1), "Arsenal", "OppA", hst=6),
        make_match(date(2024, 6, 1), "Arsenal", "OppB", hst=1),  # after as_of
    ]
    analyzer = StreakAnalyzer(matches)
    as_of = date(2024, 2, 1)
    result = analyzer.get_streak("Arsenal", "shots_on_target", 5, window=5, direction="for", as_of=as_of, filter_type="home_only")

    assert result.total == 1, f"Expected only the pre-as_of match counted, got total={result.total}"
    print("PASS: test_get_streak_respects_no_lookahead")


def test_get_streak_home_only_filter_excludes_away_matches():
    matches = [
        make_match(date(2024, 1, 1), "Arsenal", "OppA", hst=6),  # home
        make_match(date(2024, 1, 8), "OppB", "Arsenal", hst=3, ast=6),  # away for Arsenal
    ]
    analyzer = StreakAnalyzer(matches)
    as_of = date(2024, 2, 1)
    result = analyzer.get_streak("Arsenal", "shots_on_target", 5, window=5, direction="for", as_of=as_of, filter_type="home_only")

    assert result.total == 1, f"Expected only the home match counted, got total={result.total}"
    print("PASS: test_get_streak_home_only_filter_excludes_away_matches")


def test_get_h2h_streak_only_counts_matches_between_the_two_teams():
    matches = [
        make_match(date(2024, 1, 1), "Arsenal", "Chelsea", hst=6),  # H2H
        make_match(date(2024, 1, 8), "Arsenal", "Liverpool", hst=7),  # NOT H2H
        make_match(date(2024, 1, 15), "Chelsea", "Arsenal", hst=3, ast=5),  # H2H (reversed)
    ]
    analyzer = StreakAnalyzer(matches)
    as_of = date(2024, 2, 1)
    result = analyzer.get_h2h_streak("Arsenal", "Chelsea", "shots_on_target", 5, window=10, as_of=as_of, direction="for")

    assert result.total == 2, f"Expected only the 2 H2H matches, got total={result.total}"
    # Match 1: Arsenal home, SOT=6 -> meets threshold
    # Match 3: Arsenal away, SOT=5 -> meets threshold
    assert result.count == 2
    print("PASS: test_get_h2h_streak_only_counts_matches_between_the_two_teams")


def test_find_mismatches_requires_minimum_window_size():
    """With too few matches (below MISMATCH_MIN_WINDOW), no mismatch
    should ever be produced even if percentages look extreme, since a
    small sample isn't reliable evidence.
    """
    matches = [
        make_match(date(2024, 1, 1), "Arsenal", "Chelsea", hst=6, ast=6),
        make_match(date(2024, 1, 8), "Arsenal", "Everton", hst=6, ast=1),
    ]
    analyzer = StreakAnalyzer(matches)
    as_of = date(2024, 6, 1)
    mismatches = analyzer.find_mismatches("Arsenal", "Chelsea", as_of)

    # Only 2 matches total exist - nowhere near MISMATCH_MIN_WINDOW (5) -
    # so no mismatch should be found regardless of how extreme the stats look.
    assert len(mismatches) == 0, f"Expected no mismatches with insufficient data, got {len(mismatches)}"
    print("PASS: test_find_mismatches_requires_minimum_window_size")


def test_find_mismatches_detects_a_clear_double_streak():
    """Construct a scenario with a deliberate, clear double-streak
    mismatch: TeamA scores 5+ SOT at home consistently, TeamB allows
    5+ SOT away consistently - confirm find_mismatches() surfaces it.
    """
    matches = []
    start = date(2023, 8, 1)
    day = 0
    # TeamA: 8 home matches, all with 6+ SOT (clears every threshold in config)
    for i in range(8):
        matches.append(make_match(start + timedelta(days=day), "TeamA", f"Filler{i}", hst=7))
        day += 7
    # TeamB: 8 away matches, allowing 6+ SOT every time (opponent's home SOT = 7)
    for i in range(8):
        matches.append(make_match(start + timedelta(days=day), f"Filler{i+10}", "TeamB", hst=7, ast=1))
        day += 7

    analyzer = StreakAnalyzer(matches)
    as_of = start + timedelta(days=day + 30)
    mismatches = analyzer.find_mismatches("TeamA", "TeamB", as_of)

    assert len(mismatches) > 0, "Expected at least one mismatch to be detected for this clear scenario"
    sot_mismatches = [m for m in mismatches if m.stat == "shots_on_target"]
    assert len(sot_mismatches) > 0, "Expected shots_on_target mismatches specifically"
    print(f"PASS: test_find_mismatches_detects_a_clear_double_streak ({len(mismatches)} total mismatches found)")


def test_find_mismatches_never_recommends_bets_or_stakes():
    """Guard against a future edit accidentally adding staking/Kelly
    logic to this module - per spec, this is decision support only.

    Checks for actual usage (an import, a function/class reference),
    not just the word "kelly" appearing anywhere - the module's own
    docstring legitimately mentions Kelly Criterion to explain that it
    is NOT used here, which a naive text search would misflag.
    """
    import inspect
    from engine.streaks import analyzer as analyzer_module

    source = inspect.getsource(analyzer_module)
    forbidden_patterns = [
        "import kelly", "from engine.backtest.kelly", "kelly_fraction(",
        "KellyResult", "stake_fraction", "stake_amount(",
    ]
    for pattern in forbidden_patterns:
        assert pattern.lower() not in source.lower(), f"FORBIDDEN: found {pattern!r} usage in streaks module"

    # Also confirm no public method/function on the analyzer computes
    # or returns anything stake-related.
    public_members = [name for name in dir(analyzer_module.StreakAnalyzer) if not name.startswith("_")]
    for name in public_members:
        assert "stake" not in name.lower() and "kelly" not in name.lower(), (
            f"FORBIDDEN: found staking-related method {name} on StreakAnalyzer"
        )
    print("PASS: test_find_mismatches_never_recommends_bets_or_stakes")


if __name__ == "__main__":
    test_get_stat_value_picks_correct_side()
    test_get_stat_value_goals_conceded_is_opponents_goals()
    test_get_opponent_stat_value_is_the_mirror()
    test_strength_label_thresholds()
    test_mismatch_strength_label_factors_in_window()
    test_get_streak_counts_correctly_for_known_data()
    test_get_streak_excludes_missing_data_from_total()
    test_get_streak_respects_no_lookahead()
    test_get_streak_home_only_filter_excludes_away_matches()
    test_get_h2h_streak_only_counts_matches_between_the_two_teams()
    test_find_mismatches_requires_minimum_window_size()
    test_find_mismatches_detects_a_clear_double_streak()
    test_find_mismatches_never_recommends_bets_or_stakes()
    print("\nAll tests passed.")
