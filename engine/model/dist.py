"""
engine/model/dist.py - discrete distributions as numpy probability arrays.

Goals are Poisson. Corners / shots / shots on target are over-dispersed
(variance > mean), so they use a negative binomial with variance = phi * mean.
phi is measured from the data in the backtest, not guessed.
"""

import numpy as np

SUPPORT = {"g": 16, "c": 36, "s": 70, "t": 36}   # array length per stat (0..n)


def pmf(mu: float, phi: float, n: int) -> np.ndarray:
    """P(X=k) for k=0..n-1 (renormalised, so tiny tail mass is not lost)."""
    mu = max(float(mu), 1e-6)
    k = np.arange(n - 1, dtype=float)
    if phi <= 1.001:
        p0 = np.exp(-mu)
        ratios = mu / (k + 1.0)
    else:
        r = mu / (phi - 1.0)
        q = r / (r + mu)                      # success prob; mean = r(1-q)/q = mu
        p0 = q ** r
        ratios = (k + r) / (k + 1.0) * (1.0 - q)
    out = p0 * np.concatenate(([1.0], np.cumprod(ratios)))
    s = out.sum()
    return out / s if s > 0 else out


def p_over(p: np.ndarray, line: float) -> float:
    """P(X > line) for a .5 line."""
    return float(p[int(line) + 1:].sum())


def conv(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.convolve(a, b)


def erf_np(x: np.ndarray) -> np.ndarray:
    """Vectorised erf (Abramowitz-Stegun 7.1.26, abs error < 1.5e-7)."""
    s = np.sign(x)
    x = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return s * y


def norm_cdf(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + erf_np(z / np.sqrt(2.0)))
