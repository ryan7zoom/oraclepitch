"""
tests/test_kelly.py

Tests for Kelly Criterion staking logic, particularly the "no edge, no
bet" rule and the fractional Kelly discount.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.backtest.kelly import kelly_fraction, implied_probability, stake_amount
import config


def test_implied_probability_basic():
    assert abs(implied_probability(2.0) - 0.5) < 1e-9
    assert abs(implied_probability(4.0) - 0.25) < 1e-9
    print("PASS: test_implied_probability_basic")


def test_implied_probability_rejects_invalid_odds():
    try:
        implied_probability(1.0)
        assert False, "Expected ValueError for odds <= 1.0"
    except ValueError:
        pass
    print("PASS: test_implied_probability_rejects_invalid_odds")


def test_no_edge_means_no_bet():
    # model probability exactly equals implied probability -> no edge -> no bet
    result = kelly_fraction(model_probability=0.5, decimal_odds=2.0)
    assert result.should_bet is False
    assert result.recommended_fraction == 0.0
    print("PASS: test_no_edge_means_no_bet")


def test_negative_edge_means_no_bet():
    # model thinks it's less likely than the bookmaker implies -> no bet
    result = kelly_fraction(model_probability=0.4, decimal_odds=2.0)  # implied = 0.5
    assert result.should_bet is False
    assert result.edge < 0
    print("PASS: test_negative_edge_means_no_bet")


def test_positive_edge_produces_positive_stake():
    # model thinks 60% likely, bookmaker implies 50% -> real edge
    result = kelly_fraction(model_probability=0.6, decimal_odds=2.0)
    assert result.should_bet is True
    assert result.full_kelly_fraction > 0
    assert result.recommended_fraction > 0
    # full kelly for p=0.6, b=1.0: f* = (0.6*1 - 0.4)/1 = 0.2
    assert abs(result.full_kelly_fraction - 0.2) < 1e-9
    print(f"PASS: test_positive_edge_produces_positive_stake ({result})")


def test_fractional_kelly_applies_configured_fraction():
    result = kelly_fraction(model_probability=0.6, decimal_odds=2.0)
    expected_recommended = result.full_kelly_fraction * config.KELLY_FRACTION
    assert abs(result.recommended_fraction - expected_recommended) < 1e-9
    print(f"PASS: test_fractional_kelly_applies_configured_fraction "
          f"(KELLY_FRACTION={config.KELLY_FRACTION})")


def test_stake_amount_scales_with_bankroll():
    stake_1000 = stake_amount(1000, model_probability=0.6, decimal_odds=2.0)
    stake_2000 = stake_amount(2000, model_probability=0.6, decimal_odds=2.0)
    assert abs(stake_2000 - 2 * stake_1000) < 1e-6
    print(f"PASS: test_stake_amount_scales_with_bankroll (1000->{stake_1000:.2f}, 2000->{stake_2000:.2f})")


def test_invalid_model_probability_rejected():
    try:
        kelly_fraction(model_probability=1.5, decimal_odds=2.0)
        assert False, "Expected ValueError for probability > 1"
    except ValueError:
        pass
    try:
        kelly_fraction(model_probability=-0.1, decimal_odds=2.0)
        assert False, "Expected ValueError for negative probability"
    except ValueError:
        pass
    print("PASS: test_invalid_model_probability_rejected")


if __name__ == "__main__":
    test_implied_probability_basic()
    test_implied_probability_rejects_invalid_odds()
    test_no_edge_means_no_bet()
    test_negative_edge_means_no_bet()
    test_positive_edge_produces_positive_stake()
    test_fractional_kelly_applies_configured_fraction()
    test_stake_amount_scales_with_bankroll()
    test_invalid_model_probability_rejected()
    print("\nAll tests passed.")
