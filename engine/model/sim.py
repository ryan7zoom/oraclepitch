"""
engine/model/sim.py - correlated match simulation (for bet-builder combos).

Legs in one match are NOT independent (a high-scoring game also has more
shots and corners). A Gaussian copula keeps each stat's own distribution
exact but links them with correlations measured in the backtest. Half-time
goals are split from full-time goals (binomial), so halves always add up.
"""

import base64

import numpy as np

from engine.model import dist, markets


def nearest_corr(c: np.ndarray) -> np.ndarray:
    """Make a symmetric matrix a valid correlation matrix."""
    c = (c + c.T) / 2
    w, v = np.linalg.eigh(c)
    w = np.clip(w, 1e-4, None)
    c = (v * w) @ v.T
    d = np.sqrt(np.diag(c))
    return c / np.outer(d, d)


def simulate(params, corr, n: int = 2000, seed: int = 7) -> np.ndarray:
    """(n, 10) uint8 array, columns = markets.SIM_COLS."""
    rng = np.random.default_rng(seed)
    d = markets.pmfs(params)
    avail = [v for v in markets.BASE_VARS if v in d]
    idx = [markets.BASE_VARS.index(v) for v in avail]
    R = np.eye(len(avail)) if corr is None else nearest_corr(np.asarray(corr)[np.ix_(idx, idx)])
    z = rng.multivariate_normal(np.zeros(len(avail)), R, size=n)
    u = dist.norm_cdf(z)
    out = np.zeros((n, len(markets.SIM_COLS)), dtype=np.uint8)
    for j, name in enumerate(avail):
        cdf = np.cumsum(d[name])
        k = np.searchsorted(cdf, u[:, j], side="left")
        out[:, markets.SIM_COLS.index(name)] = np.minimum(k, len(cdf) - 1)
    for side in ("h", "a"):
        g = out[:, markets.SIM_COLS.index("g_" + side)].astype(int)
        out[:, markets.SIM_COLS.index("g1_" + side)] = rng.binomial(g, params.share[side])
    return out


def to_values(row) -> dict:
    """One simulated row -> the value dict eval_key() expects."""
    v = {c: int(row[i]) for i, c in enumerate(markets.SIM_COLS)}
    v["g2_h"], v["g2_a"] = v["g_h"] - v["g1_h"], v["g_a"] - v["g1_a"]
    return v


def joint_prob(sim: np.ndarray, keys: list) -> float:
    ok = 0
    for row in sim:
        v = to_values(row)
        if all(markets.eval_key(k, v) for k in keys):
            ok += 1
    return ok / len(sim)


def encode(sim: np.ndarray) -> str:
    return base64.b64encode(sim.astype(np.uint8).tobytes()).decode("ascii")
