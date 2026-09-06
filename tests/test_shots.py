"""
tests/test_shots.py

Tests for the shots-on-target model. The single most important property
tested here is that NO method or output ever contains an "under"
probability, anywhere - per the hard bookmaker constraint. Every other
test is secondary to that one.
"""

import sys
import os
import inspect
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.prediction.shots import (
    ShotsOnTargetModel, TeamShotsProfile, _moments_to_nbinom_params,
    generate_lines, generate_match_lines,
)
import config


def make_test_model():
    model = ShotsOnTargetModel()
    profiles = [
        TeamShotsProfile("StrongTeam", home_for_avg=7.0, home_against_avg=2.5,
                          away_for_avg=5.5, away_against_avg=3.0),
        TeamShotsProfile("WeakTeam", home_for_avg=3.5, home_against_avg=5.0,
                          away_for_avg=2.5, away_against_avg=6.0),
    ]
    model.fit(profiles)
    return model


def test_generate_lines_matches_spec_worked_examples():
    """Regression test locking in the exact spec examples: weak team
    (avg 3.5) -> lines up to 6.5; strong team (avg 7.5) -> lines up to
    10.5. Also guards against the round()-vs-floor() banker's-rounding
    discrepancy found during development (round(6.5) == 6, not 7,
    which would have silently produced a ceiling one line short of the
    spec's own example).
    """
    weak = generate_lines(3.5)
    strong = generate_lines(7.5)
    assert weak == [1.5, 2.5, 3.5, 4.5, 5.5, 6.5], f"Got {weak}"
    assert strong == [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5], f"Got {strong}"
    print(f"PASS: test_generate_lines_matches_spec_worked_examples (weak={weak}, strong={strong})")


def test_generate_match_lines_uses_higher_floor_than_team_lines():
    """Match-level lines start at 3.5 (not 1.5 like team-level), since
    two teams' combined shots almost always clear a low single-team
    threshold - a 1.5 floor at match level would be uninformative.
    """
    lines = generate_match_lines(expected_total=9.0)
    assert lines[0] == 3.5, f"Expected match lines to start at 3.5, got {lines[0]}"
    assert min(lines) >= config.SHOTS_MIN_LINE, "Match floor should be at or above team floor"
    print(f"PASS: test_generate_match_lines_uses_higher_floor_than_team_lines ({lines})")


def test_no_under_key_anywhere_in_team_probs():
    model = make_test_model()
    result = model.team_over_probs("StrongTeam", "WeakTeam")
    for side in ("home", "away"):
        for key in result[side]:
            assert "under" not in key.lower(), f"FORBIDDEN: found under-like key {key}"
            assert key.startswith("over_"), f"Unexpected key format: {key}"
    print(f"PASS: test_no_under_key_anywhere_in_team_probs ({result})")


def test_no_under_key_anywhere_in_match_probs():
    model = make_test_model()
    result = model.match_total_over_probs("StrongTeam", "WeakTeam")
    for key in result:
        assert "under" not in key.lower(), f"FORBIDDEN: found under-like key {key}"
        assert key.startswith("over_"), f"Unexpected key format: {key}"
    print(f"PASS: test_no_under_key_anywhere_in_match_probs ({result})")


def test_no_under_method_exists_on_class():
    """Guard against a future edit accidentally adding an under_probs
    method - fail loudly if one ever appears.
    """
    methods = [name for name, _ in inspect.getmembers(ShotsOnTargetModel, predicate=inspect.isfunction)]
    for name in methods:
        assert "under" not in name.lower(), f"FORBIDDEN: found method {name} on ShotsOnTargetModel"
    print("PASS: test_no_under_method_exists_on_class")


def test_team_over_probs_uses_dynamic_lines_per_side():
    """Home and away sides should get their OWN dynamically generated
    line ranges based on their own expected shots on target, not a
    single shared fixed list.
    """
    model = make_test_model()
    result = model.team_over_probs("StrongTeam", "WeakTeam")
    home_keys = sorted(result["home"].keys())
    away_keys = sorted(result["away"].keys())
    assert home_keys != away_keys, (
        f"Expected different dynamic ranges, got identical keys: {home_keys}"
    )
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
    print(f"PASS: test_team_over_probs_monotonically_decrease")


def test_match_total_over_probs_monotonically_decrease():
    model = make_test_model()
    result = model.match_total_over_probs("StrongTeam", "WeakTeam")
    lines_used = sorted(float(k.split("_")[1]) for k in result.keys())
    values = [result[f"over_{line}"] for line in lines_used]
    for a, b in zip(values, values[1:]):
        assert a >= b, f"Match total over probs should decrease: {result}"
    print(f"PASS: test_match_total_over_probs_monotonically_decrease ({result})")


def test_strong_team_has_higher_shots_expectation_than_weak_team():
    model = make_test_model()
    expected_home, expected_away = model.expected_shots_on_target("StrongTeam", "WeakTeam")
    assert expected_home > expected_away, (
        f"Strong team at home should have higher expected SOT: {expected_home} vs {expected_away}"
    )
    print(f"PASS: test_strong_team_has_higher_shots_expectation_than_weak_team "
          f"(home={expected_home:.2f}, away={expected_away:.2f})")


def test_moments_to_nbinom_recovers_mean_and_variance():
    mean, variance = 5.0, 6.5
    n, p = _moments_to_nbinom_params(mean, variance)
    # For NB(n, p): mean = n(1-p)/p, variance = n(1-p)/p^2
    recovered_mean = n * (1 - p) / p
    recovered_variance = n * (1 - p) / (p ** 2)
    assert abs(recovered_mean - mean) < 1e-6, f"Mean not recovered: {recovered_mean} vs {mean}"
    assert abs(recovered_variance - variance) < 1e-6, f"Variance not recovered: {recovered_variance} vs {variance}"
    print("PASS: test_moments_to_nbinom_recovers_mean_and_variance")


def test_unknown_team_falls_back_to_league_average():
    model = make_test_model()
    # "MysteryTeam" was never fit - should use league averages, not crash
    expected_home, expected_away = model.expected_shots_on_target("MysteryTeam", "WeakTeam")
    assert expected_home > 0 and expected_away > 0
    print(f"PASS: test_unknown_team_falls_back_to_league_average "
          f"(home={expected_home:.2f}, away={expected_away:.2f})")


def test_probabilities_are_valid_range():
    model = make_test_model()
    team_result = model.team_over_probs("StrongTeam", "WeakTeam")
    match_result = model.match_total_over_probs("StrongTeam", "WeakTeam")
    for side in ("home", "away"):
        for v in team_result[side].values():
            assert 0.0 <= v <= 1.0, f"Probability out of range: {v}"
    for v in match_result.values():
        assert 0.0 <= v <= 1.0, f"Probability out of range: {v}"
    print("PASS: test_probabilities_are_valid_range")


if __name__ == "__main__":
    test_no_under_key_anywhere_in_team_probs()
    test_no_under_key_anywhere_in_match_probs()
    test_no_under_method_exists_on_class()
    test_generate_lines_matches_spec_worked_examples()
    test_generate_match_lines_uses_higher_floor_than_team_lines()
    test_team_over_probs_uses_dynamic_lines_per_side()
    test_team_over_probs_monotonically_decrease()
    test_match_total_over_probs_monotonically_decrease()
    test_strong_team_has_higher_shots_expectation_than_weak_team()
    test_moments_to_nbinom_recovers_mean_and_variance()
    test_unknown_team_falls_back_to_league_average()
    test_probabilities_are_valid_range()
    print("\nAll tests passed.")
