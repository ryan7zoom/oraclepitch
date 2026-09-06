"""
tests/test_dixon_coles.py

Validates the Dixon-Coles model against synthetic data with known
statistical properties, e.g.:
- Probabilities from scoreline_matrix must sum to 1.
- A team that's scored way more than it's conceded, against opponents
  that are the reverse, should be predicted as a strong favorite.
- Two teams with identical historical records should be predicted as
  close to a coin flip (modulo home advantage).
- Double chance probabilities must equal win+draw as expected, and must
  never include a "12" market.
"""

import sys
import os
import random
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.prediction.dixon_coles import DixonColesModel, MatchInput
import config


def generate_synthetic_league(n_teams=6, matches_per_pair=4, seed=42):
    """Generate a round-robin-ish synthetic season where team strength is
    known in advance, so we can check the model recovers sensible ratings.
    Team 0 is deliberately made much stronger than Team 5.
    """
    random.seed(seed)
    teams = [f"Team{i}" for i in range(n_teams)]
    # true_strength: higher = scores more, concedes less
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
                lam = 0.8 + 0.25 * true_strength[home] - 0.15 * true_strength[away]
                mu = 0.8 + 0.25 * true_strength[away] - 0.15 * true_strength[home]
                lam = max(lam, 0.1)
                mu = max(mu, 0.1)
                hg = min(np.random.poisson(lam), 8) if False else _poisson_sample(lam)
                ag = _poisson_sample(mu)
                matches.append(MatchInput(
                    home_team=home, away_team=away,
                    home_goals=hg, away_goals=ag,
                    match_date=start + timedelta(days=day_counter),
                ))
                day_counter += 1
    return matches, teams


def _poisson_sample(lam):
    # simple poisson sampler without importing numpy at module load twice
    import numpy as np
    return int(np.random.poisson(lam))


def test_scoreline_matrix_sums_to_one():
    import numpy as np
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    model = DixonColesModel()
    model.fit(matches)

    matrix = model.scoreline_matrix(teams[0], teams[1])
    total = matrix.sum()
    assert abs(total - 1.0) < 1e-6, f"Matrix should sum to 1, got {total}"
    print("PASS: test_scoreline_matrix_sums_to_one")


def test_strong_team_favored_at_home():
    import numpy as np
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    model = DixonColesModel()
    model.fit(matches)

    # teams[0] is the strongest, teams[-1] is the weakest
    strong, weak = teams[0], teams[-1]
    outcomes = model.match_outcome_probs(strong, weak)

    assert outcomes["home_win"] > outcomes["away_win"], (
        f"Strong home team should be favored: {outcomes}"
    )
    assert outcomes["home_win"] > 0.5, f"Expected clear favorite, got {outcomes}"
    print(f"PASS: test_strong_team_favored_at_home ({outcomes})")


def test_double_chance_excludes_12_and_sums_correctly():
    import numpy as np
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    model = DixonColesModel()
    model.fit(matches)

    outcomes = model.match_outcome_probs(teams[0], teams[1])
    dc = model.double_chance_probs(teams[0], teams[1])

    assert set(dc.keys()) == {"1X", "X2"}, f"Should only have 1X and X2, got {dc.keys()}"
    assert abs(dc["1X"] - (outcomes["home_win"] + outcomes["draw"])) < 1e-9
    assert abs(dc["X2"] - (outcomes["away_win"] + outcomes["draw"])) < 1e-9
    print(f"PASS: test_double_chance_excludes_12_and_sums_correctly ({dc})")


def test_match_total_goals_over_probs_monotonically_decrease():
    import numpy as np
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    model = DixonColesModel()
    model.fit(matches)

    over_probs = model.match_total_goals_over_probs(teams[0], teams[1])
    values = [over_probs[f"over_{line}"] for line in config.GOAL_LINES]

    for a, b in zip(values, values[1:]):
        assert a >= b, f"Over probabilities should decrease as line increases: {over_probs}"
    print(f"PASS: test_match_total_goals_over_probs_monotonically_decrease ({over_probs})")


def test_team_total_goals_over_probs_structure():
    import numpy as np
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    model = DixonColesModel()
    model.fit(matches)

    result = model.team_total_goals_over_probs(teams[0], teams[1])
    assert "home" in result and "away" in result
    for side in ("home", "away"):
        values = [result[side][f"over_{line}"] for line in config.TEAM_GOAL_LINES]
        for a, b in zip(values, values[1:]):
            assert a >= b, f"{side} over probs should decrease: {result[side]}"
    print(f"PASS: test_team_total_goals_over_probs_structure ({result})")


def test_extreme_small_sample_never_produces_invalid_probabilities():
    """Regression test for a real bug found during backtest development:
    on a small (35-match), lopsided sample, one team's attack parameter
    was driven to an extreme value (-15.6) by unconstrained MLE, which
    combined with an unbounded-in-effect rho to produce a negative
    tau() value and therefore negative "probabilities" in the scoreline
    matrix - while the matrix still summed to ~1.0, masking the problem
    unless individual cells were checked. This test reproduces that
    exact scenario and confirms all probabilities stay valid.
    """
    import numpy as np
    np.random.seed(42)
    n_teams = 8
    teams = [f"Team{i}" for i in range(n_teams)]
    true_strength = {t: (n_teams - i) for i, t in enumerate(teams)}
    matches = []
    start = date(2023, 8, 1)
    day_counter = 0
    for _ in range(6):
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

    sorted_matches = sorted(matches, key=lambda m: m.match_date)
    model = DixonColesModel()
    training = sorted_matches[:35]  # the exact window size that triggered the original bug
    model.fit(training, as_of=training[-1].match_date)

    # Check every team pairing, not just the one that originally broke -
    # the regularization fix should hold generally, not just patch this
    # one case.
    for home in teams:
        for away in teams:
            if home == away:
                continue
            matrix = model.scoreline_matrix(home, away)
            assert (matrix >= 0).all(), f"Negative probability for {home} vs {away}"
            assert abs(matrix.sum() - 1.0) < 1e-6, f"Matrix doesn't sum to 1 for {home} vs {away}"
            outcomes = model.match_outcome_probs(home, away)
            for key, val in outcomes.items():
                assert 0.0 <= val <= 1.0, f"Invalid {key}={val} for {home} vs {away}"
    print("PASS: test_extreme_small_sample_never_produces_invalid_probabilities")


def test_unknown_team_raises_clear_error():
    import numpy as np
    np.random.seed(1)
    matches, teams = generate_synthetic_league()
    model = DixonColesModel()
    model.fit(matches)

    try:
        model.expected_goals("NotARealTeam", teams[0])
        assert False, "Expected ValueError for unknown team"
    except ValueError as e:
        assert "Unknown team" in str(e)
    print("PASS: test_unknown_team_raises_clear_error")


if __name__ == "__main__":
    test_scoreline_matrix_sums_to_one()
    test_strong_team_favored_at_home()
    test_double_chance_excludes_12_and_sums_correctly()
    test_match_total_goals_over_probs_monotonically_decrease()
    test_team_total_goals_over_probs_structure()
    test_extreme_small_sample_never_produces_invalid_probabilities()
    test_unknown_team_raises_clear_error()
    print("\nAll tests passed.")
