"""
tests/test_generate_html.py

Tests for the HTML dashboard generator, particularly the "best over
line" selection logic (see its docstring for why this isn't simply
"highest probability" - that trivially always picks the lowest line).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.output.generate_html import _best_over_line, _lines_from_probs, generate_html
import config


def test_lines_from_probs_extracts_and_sorts_dynamic_keys():
    """Core test for the dynamic-lines integration: with no fixed config
    list of shots/corners lines anymore, _best_over_line must derive
    the actual lines directly from the prediction dict's own keys.
    """
    probs = {"over_4.5": 0.6, "over_1.5": 0.9, "over_10.5": 0.1, "over_7.5": 0.3}
    lines = _lines_from_probs(probs)
    assert lines == [1.5, 4.5, 7.5, 10.5], f"Expected sorted extraction, got {lines}"
    print(f"PASS: test_lines_from_probs_extracts_and_sorts_dynamic_keys ({lines})")


def test_best_over_line_with_no_explicit_lines_uses_dict_keys():
    """When lines=None (the new default), _best_over_line should derive
    lines from over_probs itself - this is what generate_html.py now
    relies on for shots/corners, since those no longer have a shared
    fixed config list to pass in explicitly.
    """
    probs = {"over_1.5": 0.95, "over_4.5": 0.7, "over_9.5": 0.2}  # dynamic, strong-team-shaped range
    line, prob = _best_over_line(probs)
    assert line == 4.5, f"Expected highest line clearing the 0.5 floor (4.5), got {line}"
    print(f"PASS: test_best_over_line_with_no_explicit_lines_uses_dict_keys (line={line}, prob={prob})")


def test_best_over_line_handles_empty_dict_gracefully():
    """An empty over_probs dict (no lines at all) should return (None,
    None) rather than crash - this could happen if a model somehow
    generates zero lines for a degenerate expected value.
    """
    line, prob = _best_over_line({})
    assert line is None and prob is None
    print("PASS: test_best_over_line_handles_empty_dict_gracefully")


def test_best_over_line_picks_highest_confident_line_not_lowest():
    """Regression test for a real bug found during development: naively
    picking max-probability always selects the lowest line since Over
    probabilities are monotonically decreasing. This confirms the fix
    picks a higher line when confidence supports it.
    """
    probs = {
        "over_0.5": 0.95,
        "over_1.5": 0.85,
        "over_2.5": 0.60,
        "over_3.5": 0.20,  # below the 0.5 confidence floor
    }
    line, prob = _best_over_line(probs, [0.5, 1.5, 2.5, 3.5])
    assert line == 2.5, f"Expected highest line clearing confidence floor (2.5), got {line}"
    assert abs(prob - 0.60) < 1e-9
    print(f"PASS: test_best_over_line_picks_highest_confident_line_not_lowest (line={line}, prob={prob})")


def test_best_over_line_falls_back_when_nothing_clears_floor():
    probs = {"over_0.5": 0.3, "over_1.5": 0.1}
    line, prob = _best_over_line(probs, [0.5, 1.5])
    assert line == 0.5, f"Expected fallback to lowest line, got {line}"
    print(f"PASS: test_best_over_line_falls_back_when_nothing_clears_floor (line={line}, prob={prob})")


def test_best_over_line_handles_missing_keys():
    probs = {"over_0.5": 0.9}  # missing 1.5, 2.5
    line, prob = _best_over_line(probs, [0.5, 1.5, 2.5])
    assert line == 0.5
    print("PASS: test_best_over_line_handles_missing_keys")


def test_generate_html_handles_empty_predictions():
    html = generate_html([])
    assert "No upcoming fixtures found" in html
    assert "<!DOCTYPE html>" in html
    print("PASS: test_generate_html_handles_empty_predictions")


def test_generate_html_no_crash_on_full_prediction():
    prediction = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "match_winner": {"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
        "double_chance": {"1X": 0.75, "X2": 0.5},
        "match_total_goals": {f"over_{l}": max(0.9 - i * 0.15, 0.05) for i, l in enumerate([0.5, 1.5, 2.5, 3.5, 4.5])},
        "team_total_goals": {
            "home": {f"over_{l}": max(0.8 - i * 0.15, 0.05) for i, l in enumerate([0.5, 1.5, 2.5, 3.5])},
            "away": {f"over_{l}": max(0.7 - i * 0.15, 0.05) for i, l in enumerate([0.5, 1.5, 2.5, 3.5])},
        },
        "team_shots_on_target": {
            "home": {f"over_{l}": max(0.8 - i * 0.15, 0.05) for i, l in enumerate([2.5, 3.5, 4.5, 5.5])},
            "away": {f"over_{l}": max(0.6 - i * 0.15, 0.05) for i, l in enumerate([2.5, 3.5, 4.5, 5.5])},
        },
        "match_shots_on_target": {f"over_{l}": max(0.7 - i * 0.15, 0.05) for i, l in enumerate([7.5, 8.5, 9.5, 10.5])},
    }
    html = generate_html([prediction])
    assert "Arsenal" in html and "Chelsea" in html
    assert "<!DOCTYPE html>" in html
    print("PASS: test_generate_html_no_crash_on_full_prediction")


if __name__ == "__main__":
    test_lines_from_probs_extracts_and_sorts_dynamic_keys()
    test_best_over_line_with_no_explicit_lines_uses_dict_keys()
    test_best_over_line_handles_empty_dict_gracefully()
    test_best_over_line_picks_highest_confident_line_not_lowest()
    test_best_over_line_falls_back_when_nothing_clears_floor()
    test_best_over_line_handles_missing_keys()
    test_generate_html_handles_empty_predictions()
    test_generate_html_no_crash_on_full_prediction()
    print("\nAll tests passed.")
