"""
tests/test_monte_carlo.py

The critical test here: Monte Carlo simulation and the Dixon-Coles
closed-form probabilities are built on the same underlying rate
parameters, so with enough iterations they must converge to within a
small tolerance. If they diverge significantly, that indicates a real
bug in one of the two implementations - this test exists specifically
to catch that class of error before it reaches production.
"""

import sys
import os
import numpy as np
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.prediction.dixon_coles import DixonColesModel, MatchInput
from engine.prediction.monte_carlo import MonteCarloSimulator
import config


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


def _fitted_model():
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    model = DixonColesModel()
    model.fit(matches)
    return model, teams


def test_monte_carlo_converges_to_closed_form_outcomes():
    model, teams = _fitted_model()
    mc = MonteCarloSimulator(model, n_iterations=200_000, seed=7)

    closed_form = model.match_outcome_probs(teams[0], teams[1])
    simulated = mc.match_outcome_probs(teams[0], teams[1])

    for key in ("home_win", "draw", "away_win"):
        diff = abs(closed_form[key] - simulated[key])
        assert diff < 0.02, (
            f"Monte Carlo diverges from closed-form on {key}: "
            f"closed_form={closed_form[key]:.4f} simulated={simulated[key]:.4f} diff={diff:.4f}"
        )
    print(f"PASS: test_monte_carlo_converges_to_closed_form_outcomes "
          f"(closed_form={closed_form}, simulated={simulated})")


def test_monte_carlo_converges_on_double_chance():
    model, teams = _fitted_model()
    mc = MonteCarloSimulator(model, n_iterations=200_000, seed=7)

    closed_form = model.double_chance_probs(teams[0], teams[1])
    simulated = mc.double_chance_probs(teams[0], teams[1])

    for key in ("1X", "X2"):
        diff = abs(closed_form[key] - simulated[key])
        assert diff < 0.02, f"Diverges on {key}: {closed_form[key]:.4f} vs {simulated[key]:.4f}"
    print(f"PASS: test_monte_carlo_converges_on_double_chance ({simulated})")


def test_monte_carlo_converges_on_match_total_goals():
    model, teams = _fitted_model()
    mc = MonteCarloSimulator(model, n_iterations=200_000, seed=7)

    closed_form = model.match_total_goals_over_probs(teams[0], teams[1])
    simulated = mc.match_total_goals_over_probs(teams[0], teams[1])

    for line in config.GOAL_LINES:
        key = f"over_{line}"
        diff = abs(closed_form[key] - simulated[key])
        assert diff < 0.02, f"Diverges on {key}: {closed_form[key]:.4f} vs {simulated[key]:.4f}"
    print(f"PASS: test_monte_carlo_converges_on_match_total_goals")


def test_monte_carlo_converges_on_team_total_goals():
    model, teams = _fitted_model()
    mc = MonteCarloSimulator(model, n_iterations=200_000, seed=7)

    closed_form = model.team_total_goals_over_probs(teams[0], teams[1])
    simulated = mc.team_total_goals_over_probs(teams[0], teams[1])

    for side in ("home", "away"):
        for line in config.TEAM_GOAL_LINES:
            key = f"over_{line}"
            diff = abs(closed_form[side][key] - simulated[side][key])
            assert diff < 0.02, (
                f"Diverges on {side}/{key}: {closed_form[side][key]:.4f} vs {simulated[side][key]:.4f}"
            )
    print("PASS: test_monte_carlo_converges_on_team_total_goals")


def test_no_12_market_in_simulated_double_chance():
    model, teams = _fitted_model()
    mc = MonteCarloSimulator(model, n_iterations=1000, seed=7)
    dc = mc.double_chance_probs(teams[0], teams[1])
    assert set(dc.keys()) == {"1X", "X2"}, f"Should never include 12: {dc.keys()}"
    print("PASS: test_no_12_market_in_simulated_double_chance")


if __name__ == "__main__":
    test_monte_carlo_converges_to_closed_form_outcomes()
    test_monte_carlo_converges_on_double_chance()
    test_monte_carlo_converges_on_match_total_goals()
    test_monte_carlo_converges_on_team_total_goals()
    test_no_12_market_in_simulated_double_chance()
    print("\nAll tests passed.")
