"""
tests/test_corners.py

Tests for the corners model. Structurally mirrors test_shots.py since
corners.py is built on the same pattern. Key checks: probabilities are
valid and monotonically decreasing, unknown teams fall back safely, and
the historical-data aggregation helper correctly skips rows with
missing corners data rather than treating them as zero.
"""

import sys
import os
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.prediction.corners import (
    CornersModel, TeamCornersProfile, build_corners_profiles_from_historical,
    generate_lines,
)
import config


def make_test_model():
    model = CornersModel()
    profiles = [
        TeamCornersProfile("StrongTeam", home_for_avg=7.5, home_against_avg=3.0,
                            away_for_avg=6.0, away_against_avg=3.5),
        TeamCornersProfile("WeakTeam", home_for_avg=4.0, home_against_avg=6.0,
                            away_for_avg=3.0, away_against_avg=7.0),
    ]
    model.fit(profiles)
    return model


def test_generate_lines_starts_at_min_and_steps_correctly():
    lines = generate_lines(expected_value=5.0, min_line=1.5, step=1.0)
    assert lines[0] == 1.5, f"Expected to start at 1.5, got {lines[0]}"
    for a, b in zip(lines, lines[1:]):
        assert abs((b - a) - 1.0) < 1e-9, f"Expected step of 1.0 between {a} and {b}"
    print(f"PASS: test_generate_lines_starts_at_min_and_steps_correctly ({lines})")


def test_generate_lines_extends_further_for_stronger_expected_value():
    """Regression test for the core spec requirement: weak teams get a
    short line range, strong teams get a longer one extending well past
    the old fixed ceiling of 6.5.
    """
    weak_lines = generate_lines(expected_value=3.5)
    strong_lines = generate_lines(expected_value=7.5)

    assert max(weak_lines) < max(strong_lines), (
        f"Strong team's line range should extend further: weak={weak_lines}, strong={strong_lines}"
    )
    # Per the spec's own worked example: weak team avg 3.5 -> lines up
    # to 6.5; strong team avg 7.5 -> lines up to 10.5.
    assert max(weak_lines) == 6.5, f"Expected weak team's max line to be 6.5, got {max(weak_lines)}"
    assert max(strong_lines) == 10.5, f"Expected strong team's max line to be 10.5, got {max(strong_lines)}"
    print(f"PASS: test_generate_lines_extends_further_for_stronger_expected_value "
          f"(weak={weak_lines}, strong={strong_lines})")


def test_generate_lines_always_includes_min_line_even_for_very_weak_teams():
    lines = generate_lines(expected_value=0.5)
    assert lines[0] == config.CORNERS_MIN_LINE
    print(f"PASS: test_generate_lines_always_includes_min_line_even_for_very_weak_teams ({lines})")


def test_team_over_probs_uses_dynamic_lines_per_side():
    """The core behavioral change: home and away sides now get their OWN
    line ranges based on their own expected corners, rather than a
    single shared fixed list - so a strong home team and a weak away
    team in the same match can have entirely different line ranges.
    """
    model = make_test_model()
    result = model.team_over_probs("StrongTeam", "WeakTeam")

    home_keys = sorted(result["home"].keys())
    away_keys = sorted(result["away"].keys())

    assert home_keys != away_keys, (
        f"Expected different dynamic line ranges for the strong home team "
        f"vs weak away team, got identical keys: {home_keys}"
    )
    # Home (StrongTeam) should have MORE / higher lines than away (WeakTeam)
    assert max(float(k.split("_")[1]) for k in home_keys) > max(float(k.split("_")[1]) for k in away_keys)
    print(f"PASS: test_team_over_probs_uses_dynamic_lines_per_side "
          f"(home_keys={home_keys}, away_keys={away_keys})")


def test_team_over_probs_monotonically_decrease():
    model = make_test_model()
    result = model.team_over_probs("StrongTeam", "WeakTeam")
    for side in ("home", "away"):
        lines_used = sorted(float(k.split("_")[1]) for k in result[side].keys())
        values = [result[side][f"over_{line}"] for line in lines_used]
        for a, b in zip(values, values[1:]):
            assert a >= b, f"{side} over probs should decrease: {result[side]}"
    print(f"PASS: test_team_over_probs_monotonically_decrease ({result})")


def test_strong_team_has_higher_corners_expectation():
    model = make_test_model()
    expected_home, expected_away = model.expected_corners("StrongTeam", "WeakTeam")
    assert expected_home > expected_away, (
        f"Strong team at home should have higher expected corners: {expected_home} vs {expected_away}"
    )
    print(f"PASS: test_strong_team_has_higher_corners_expectation "
          f"(home={expected_home:.2f}, away={expected_away:.2f})")


def test_unknown_team_falls_back_to_league_average():
    model = make_test_model()
    expected_home, expected_away = model.expected_corners("MysteryTeam", "WeakTeam")
    assert expected_home > 0 and expected_away > 0
    print(f"PASS: test_unknown_team_falls_back_to_league_average "
          f"(home={expected_home:.2f}, away={expected_away:.2f})")


def test_probabilities_are_valid_range():
    model = make_test_model()
    result = model.team_over_probs("StrongTeam", "WeakTeam")
    for side in ("home", "away"):
        for v in result[side].values():
            assert 0.0 <= v <= 1.0, f"Probability out of range: {v}"
    print("PASS: test_probabilities_are_valid_range")


