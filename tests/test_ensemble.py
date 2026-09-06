"""
tests/test_ensemble.py

End-to-end tests for the ensemble predictor. Checks that:
- Blended probabilities land between the two source estimates (a basic
  sanity check on the weighted average math).
- The final output structure matches what the HTML generator will
  expect to consume (all required keys present).
- A misconfigured weights dict (not summing to 1) fails loudly at
  construction time rather than silently producing wrong numbers.
"""

import sys
import os
import numpy as np
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.prediction.dixon_coles import DixonColesModel, MatchInput
from engine.prediction.monte_carlo import MonteCarloSimulator
from engine.prediction.shots import ShotsOnTargetModel, TeamShotsProfile
from engine.prediction.ensemble import EnsemblePredictor


def generate_synthetic_league(n_teams=6, matches_per_pair=4, seed=42):
    np.random.seed(seed)
    teams = [f"Team{i}" for i in range(n_teams)]
    true_strength = {t: (n_teams - i) for i, t in enumerate(teams)}
    matches = []
    start = date(2024, 8, 1)
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
    return matches, teams


def build_ensemble():
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    dc_model = DixonColesModel()
    dc_model.fit(matches)
    mc_sim = MonteCarloSimulator(dc_model, n_iterations=50_000, seed=3)

    shots_model = ShotsOnTargetModel()
    shots_model.fit([
        TeamShotsProfile(t, home_for_avg=5.0, home_against_avg=4.0,
                          away_for_avg=4.0, away_against_avg=5.0)
        for t in teams
    ])

    ensemble = EnsemblePredictor(dc_model, mc_sim, shots_model)
    return ensemble, teams


def test_blended_probability_lies_between_sources():
    ensemble, teams = build_ensemble()
    dc_outcome = ensemble.dc_model.match_outcome_probs(teams[0], teams[1])
    mc_outcome = ensemble.mc_simulator.match_outcome_probs(teams[0], teams[1])
    prediction = ensemble.predict(teams[0], teams[1])

    for key in ("home_win", "draw", "away_win"):
        lo = min(dc_outcome[key], mc_outcome[key])
        hi = max(dc_outcome[key], mc_outcome[key])
        blended = prediction.match_winner[key]
        assert lo - 1e-9 <= blended <= hi + 1e-9, (
            f"Blended {key}={blended} not between dc={dc_outcome[key]} and mc={mc_outcome[key]}"
        )
    print(f"PASS: test_blended_probability_lies_between_sources")


def test_prediction_has_all_required_fields():
    ensemble, teams = build_ensemble()
    prediction = ensemble.predict(teams[0], teams[1])
    d = prediction.to_dict()

    required_top_level = {
        "home_team", "away_team", "match_winner", "double_chance",
        "match_total_goals", "team_total_goals", "team_shots_on_target",
        "match_shots_on_target",
    }
    assert required_top_level.issubset(d.keys()), f"Missing fields: {required_top_level - d.keys()}"

    assert set(d["match_winner"].keys()) == {"home_win", "draw", "away_win"}
    assert set(d["double_chance"].keys()) == {"1X", "X2"}
    assert "home" in d["team_total_goals"] and "away" in d["team_total_goals"]
    assert "home" in d["team_shots_on_target"] and "away" in d["team_shots_on_target"]
    print("PASS: test_prediction_has_all_required_fields")


def test_no_under_leaks_through_ensemble():
    ensemble, teams = build_ensemble()
    prediction = ensemble.predict(teams[0], teams[1])
    d = prediction.to_dict()

    def check_no_under(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert "under" not in str(k).lower(), f"FORBIDDEN under-like key at {path}.{k}"
                check_no_under(v, f"{path}.{k}")

    check_no_under(d)
    print("PASS: test_no_under_leaks_through_ensemble")


def test_misconfigured_weights_raises():
    ensemble, teams = build_ensemble()
    try:
        EnsemblePredictor(
            ensemble.dc_model, ensemble.mc_simulator, ensemble.shots_model,
            weights={"dixon_coles": 0.5, "monte_carlo": 0.6},  # sums to 1.1
        )
        assert False, "Expected ValueError for weights not summing to 1"
    except ValueError as e:
        assert "sum to 1.0" in str(e)
    print("PASS: test_misconfigured_weights_raises")


def test_probabilities_all_in_valid_range():
    ensemble, teams = build_ensemble()
    prediction = ensemble.predict(teams[0], teams[1])
    d = prediction.to_dict()

    def check_range(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                check_range(v, f"{path}.{k}")
        elif isinstance(obj, (int, float)):
            assert 0.0 <= obj <= 1.0, f"Out of range probability at {path}: {obj}"

    check_range(d["match_winner"], "match_winner")
    check_range(d["double_chance"], "double_chance")
    check_range(d["match_total_goals"], "match_total_goals")
    print("PASS: test_probabilities_all_in_valid_range")


if __name__ == "__main__":
    test_blended_probability_lies_between_sources()
    test_prediction_has_all_required_fields()
    test_no_under_leaks_through_ensemble()
    test_misconfigured_weights_raises()
    test_probabilities_all_in_valid_range()
    print("\nAll tests passed.")
