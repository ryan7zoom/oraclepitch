"""Helpers shared by the runner: league base rates."""

from collections import defaultdict

from engine.model import markets


def base_rates(matches) -> dict:
    """How often each market key hit across the given matches: {key: rate}."""
    keys = markets.all_keys()
    n, h = defaultdict(int), defaultdict(int)
    for m in matches:
        for k, a in markets.actuals(m, keys).items():
            n[k] += 1; h[k] += a
    return {k: h[k] / n[k] for k in n if n[k] >= 100}
