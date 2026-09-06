"""
engine/prediction/ensemble.py

Combines the outputs of the Dixon-Coles model, Monte Carlo simulator, and
shots-on-target model into a single set of final probabilities per match,
using the weights defined in config.ENSEMBLE_WEIGHTS.

Design note: Dixon-Coles and Monte Carlo predict the *same* underlying
markets (match winner, double chance, goals) from the *same* fitted rate
parameters, so blending them is really an average of a closed-form
estimate and a simulated estimate of the same quantity - useful mostly
as a smoothing/sanity mechanism (see test_monte_carlo.py, which confirms
they already agree closely). Shots on target is a structurally separate
model (negative binomial on a different stat) and isn't blended with
anything - it's just passed through. If corners are re-enabled later,
they'd be handled the same way as shots.
"""

from dataclasses import dataclass, asdict

import config
from engine.prediction.dixon_coles import DixonColesModel
from engine.prediction.monte_carlo import MonteCarloSimulator
from engine.prediction.shots import ShotsOnTargetModel


@dataclass
class MatchPrediction:
    home_team: str
    away_team: str
    match_winner: dict
    double_chance: dict
    match_total_goals: dict
    team_total_goals: dict
    team_shots_on_target: dict
    match_shots_on_target: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _blend(dc_probs: dict, mc_probs: dict, weights: dict) -> dict:
    """Weighted average of two probability dicts sharing the same keys."""
    w_dc = weights["dixon_coles"]
    w_mc = weights["monte_carlo"]
    total_weight = w_dc + w_mc
    return {
        key: (dc_probs[key] * w_dc + mc_probs[key] * w_mc) / total_weight
        for key in dc_probs
    }


def _blend_nested(dc_probs: dict, mc_probs: dict, weights: dict) -> dict:
    """Same as _blend but for one level of nesting (e.g. {'home': {...}, 'away': {...}})."""
    return {
        side: _blend(dc_probs[side], mc_probs[side], weights)
        for side in dc_probs
    }


class EnsemblePredictor:
    def __init__(
        self,
        dixon_coles_model: DixonColesModel,
        monte_carlo_simulator: MonteCarloSimulator,
        shots_model: ShotsOnTargetModel,
        weights: dict = None,
    ):
        self.dc_model = dixon_coles_model
        self.mc_simulator = monte_carlo_simulator
        self.shots_model = shots_model
        self.weights = weights or config.ENSEMBLE_WEIGHTS

        total = self.weights.get("dixon_coles", 0) + self.weights.get("monte_carlo", 0)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"ENSEMBLE_WEIGHTS for dixon_coles + monte_carlo must sum to 1.0, "
                f"got {total}. Check config.py."
            )

    def predict(self, home_team: str, away_team: str) -> MatchPrediction:
        dc_outcome = self.dc_model.match_outcome_probs(home_team, away_team)
        mc_outcome = self.mc_simulator.match_outcome_probs(home_team, away_team)
        match_winner = _blend(dc_outcome, mc_outcome, self.weights)

        dc_dc = self.dc_model.double_chance_probs(home_team, away_team)
        mc_dc = self.mc_simulator.double_chance_probs(home_team, away_team)
        double_chance = _blend(dc_dc, mc_dc, self.weights)

        dc_goals = self.dc_model.match_total_goals_over_probs(home_team, away_team)
        mc_goals = self.mc_simulator.match_total_goals_over_probs(home_team, away_team)
        match_total_goals = _blend(dc_goals, mc_goals, self.weights)

        dc_team_goals = self.dc_model.team_total_goals_over_probs(home_team, away_team)
        mc_team_goals = self.mc_simulator.team_total_goals_over_probs(home_team, away_team)
        team_total_goals = _blend_nested(dc_team_goals, mc_team_goals, self.weights)

        # Shots on target is not blended - it's a separate model entirely
        team_shots_on_target = self.shots_model.team_over_probs(home_team, away_team)
        match_shots_on_target = self.shots_model.match_total_over_probs(home_team, away_team)

        return MatchPrediction(
            home_team=home_team,
            away_team=away_team,
            match_winner=match_winner,
            double_chance=double_chance,
            match_total_goals=match_total_goals,
            team_total_goals=team_total_goals,
            team_shots_on_target=team_shots_on_target,
            match_shots_on_target=match_shots_on_target,
        )
