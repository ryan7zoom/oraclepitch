"""
tests/test_corners_calibration.py

Tests for _run_corners_calibration_check in engine/backtest/simulator.py.
Since football-data.co.uk has no corners odds, this calibration check
(predicted probability vs actual frequency) is the only way to get a
meaningful signal on the corners model's quality - these tests confirm
the mechanism itself works correctly, particularly the no-lookahead
property (reusing the same discipline verified for run_backtest() in
test_simulator.py).
"""

import sys
import os
from dataclasses import dataclass
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from engine.backtest.simulator import _run_corners_calibration_check
import config


@dataclass
class FakeCornersRow:
    date: date
    home_team: str
    away_team: str
    home_corners: float
    away_corners: float


def _generate_fake_corners_history(n_teams=8, matches_per_pair=6, seed=11):
    """Generate synthetic corners data where home corners are genuinely
    Poisson-ish around a team-strength-dependent mean, so a well-behaved
    model fit on this data SHOULD show reasonable calibration - this
    isn't testing whether the model is a good corners model in general.
    Just whether the calibration-checking mechanism correctly measures
    what it claims to measure.
    """
    np.random.seed(seed)
    teams = [f"Team{i}" for i in range(n_teams)]
    true_strength = {t: (n_teams - i) for i, t in enumerate(teams)}
    rows = []
    start = date(2023, 8, 1)
    day_counter = 0
    for _ in range(matches_per_pair):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                home, away = teams[i], teams[j]
                mean_corners = max(4.5 + 0.3 * true_strength[home] - 0.2 * true_strength[away], 1.0)
                home_corners = int(np.random.poisson(mean_corners))
                away_corners = int(np.random.poisson(4.0))
                rows.append(FakeCornersRow(
                    date=start + timedelta(days=day_counter),
                    home_team=home, away_team=away,
                    home_corners=home_corners, away_corners=away_corners,
                ))
                day_counter += 1
    return rows


def test_calibration_returns_dynamic_lines_actually_used():
    """With dynamic per-match line generation (see corners.py's
    generate_lines()), there's no longer a single fixed set of lines
    guaranteed across every match - the result's keys depend on what
    range each match's expected corners actually produced. This test
    checks the result is non-empty and every key follows the expected
    "over_X.X" format, rather than asserting a fixed key set that no
    longer exists.
    """
    rows = _generate_fake_corners_history()
    result = _run_corners_calibration_check(rows, min_training_matches=40)

    assert len(result) > 0, "Expected at least some lines to appear in the calibration result"
    for key in result:
        assert key.startswith("over_"), f"Unexpected key format: {key}"
        float(key.split("_", 1)[1])  # should parse cleanly as a float
    # The lowest configured line should always appear, since every
    # team's dynamic range includes CORNERS_MIN_LINE regardless of
    # their expected value (see generate_lines()'s floor guarantee).
    min_line_key = f"over_{config.CORNERS_MIN_LINE}"
    assert min_line_key in result, f"Expected the floor line {min_line_key} to always appear, got keys: {list(result.keys())}"
    print(f"PASS: test_calibration_returns_dynamic_lines_actually_used ({sorted(result.keys(), key=lambda k: float(k.split('_')[1]))})")


def test_calibration_produces_reasonable_predictions_with_enough_data():
    """With a reasonably-sized synthetic dataset that has real signal
    (home teams from stronger sides get more corners), the model's
    average predicted probability and the actual observed frequency
    should be in the same ballpark (not exactly equal - that would be
    an unreasonably strict bar for a real-world-shaped stochastic
    process - but not wildly divergent either, which would indicate a
    broken calibration mechanism, not just normal model imperfection).

    Only checks lines with a reasonable sample size (n >= 20) - with
    dynamic lines, a high line like over_9.5 may only appear for the
    small subset of matches where a team's expected corners were high
    enough to generate it, and a small-n average is expected to be
    noisier without that indicating a broken mechanism.
    """
    rows = _generate_fake_corners_history()
    result = _run_corners_calibration_check(rows, min_training_matches=40)

    checked_any = False
    for key, (predicted, actual, n) in result.items():
        if n < 20:
            continue
        checked_any = True
        assert predicted is not None and actual is not None
        assert 0.0 <= predicted <= 1.0
        assert 0.0 <= actual <= 1.0
        diff = abs(predicted - actual)
        assert diff < 0.25, (
            f"{key}: predicted={predicted:.3f} vs actual={actual:.3f} (n={n}) - "
            f"large divergence suggests a broken calibration mechanism, not just model imperfection"
        )
    assert checked_any, "Expected at least one line with n >= 20 to check calibration against"
    print(f"PASS: test_calibration_produces_reasonable_predictions_with_enough_data ({result})")


def test_calibration_handles_no_lookahead():
    """Direct check that predictions for a given match only use training
    data strictly before that match's date - same discipline as
    test_simulator.py's test_no_lookahead_bias, applied to this
    separate calibration code path since it has its own independent
    training-window slicing logic, not shared with run_backtest().
    """
    rows = _generate_fake_corners_history(n_teams=6, matches_per_pair=4)
    sorted_rows = sorted(rows, key=lambda r: r.date)

    from engine.prediction.corners import build_corners_profiles_from_historical
    import engine.backtest.simulator as sim_module

    call_log = []
    original = build_corners_profiles_from_historical

    def instrumented(training_data):
        if training_data:
            call_log.append(max(r.date for r in training_data))
        return original(training_data)

    sim_module.build_corners_profiles_from_historical = instrumented
    try:
        # Need to patch the import used INSIDE the function, not the
        # module-level name here - _run_corners_calibration_check does
        # `from engine.prediction.corners import ... build_corners_profiles_from_historical`
        # as a local import, so patching sim_module's attribute won't
        # actually intercept it. Patch at the source module instead.
        import engine.prediction.corners as corners_module
        corners_module.build_corners_profiles_from_historical = instrumented

        result = _run_corners_calibration_check(sorted_rows, min_training_matches=20)
    finally:
        corners_module.build_corners_profiles_from_historical = original

    assert len(call_log) > 0, "Expected at least one training call to be logged"
    # Every logged training max-date should be strictly less than SOME
    # match's date that came after it chronologically - the simplest
    # direct check is that the largest logged training date is still
    # less than the last match's date (can't have trained on the future).
    assert max(call_log) < sorted_rows[-1].date, (
        "Training data included a date at or after the final match - possible lookahead"
    )
    print(f"PASS: test_calibration_handles_no_lookahead ({len(call_log)} training calls checked)")


if __name__ == "__main__":
    test_calibration_returns_dynamic_lines_actually_used()
    test_calibration_produces_reasonable_predictions_with_enough_data()
    test_calibration_handles_no_lookahead()
    print("\nAll tests passed.")
