"""
tests/test_match_outcome_calibration.py

Tests for _run_match_outcome_calibration_check in engine/backtest/simulator.py.

This check was added after a real backtest run showed a puzzling
pattern: every betting selection (home_win, draw, away_win, over_2.5)
showed a positive average "edge" (model probability minus
bookmaker-implied probability) yet the backtest still lost money
overall. That combination - consistently believing you have an edge,
consistently losing - is a signature of model overconfidence, but the
betting-layer numbers alone can't distinguish "the model's
probabilities are wrong" from "the staking/threshold logic is the
problem," since both run through the same Kelly-staked bets. This
check isolates the model by comparing its RAW predictions to actual
outcomes with no odds or staking involved at all.
"""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from engine.prediction.dixon_coles import MatchInput
from engine.backtest.simulator import _run_match_outcome_calibration_check
import engine.backtest.simulator as simulator_module


def _generate_fake_league_history(n_teams=8, matches_per_pair=6, seed=21):
    np.random.seed(seed)
    teams = [f"Team{i}" for i in range(n_teams)]
    true_strength = {t: (n_teams - i) for i, t in enumerate(teams)}
    matches = []
    start = date(2023, 8, 1)
    day_counter = 0
    for _ in range(matches_per_pair):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                home, away = teams[i], teams[j]
                lam = max(0.8 + 0.25 * true_strength[home] - 0.15 * true_strength[away], 0.1)
                mu = max(0.8 + 0.25 * true_strength[away] - 0.15 * true_strength[home], 0.1)
                hg = int(np.random.poisson(lam))
                ag = int(np.random.poisson(mu))
                matches.append(MatchInput(home, away, hg, ag, start + timedelta(days=day_counter)))
                day_counter += 1
    return matches


def test_calibration_returns_all_three_outcomes():
    matches = _generate_fake_league_history()
    result = _run_match_outcome_calibration_check(matches, min_training_matches=40)

    assert set(result.keys()) == {"home_win", "draw", "away_win"}
    for outcome, (predicted, actual, n) in result.items():
        assert n > 0, f"Expected some predictions for {outcome}, got 0"
    print(f"PASS: test_calibration_returns_all_three_outcomes ({result})")


def test_calibration_produces_reasonable_predictions_with_enough_data():
    matches = _generate_fake_league_history()
    result = _run_match_outcome_calibration_check(matches, min_training_matches=40)

    for outcome, (predicted, actual, n) in result.items():
        assert predicted is not None and actual is not None
        assert 0.0 <= predicted <= 1.0
        assert 0.0 <= actual <= 1.0
        diff = abs(predicted - actual)
        assert diff < 0.15, (
            f"{outcome}: predicted={predicted:.3f} vs actual={actual:.3f} - "
            f"large divergence suggests a broken calibration mechanism, not just model imperfection"
        )
    print(f"PASS: test_calibration_produces_reasonable_predictions_with_enough_data ({result})")


def test_calibration_detects_injected_overconfidence():
    """The core thing this check needs to be able to detect: if a model
    is systematically overconfident, the calibration gap should be
    positive and visible. Verified by monkey-patching
    DixonColesModel.match_outcome_probs to inflate home_win probability
    while keeping actual outcomes unchanged.
    """
    matches = _generate_fake_league_history()

    original_method = simulator_module.DixonColesModel.match_outcome_probs

    def inflated_method(self, home_team, away_team):
        probs = original_method(self, home_team, away_team)
        boost = min(0.2, 1.0 - probs["home_win"])
        remaining = probs["draw"] + probs["away_win"]
        if remaining > 0:
            scale = (remaining - boost) / remaining
            probs["draw"] *= scale
            probs["away_win"] *= scale
        probs["home_win"] += boost
        return probs

    simulator_module.DixonColesModel.match_outcome_probs = inflated_method
    try:
        result = _run_match_outcome_calibration_check(matches, min_training_matches=40)
    finally:
        simulator_module.DixonColesModel.match_outcome_probs = original_method

    predicted, actual, n = result["home_win"]
    gap = predicted - actual
    assert gap > 0.05, (
        f"Expected the injected overconfidence to show up as a clear positive "
        f"gap for home_win, got predicted={predicted:.3f} actual={actual:.3f} gap={gap:+.3f}"
    )
    print(f"PASS: test_calibration_detects_injected_overconfidence (gap={gap:+.1%})")


def test_calibration_handles_no_lookahead():
    matches = _generate_fake_league_history()
    sorted_matches = sorted(matches, key=lambda m: m.match_date)

    fit_calls = []
    original_fit = simulator_module.DixonColesModel.fit

    def instrumented_fit(self, training_matches, as_of=None):
        fit_calls.append(max(m.match_date for m in training_matches))
        return original_fit(self, training_matches, as_of=as_of)

    simulator_module.DixonColesModel.fit = instrumented_fit
    try:
        _run_match_outcome_calibration_check(matches, min_training_matches=40, refit_every_n_matches=5)
    finally:
        simulator_module.DixonColesModel.fit = original_fit

    assert len(fit_calls) > 0, "Expected at least one training call to be logged"
    assert max(fit_calls) < sorted_matches[-1].match_date, (
        "Training data included a date at or after the final match - possible lookahead"
    )
    print(f"PASS: test_calibration_handles_no_lookahead ({len(fit_calls)} training calls checked)")


def test_calibration_skips_matches_with_unknown_teams_gracefully():
    matches = _generate_fake_league_history(n_teams=6, matches_per_pair=4)
    sorted_matches = sorted(matches, key=lambda m: m.match_date)
    injected = MatchInput("BrandNewTeam", sorted_matches[0].home_team, 1, 1, sorted_matches[-1].match_date)
    matches_with_unknown = matches + [injected]

    result = _run_match_outcome_calibration_check(matches_with_unknown, min_training_matches=40)
    assert result["home_win"][2] > 0
    print("PASS: test_calibration_skips_matches_with_unknown_teams_gracefully")


if __name__ == "__main__":
    test_calibration_returns_all_three_outcomes()
    test_calibration_produces_reasonable_predictions_with_enough_data()
    test_calibration_detects_injected_overconfidence()
    test_calibration_handles_no_lookahead()
    test_calibration_skips_matches_with_unknown_teams_gracefully()
    print("\nAll tests passed.")
