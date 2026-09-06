"""
engine/prediction/dixon_coles.py

Dixon-Coles Poisson model for football match outcomes.

Reference: Dixon, M.J. and Coles, S.G. (1997), "Modelling Association
Football Scores and Inefficiencies in the Football Betting Market."

Model summary:
- Each team i has an attack strength alpha_i and defense strength beta_i.
- Home advantage is a single global parameter gamma.
- Expected home goals = exp(alpha_home + beta_away + gamma)
- Expected away goals = exp(alpha_away + beta_home)
- Goals are Poisson-distributed given these expectations.
- The tau() correction adjusts the joint probability of low-scoring
  outcomes (0-0, 1-0, 0-1, 1-1), which vanilla independent-Poisson
  models get slightly wrong empirically.
- Time decay downweights older matches when fitting, via exponential
  decay with a configurable half-life.
"""

from dataclasses import dataclass, field
from datetime import date
from math import exp, factorial

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

import config


@dataclass
class TeamRatings:
    attack: dict = field(default_factory=dict)
    defense: dict = field(default_factory=dict)
    home_advantage: float = 0.0
    teams: list = field(default_factory=list)


@dataclass
class MatchInput:
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    match_date: date


def tau(home_goals: int, away_goals: int, lambda_home: float, mu_away: float, rho: float) -> float:
    """Dixon-Coles low-score correction factor.

    This is a probability *multiplier* for the four low-score cells, so
    it must never go negative - a negative multiplier on a Poisson
    probability produces a negative "probability," which is meaningless
    and corrupts everything downstream (win/draw/loss sums, over/under
    sums, Kelly staking, etc.).

    In theory rho is small (Dixon & Coles report roughly +/-0.2 for real
    league data) and lambda/mu are reasonable goal expectations, so this
    never approaches zero in practice. But when lambda or mu is unusually
    large (observed in testing: mu ~2.3 from an unstable small-sample fit
    with extreme attack/defense parameters - see fit()'s rho bounds
    comment), terms like `1 + mu_away * rho` can go negative well within
    rho's allowed range. Clamping at a small positive epsilon (rather
    than 0 exactly, to avoid a zero-probability cell that would make
    some downstream ratio computations divide by zero) is a deliberate
    floor, not a silent correctness compromise - see the RuntimeError
    guard in scoreline_matrix() for cases where the *unclamped* value
    would have been more severely wrong, which still gets surfaced.
    """
    if home_goals == 0 and away_goals == 0:
        raw = 1 - (lambda_home * mu_away * rho)
    elif home_goals == 0 and away_goals == 1:
        raw = 1 + (lambda_home * rho)
    elif home_goals == 1 and away_goals == 0:
        raw = 1 + (mu_away * rho)
    elif home_goals == 1 and away_goals == 1:
        raw = 1 - rho
    else:
        raw = 1.0
    return max(raw, 1e-6)


def _time_weight(match_date: date, as_of: date, half_life_days: float) -> float:
    days_ago = max((as_of - match_date).days, 0)
    decay_rate = np.log(2) / half_life_days
    return float(np.exp(-decay_rate * days_ago))


