"""
engine/backtest/kelly.py

Kelly Criterion staking calculator.

f* = (p * b - q) / b

where:
  p = model's predicted probability of the bet winning
  b = decimal odds - 1 (the "net" payout multiple)
  q = 1 - p

A fractional Kelly (config.KELLY_FRACTION) is applied on top of the full
Kelly stake, which is standard practice to reduce variance - full Kelly
is provably optimal for long-run growth ONLY if the model's probabilities
are exactly correct, which they never are in practice, so betting full
Kelly against imperfect estimates leads to excessive drawdowns.
"""

from dataclasses import dataclass

import config


@dataclass
class KellyResult:
    edge: float           # model_probability - implied_probability
    full_kelly_fraction: float
    recommended_fraction: float  # full_kelly_fraction * KELLY_FRACTION
    should_bet: bool


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError(f"Decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def kelly_fraction(model_probability: float, decimal_odds: float) -> KellyResult:
    """Compute the Kelly stake fraction for a single bet.

    Returns a KellyResult with should_bet=False (and fraction=0) when the
    model's probability does not exceed the bookmaker's implied
    probability - i.e. no perceived edge, no bet, per the spec's rule
    "Only bet when model_probability > implied_probability."
    """
    if not (0.0 <= model_probability <= 1.0):
        raise ValueError(f"model_probability must be in [0, 1], got {model_probability}")

    implied_p = implied_probability(decimal_odds)
    edge = model_probability - implied_p

    if edge <= 0:
        return KellyResult(
            edge=edge, full_kelly_fraction=0.0,
            recommended_fraction=0.0, should_bet=False,
        )

    b = decimal_odds - 1.0
    q = 1.0 - model_probability
    f_full = (model_probability * b - q) / b

    # f_full should be positive here given edge > 0, but guard against
    # floating point edge cases rather than assume.
    f_full = max(f_full, 0.0)
    f_recommended = f_full * config.KELLY_FRACTION

    return KellyResult(
        edge=edge,
        full_kelly_fraction=f_full,
        recommended_fraction=f_recommended,
        should_bet=f_recommended > 0,
    )


def stake_amount(bankroll: float, model_probability: float, decimal_odds: float) -> float:
    """Convenience wrapper returning an actual currency stake amount."""
    result = kelly_fraction(model_probability, decimal_odds)
    return bankroll * result.recommended_fraction