@dataclass
class FakeHistoricalRow:
    """Minimal stand-in for HistoricalMatchOdds, exposing only the
    attributes build_corners_profiles_from_historical() actually reads -
    confirms the function works with any duck-typed object, not just
    the real class from football_data_co_uk_source.py.
    """
    home_team: str
    away_team: str
    home_corners: float
    away_corners: float


def test_build_profiles_from_historical_computes_correct_averages():
    rows = [
        FakeHistoricalRow("Arsenal", "Chelsea", home_corners=8, away_corners=3),
        FakeHistoricalRow("Arsenal", "Liverpool", home_corners=6, away_corners=5),
        FakeHistoricalRow("Chelsea", "Arsenal", home_corners=4, away_corners=7),
    ]
    profiles = build_corners_profiles_from_historical(rows)
    profiles_by_team = {p.team: p for p in profiles}

    arsenal = profiles_by_team["Arsenal"]
    # Arsenal's home_for_avg: average of home_corners in matches where
    # Arsenal was home (rows 0 and 1): (8+6)/2 = 7.0
    assert abs(arsenal.home_for_avg - 7.0) < 1e-9, f"Expected 7.0, got {arsenal.home_for_avg}"
    # Arsenal's away_for_avg: away_corners in matches where Arsenal was away (row 2): 7.0
    assert abs(arsenal.away_for_avg - 7.0) < 1e-9, f"Expected 7.0, got {arsenal.away_for_avg}"

    print(f"PASS: test_build_profiles_from_historical_computes_correct_averages ({arsenal})")


def test_build_profiles_skips_rows_with_missing_corners():
    """A row with corners=None should be excluded from averages, not
    treated as a real zero (which would silently drag the average down).

    Uses enough matches that Arsenal has complete home AND away data
    even after the missing row is dropped - with too few matches, a
    team can look "incomplete" (e.g. no away-side data at all) for a
    different, correct reason (see the None-completeness check in
    build_corners_profiles_from_historical), which would mask whether
    the missing-row skip itself is working. This test isolates that one
    behavior specifically.
    """
    rows = [
        FakeHistoricalRow("Arsenal", "Chelsea", home_corners=8, away_corners=3),
        FakeHistoricalRow("Arsenal", "Liverpool", home_corners=None, away_corners=None),  # missing data
        FakeHistoricalRow("Chelsea", "Arsenal", home_corners=4, away_corners=6),
        FakeHistoricalRow("Liverpool", "Arsenal", home_corners=5, away_corners=7),
    ]
    profiles = build_corners_profiles_from_historical(rows)
    profiles_by_team = {p.team: p for p in profiles}

    assert "Arsenal" in profiles_by_team, (
        f"Arsenal should have a complete profile from the 3 valid rows, "
        f"got teams: {list(profiles_by_team.keys())}"
    )
    arsenal = profiles_by_team["Arsenal"]
    # Only the first row should count for home_for_avg - not (8+0)/2=4.0,
    # which is what you'd get if the None row were wrongly treated as 0.
    assert abs(arsenal.home_for_avg - 8.0) < 1e-9, (
        f"Expected 8.0 (missing row excluded), got {arsenal.home_for_avg} "
        f"(would be 4.0 if None were wrongly treated as 0)"
    )
    print("PASS: test_build_profiles_skips_rows_with_missing_corners")


def test_build_profiles_returns_empty_list_when_no_data():
    rows = [FakeHistoricalRow("Arsenal", "Chelsea", home_corners=None, away_corners=None)]
    profiles = build_corners_profiles_from_historical(rows)
    assert profiles == []
    print("PASS: test_build_profiles_returns_empty_list_when_no_data")


def test_build_profiles_excludes_teams_with_incomplete_home_away_split():
    """A team seen only as a home team (never away) has no away-side
    data at all, not just missing individual rows. Its profile is
    incomplete (can't compute away_for_avg/away_against_avg), and it
    should be excluded entirely rather than shipped with a fabricated
    or None value in place of real away-side data - this is the same
    completeness discipline used in engine/main.py's shots aggregation.
    """
    rows = [
        FakeHistoricalRow("Arsenal", "Chelsea", home_corners=8, away_corners=3),
    ]
    profiles = build_corners_profiles_from_historical(rows)
    profiles_by_team = {p.team: p for p in profiles}
    assert "Arsenal" not in profiles_by_team, (
        "Arsenal has no away-side data (only ever home in this dataset) - "
        "should be excluded as an incomplete profile"
    )
    assert "Chelsea" not in profiles_by_team, (
        "Chelsea has no home-side data (only ever away in this dataset) - "
        "should be excluded as an incomplete profile"
    )
    print("PASS: test_build_profiles_excludes_teams_with_incomplete_home_away_split")


if __name__ == "__main__":
    test_generate_lines_starts_at_min_and_steps_correctly()
    test_generate_lines_extends_further_for_stronger_expected_value()
    test_generate_lines_always_includes_min_line_even_for_very_weak_teams()
    test_team_over_probs_uses_dynamic_lines_per_side()
    test_team_over_probs_monotonically_decrease()
    test_strong_team_has_higher_corners_expectation()
    test_unknown_team_falls_back_to_league_average()
    test_probabilities_are_valid_range()
    test_build_profiles_from_historical_computes_correct_averages()
    test_build_profiles_skips_rows_with_missing_corners()
    test_build_profiles_returns_empty_list_when_no_data()
    test_build_profiles_excludes_teams_with_incomplete_home_away_split()
    print("\nAll tests passed.")