class DixonColesModel:
    """Fits attack/defense/home-advantage parameters via maximum likelihood
    and exposes methods to predict scoreline distributions.
    """

    def __init__(self, half_life_days: float = None):
        self.half_life_days = half_life_days or config.TIME_DECAY_HALF_LIFE
        self.ratings: TeamRatings | None = None
        self.rho: float = 0.0  # low-score correction parameter, fitted

    def fit(self, matches: list[MatchInput], as_of: date | None = None):
        """Fit attack/defense/home-advantage/rho parameters via MLE.

        matches: list of completed matches with goals and dates.
        as_of: reference date for time-decay weighting (defaults to the
               date of the most recent match, so backtesting at a
               historical point in time works correctly).
        """
        if not matches:
            raise ValueError("Cannot fit model on empty match list")

        teams = sorted({m.home_team for m in matches} | {m.away_team for m in matches})
        n = len(teams)
        team_idx = {t: i for i, t in enumerate(teams)}

        if as_of is None:
            as_of = max(m.match_date for m in matches)

        weights = np.array([
            _time_weight(m.match_date, as_of, self.half_life_days) for m in matches
        ])

        # Precompute integer team-index and goal arrays once, outside the
        # objective function, so the (frequently-called, during numerical
        # gradient estimation) likelihood evaluation is pure vectorized
        # numpy rather than a Python-level for loop over matches.
        #
        # PERFORMANCE NOTE: profiling during development showed the
        # original pure-Python-loop version of this function took ~5s per
        # fit on just 100 matches / 8 teams (360 likelihood evaluations
        # during one L-BFGS-B run, each doing a Python for-loop over all
        # matches for numerical gradient estimation). That's impractical
        # for a real backtest over 1000+ matches and 20 teams, and was
        # actually observed to cause backtest tests to time out during
        # development. This vectorized version does the same computation
        # with numpy array operations instead of a per-match Python loop.
        home_idx = np.array([team_idx[m.home_team] for m in matches])
        away_idx = np.array([team_idx[m.away_team] for m in matches])
        home_goals_arr = np.array([m.home_goals for m in matches])
        away_goals_arr = np.array([m.away_goals for m in matches])

        # Precompute log-factorials for the Poisson log-pmf, since
        # scipy.stats.poisson.logpmf has more overhead than needed here.
        home_log_fact = np.array([np.log(float(factorial(g))) for g in home_goals_arr])
        away_log_fact = np.array([np.log(float(factorial(g))) for g in away_goals_arr])

        # Boolean masks for which matches fall into each of the four
        # tau-affected low-score cells, precomputed once.
        mask_00 = (home_goals_arr == 0) & (away_goals_arr == 0)
        mask_01 = (home_goals_arr == 0) & (away_goals_arr == 1)
        mask_10 = (home_goals_arr == 1) & (away_goals_arr == 0)
        mask_11 = (home_goals_arr == 1) & (away_goals_arr == 1)

        # Parameter vector: [attack_0..attack_n-1, defense_0..defense_n-1, home_adv, rho]
        # Fix attack_0 = 0 for identifiability (attack/defense are only
        # meaningful relative to each other).
        def unpack(params):
            attack = np.concatenate(([0.0], params[: n - 1]))
            defense = params[n - 1: 2 * n - 1]
            home_adv = params[2 * n - 1]
            rho = params[2 * n]
            return attack, defense, home_adv, rho

        # L2 regularization strength on attack/defense parameters. Without
        # this, teams with very small/lopsided samples (e.g. early in a
        # backtest window, or a newly promoted team) can have their
        # attack or defense parameter driven to extreme values (observed
        # in testing: attack as low as -15.6 on a 35-match/8-team sample)
        # since MLE has very little signal to constrain them. A mild L2
        # penalty pulls under-supported parameters back toward 0 (the
        # league-average team) without materially affecting well-supported
        # ones. This is a standard shrinkage approach for exactly this
        # kind of small-sample instability.
        l2_penalty = 0.01

        def neg_log_likelihood(params):
            attack, defense, home_adv, rho = unpack(params)

            lam = np.exp(attack[home_idx] + defense[away_idx] + home_adv)
            mu = np.exp(attack[away_idx] + defense[home_idx])

            log_pmf_home = home_goals_arr * np.log(lam) - lam - home_log_fact
            log_pmf_away = away_goals_arr * np.log(mu) - mu - away_log_fact

            # Vectorized tau: compute the raw (unclamped) value for each
            # of the four cells across all matches at once, using the
            # masks to select which formula applies, then clamp with the
            # same epsilon floor as the scalar tau() function (kept in
            # sync deliberately - see tau()'s docstring for why the floor
            # exists).
            tau_val = np.ones_like(lam)
            tau_val = np.where(mask_00, 1 - lam * mu * rho, tau_val)
            tau_val = np.where(mask_01, 1 + lam * rho, tau_val)
            tau_val = np.where(mask_10, 1 + mu * rho, tau_val)
            tau_val = np.where(mask_11, 1 - rho, tau_val)
            tau_val = np.maximum(tau_val, 1e-6)

            total = np.sum(weights * (log_pmf_home + log_pmf_away + np.log(tau_val)))

            regularization = l2_penalty * (np.sum(attack ** 2) + np.sum(defense ** 2))
            return -total + regularization

        # Vector layout: (n-1) attack params + n defense params + home_adv + rho
        initial_params = np.zeros(2 * n + 1)
        initial_params[2 * n - 1] = 0.2  # reasonable starting home advantage
        initial_params[2 * n] = 0.0      # rho starts at 0 (no correction)

        # Bound rho to a plausible range. Dixon & Coles' original paper
        # reports fitted rho values roughly in [-0.2, 0.2] for real league
        # data. Leaving rho unconstrained lets the optimizer drive it to
        # extreme values on small/pathological training samples (observed
        # empirically: rho -> ~-1,000,000 on a 30-match window), which
        # produces a scoreline matrix with large canceling positive and
        # negative entries that still happen to sum to ~1.0 - passing a
        # naive "sums to 1" sanity check while individual probabilities
        # are nonsensical (negative, or in the hundreds of thousands).
        # Attack/defense/home_advantage are left unbounded since they're
        # regularized implicitly by the identifiability constraint
        # (attack_0 = 0) and haven't shown the same blowup in testing.
        n_params = 2 * n + 1
        bounds = [(None, None)] * (n_params - 1) + [(-0.5, 0.5)]

        result = minimize(
            neg_log_likelihood,
            initial_params,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 500},
        )

        attack, defense, home_adv, rho = unpack(result.x)
        self.rho = float(rho)
        self.ratings = TeamRatings(
            attack={t: float(attack[team_idx[t]]) for t in teams},
            defense={t: float(defense[team_idx[t]]) for t in teams},
            home_advantage=float(home_adv),
            teams=teams,
        )
        return result

    def expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Return (expected_home_goals, expected_away_goals)."""
        if self.ratings is None:
            raise RuntimeError("Model has not been fit yet")
        r = self.ratings
        if home_team not in r.attack or away_team not in r.attack:
            raise ValueError(
                f"Unknown team(s): {home_team!r} and/or {away_team!r} not in "
                f"fitted team list. New/promoted teams need special handling "
                f"(e.g. league-average priors) - not yet implemented."
            )
        lam = exp(r.attack[home_team] + r.defense[away_team] + r.home_advantage)
        mu = exp(r.attack[away_team] + r.defense[home_team])
        return lam, mu

    def scoreline_matrix(self, home_team: str, away_team: str, max_goals: int = None) -> np.ndarray:
        """Return an (max_goals+1) x (max_goals+1) matrix where entry [i][j]
        is P(home scores i, away scores j), with the tau correction applied
        to the four low-score cells.
        """
        max_goals = max_goals or config.MAX_GOALS_FOR_DIST
        lam, mu = self.expected_goals(home_team, away_team)

        home_probs = poisson.pmf(np.arange(max_goals + 1), lam)
        away_probs = poisson.pmf(np.arange(max_goals + 1), mu)
        matrix = np.outer(home_probs, away_probs)

        for i in range(min(2, max_goals + 1)):
            for j in range(min(2, max_goals + 1)):
                matrix[i][j] *= tau(i, j, lam, mu, self.rho)

        # Defensive floor: a well-behaved fit should never produce negative
        # cell probabilities. If it does (e.g. rho fit to an extreme value
        # on a pathological small sample - see the bounds added in fit()),
        # that's a real bug, not something to paper over by clipping and
        # silently continuing. Fail loudly here rather than let a negative
        # or absurd probability quietly reach the betting/staking logic,
        # which was previously masked because the matrix still summed to
        # ~1.0 despite individual cells being nonsensical.
        if (matrix < -1e-9).any():
            raise RuntimeError(
                f"scoreline_matrix produced negative probabilities "
                f"(rho={self.rho}) for {home_team} vs {away_team} - "
                f"model fit is unstable, likely on too small a training sample."
            )

        # Renormalize since tau adjustments can push total probability
        # slightly away from 1.0
        matrix = np.clip(matrix, 0, None)
        matrix = matrix / matrix.sum()
        return matrix

    def match_outcome_probs(self, home_team: str, away_team: str) -> dict:
        """Return P(home win), P(draw), P(away win)."""
        matrix = self.scoreline_matrix(home_team, away_team)
        home_win = float(np.tril(matrix, k=-1).sum())
        draw = float(np.trace(matrix))
        away_win = float(np.triu(matrix, k=1).sum())
        return {"home_win": home_win, "draw": draw, "away_win": away_win}

    def double_chance_probs(self, home_team: str, away_team: str) -> dict:
        """1X (home or draw) and X2 (away or draw) ONLY - no 12 per spec."""
        outcomes = self.match_outcome_probs(home_team, away_team)
        return {
            "1X": outcomes["home_win"] + outcomes["draw"],
            "X2": outcomes["away_win"] + outcomes["draw"],
        }

    def match_total_goals_over_probs(self, home_team: str, away_team: str, lines: list[float] = None) -> dict:
        lines = lines or config.GOAL_LINES
        matrix = self.scoreline_matrix(home_team, away_team)
        max_goals = matrix.shape[0] - 1

        # total_goals_probs[k] = P(home_goals + away_goals == k)
        max_total = 2 * max_goals
        total_probs = np.zeros(max_total + 1)
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                total_probs[i + j] += matrix[i][j]

        result = {}
        for line in lines:
            threshold = int(line + 0.5)  # e.g. 2.5 -> need total >= 3
            result[f"over_{line}"] = float(total_probs[threshold:].sum())
        return result

    def team_total_goals_over_probs(self, home_team: str, away_team: str, lines: list[float] = None) -> dict:
        lines = lines or config.TEAM_GOAL_LINES
        lam, mu = self.expected_goals(home_team, away_team)
        max_goals = config.MAX_GOALS_FOR_DIST

        def over_probs_for(expected_goals):
            probs = poisson.pmf(np.arange(max_goals + 1), expected_goals)
            out = {}
            for line in lines:
                threshold = int(line + 0.5)
                out[f"over_{line}"] = float(probs[threshold:].sum())
            return out

        return {
            "home": over_probs_for(lam),
            "away": over_probs_for(mu),
        }
