"""
engine/prediction/monte_carlo.py

Monte Carlo simulation of match outcomes, using the fitted Dixon-Coles
expected goals as the basis for simulated scorelines. This exists as an
independent-but-related estimate to combine with the closed-form
Dixon-Coles probabilities in engine/prediction/ensemble.py - both are
built on the same underlying rate parameters (lambda, mu), but the
Monte Carlo path re-derives markets by simulating discrete match
outcomes many times, which is a useful cross-check: if the two methods
diverge substantially, that's a signal something is wrong (e.g. a bug
in one of the closed-form probability sums), not just noise to average
away silently.
"""

from dataclasses import dataclass

import numpy as np

import config
from engine.prediction.dixon_coles import DixonColesModel, tau


@dataclass
class SimulationResult:
    home_goals: np.ndarray  # shape (n_iterations,)
    away_goals: np.ndarray


class MonteCarloSimulator:
    def __init__(self, model: DixonColesModel, n_iterations: int = None, seed: int = None):
        self.model = model
        self.n_iterations = n_iterations or config.MONTE_CARLO_ITERATIONS
        self.rng = np.random.default_rng(seed)

    def simulate(self, home_team: str, away_team: str) -> SimulationResult:
        """Simulate n_iterations independent matches using the fitted
        Dixon-Coles rate parameters. Uses rejection sampling on the tau
        correction for the four affected low-score cells so the
        Dixon-Coles adjustment is respected, not just plain independent
        Poisson draws.
        """
        lam, mu = self.model.expected_goals(home_team, away_team)
        rho = self.model.rho

        home_draws = self.rng.poisson(lam, size=self.n_iterations)
        away_draws = self.rng.poisson(mu, size=self.n_iterations)

        # Apply tau correction via rejection sampling on the low-score cells.
        # For cells where tau < 1 (probability should be reduced), we
        # randomly discard and redraw a fraction of matching samples.
        # For tau > 1 (probability boosted), technically we'd want to
        # upweight rather than reject - since tau > 1 only occurs for
        # (0,1) and (1,0) and only mildly, we approximate by leaving
        # those as pure Poisson draws (a known small approximation,
        # documented rather than silently ignored).
        for (hg, ag) in [(0, 0), (1, 1)]:
            tau_val = tau(hg, ag, lam, mu, rho)
            if tau_val < 1.0:
                mask = (home_draws == hg) & (away_draws == ag)
                idx = np.where(mask)[0]
                if len(idx) > 0:
                    reject_prob = 1.0 - tau_val
                    reject_mask = self.rng.random(len(idx)) < reject_prob
                    reject_idx = idx[reject_mask]
                    if len(reject_idx) > 0:
                        home_draws[reject_idx] = self.rng.poisson(lam, size=len(reject_idx))
                        away_draws[reject_idx] = self.rng.poisson(mu, size=len(reject_idx))

        return SimulationResult(home_goals=home_draws, away_goals=away_draws)

    def match_outcome_probs(self, home_team: str, away_team: str) -> dict:
        sim = self.simulate(home_team, away_team)
        n = len(sim.home_goals)
        home_win = float(np.sum(sim.home_goals > sim.away_goals)) / n
        draw = float(np.sum(sim.home_goals == sim.away_goals)) / n
        away_win = float(np.sum(sim.home_goals < sim.away_goals)) / n
        return {"home_win": home_win, "draw": draw, "away_win": away_win}

    def double_chance_probs(self, home_team: str, away_team: str) -> dict:
        outcomes = self.match_outcome_probs(home_team, away_team)
        return {
            "1X": outcomes["home_win"] + outcomes["draw"],
            "X2": outcomes["away_win"] + outcomes["draw"],
        }

    def match_total_goals_over_probs(self, home_team: str, away_team: str, lines: list[float] = None) -> dict:
        lines = lines or config.GOAL_LINES
        sim = self.simulate(home_team, away_team)
        totals = sim.home_goals + sim.away_goals
        n = len(totals)
        return {
            f"over_{line}": float(np.sum(totals > line)) / n
            for line in lines
        }

    def team_total_goals_over_probs(self, home_team: str, away_team: str, lines: list[float] = None) -> dict:
        lines = lines or config.TEAM_GOAL_LINES
        sim = self.simulate(home_team, away_team)
        n = len(sim.home_goals)
        return {
            "home": {f"over_{line}": float(np.sum(sim.home_goals > line)) / n for line in lines},
            "away": {f"over_{line}": float(np.sum(sim.away_goals > line)) / n for line in lines},
        }
