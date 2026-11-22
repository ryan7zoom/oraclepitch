"""
engine/trends/fair.py

The FAIR LINE model: for one fixture, how many shots / corners / cards / ...
should each team be expected to have, given WHO they are playing?

It exists to answer one question for every trend: "is this line one a bookmaker
would really offer, or is it chalk?"  A streak at a line far below the fair line
(Juventus over 11.5 shots when they are expected to have ~17) is chalk and is
dropped by engine/trends/select.py.

How the number is built (per stat, per league, using the last ~2 seasons with
recent games counting more):

  1. Team ratings. Every team gets an ATTACK rating (what it produces) and a
     DEFENCE rating (what it allows), relative to the league average. The two
     are solved together, so beating weak opponents does not inflate an attack
     and facing strong attacks does not make a defence look bad.
  2. Home / away. The league's own home and away averages are the baseline.
  3. Two optional refinements, OFF by default (settings.FAIR_USE_VENUE / FAIR_USE_TIER)
     because a backtest showed them making predictions slightly worse:
       - a team-specific home/away factor
       - an opponent-quality ("tier") factor: how does the attack do against defences
         LIKE tonight's opponent, and how does tonight's defence do against attacks LIKE
         tonight's attacker? Past games are weighted by how similar that opponent was
         (a smooth version of "top third / bottom third"), shrunk toward the overall rating.

  expected = league average at that venue
             x attack(team) x defence(opponent)       [x optional factors]

Counts are not Poisson (shots and corners vary more than that), so each stat gets
a dispersion measured from the league's own data and probabilities come from a
negative binomial. These give P(over a line) for every bet, and the win / draw /
both-teams-to-score probabilities from the goals model.

Nothing here uses odds, and only games BEFORE the fixture date are used.
"""

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from engine.trends import settings as S
from engine.trends.markets import _pair

FIT_STATS = ("goals", "shots", "sot", "corners", "yellow", "offsides")

_CLIP_VENUE = (0.80, 1.25)
_CLIP_TIER = (0.85, 1.18)
_GOAL_CAP = 14


# ---------------------------------------------------------------------------
# distributions
# ---------------------------------------------------------------------------
def pmf(mu: float, alpha: float, upto: int) -> List[float]:
    """P(X = 0..upto) for mean `mu`; alpha = extra variance (var = mu + alpha*mu^2); 0 = Poisson."""
    mu = max(mu, 1e-9)
    out = []
    if alpha < 1e-6:
        p = math.exp(-mu)
        for k in range(upto + 1):
            out.append(p)
            p *= mu / (k + 1)
        return out
    r = 1.0 / alpha
    q = r / (r + mu)
    p = q ** r
    for k in range(upto + 1):
        out.append(p)
        p *= (k + r) / (k + 1) * (1 - q)
    return out


def p_over(mu: float, alpha: float, line: float) -> float:
    """P(X > line) for a half-point line such as 11.5."""
    k = int(math.floor(line))
    return max(0.0, min(1.0, 1.0 - sum(pmf(mu, alpha, k))))


# ---------------------------------------------------------------------------
# one stat, one league, one date
# ---------------------------------------------------------------------------
class StatFit:
    def __init__(self, rows: List[tuple]):
        """rows: (home, away, home_value, away_value, weight)"""
        self.rows = rows
        wsum = sum(r[4] for r in rows) or 1.0
        self.base = {"home": sum(r[4] * r[2] for r in rows) / wsum,
                     "away": sum(r[4] * r[3] for r in rows) / wsum}
        self.mean_base = (self.base["home"] + self.base["away"]) / 2 or 1e-9
        teams = {r[0] for r in rows} | {r[1] for r in rows}
        self.att = {t: 1.0 for t in teams}
        self.dfn = {t: 1.0 for t in teams}
        self._solve_ratings()
        self.vfa = {t: {"home": 1.0, "away": 1.0} for t in teams}
        self.vfd = {t: {"home": 1.0, "away": 1.0} for t in teams}
        if S.FAIR_USE_VENUE:
            self._solve_venue()
        self._index_games()
        self.alpha = self._dispersion()

    # -- ratings -----------------------------------------------------------
    def _solve_ratings(self):
        K, b = S.FAIR_PRIOR_K, self.mean_base
        bh, ba = self.base["home"], self.base["away"]
        for _ in range(S.FAIR_ITERS):
            na, da, nd, dd = (defaultdict(float) for _ in range(4))
            for h, a, hv, av, w in self.rows:
                na[h] += w * hv
                da[h] += w * bh * self.dfn[a]
                na[a] += w * av
                da[a] += w * ba * self.dfn[h]
                nd[a] += w * hv
                dd[a] += w * bh * self.att[h]
                nd[h] += w * av
                dd[h] += w * ba * self.att[a]
            att = {t: (na[t] + K * b) / (da[t] + K * b) for t in self.att}
            dfn = {t: (nd[t] + K * b) / (dd[t] + K * b) for t in self.dfn}
            ma, md = sum(att.values()) / len(att), sum(dfn.values()) / len(dfn)
            self.att = {t: v / ma for t, v in att.items()}
            self.dfn = {t: v / md for t, v in dfn.items()}

    def _core(self, team: str, side: str, opp: str) -> float:
        other = "away" if side == "home" else "home"
        return (self.base[side] * self.att.get(team, 1.0) * self.dfn.get(opp, 1.0)
                * self.vfa.get(team, {}).get(side, 1.0) * self.vfd.get(opp, {}).get(other, 1.0))

    def _solve_venue(self):
        K, b = S.FAIR_VENUE_K, self.mean_base
        na, da, nd, dd = (defaultdict(float) for _ in range(4))
        for h, a, hv, av, w in self.rows:
            eh = self.base["home"] * self.att[h] * self.dfn[a]
            ea = self.base["away"] * self.att[a] * self.dfn[h]
            na[(h, "home")] += w * hv
            da[(h, "home")] += w * eh
            na[(a, "away")] += w * av
            da[(a, "away")] += w * ea
            nd[(a, "away")] += w * hv        # a defended away, conceded hv
            dd[(a, "away")] += w * eh
            nd[(h, "home")] += w * av
            dd[(h, "home")] += w * ea
        lo, hi = _CLIP_VENUE
        for (t, side), n in na.items():
            self.vfa[t][side] = min(hi, max(lo, (n + K * b) / (da[(t, side)] + K * b)))
        for (t, side), n in nd.items():
            self.vfd[t][side] = min(hi, max(lo, (n + K * b) / (dd[(t, side)] + K * b)))

    # -- per-game expectations (without the tier factor), for tier + dispersion ----
    def _index_games(self):
        self.for_games = defaultdict(list)      # team -> [(x, E, w, log defence of opponent faced)]
        self.against_games = defaultdict(list)  # team -> [(x, E, w, log attack of attacker faced)]
        self._e_all = []
        for h, a, hv, av, w in self.rows:
            eh, ea = self._core(h, "home", a), self._core(a, "away", h)
            self.for_games[h].append((hv, eh, w, math.log(self.dfn[a])))
            self.for_games[a].append((av, ea, w, math.log(self.dfn[h])))
            self.against_games[a].append((hv, eh, w, math.log(self.att[h])))
            self.against_games[h].append((av, ea, w, math.log(self.att[a])))
            self._e_all.append((hv, eh, w))
            self._e_all.append((av, ea, w))

    def _dispersion(self) -> float:
        num = sum(w * ((x - e) ** 2 - e) for x, e, w in self._e_all)
        den = sum(w * e * e for x, e, w in self._e_all) or 1.0
        return min(0.6, max(0.0, num / den))

    # -- tier ------------------------------------------------------------------
    def _tier(self, games, target_log: float) -> float:
        if not games:
            return 1.0
        K, bw = S.FAIR_TIER_K, S.FAIR_TIER_BW
        b = sum(w * e for _, e, w, _ in games) / (sum(w for _, _, w, _ in games) or 1.0)
        nx = de = 0.0
        for x, e, w, lg in games:
            u = math.exp(-0.5 * ((lg - target_log) / bw) ** 2)
            nx += w * u * x
            de += w * u * e
        lo, hi = _CLIP_TIER
        return min(hi, max(lo, (nx + K * b) / (de + K * b)))

    def mu(self, team: str, side: str, opp: str) -> float:
        """Expected value of the stat for `team` playing `opp`, `team` being on `side`."""
        if team not in self.att or opp not in self.att or not S.FAIR_USE_TIER:
            return self._core(team, side, opp)
        t_att = self._tier(self.for_games[team], math.log(self.dfn[opp]))
        t_dfn = self._tier(self.against_games[opp], math.log(self.att[team]))
        return self._core(team, side, opp) * t_att * t_dfn


# ---------------------------------------------------------------------------
# the model for one league on one date
# ---------------------------------------------------------------------------
class FairModel:
    def __init__(self, matches, as_of: date):
        """matches: finished Match objects of ONE league (any order)."""
        floor = as_of - timedelta(days=S.FAIR_LOOKBACK_DAYS)
        use = [m for m in matches if floor <= m.date < as_of]
        self.n_matches = len(use)
        self.fits: Dict[str, Optional[StatFit]] = {}
        for stat in FIT_STATS:
            rows = []
            for m in use:
                hv, av = _pair(m, stat)
                if hv is None or av is None:
                    continue
                age = (as_of - m.date).days
                rows.append((m.home, m.away, hv, av, 0.5 ** (age / S.FAIR_HALF_LIFE_DAYS)))
            self.fits[stat] = StatFit(rows) if len(rows) >= S.FAIR_MIN_ROWS else None
        ht = sum(m.ht_h + m.ht_a for m in use if m.ht_h is not None and m.ht_a is not None)
        ft = sum(m.ft_h + m.ft_a for m in use if m.ht_h is not None and m.ht_a is not None)
        self.first_half_share = ht / ft if ft else 0.45

    def expect(self, home: str, away: str) -> Optional["Expect"]:
        if self.n_matches < S.FAIR_MIN_ROWS or self.fits.get("goals") is None:
            return None
        mu_h, mu_a, alpha = {}, {}, {}
        for stat, fit in self.fits.items():
            if fit is None:
                continue
            mu_h[stat] = fit.mu(home, "home", away)
            mu_a[stat] = fit.mu(away, "away", home)
            alpha[stat] = fit.alpha
        return Expect(mu_h, mu_a, alpha, self.first_half_share)


class Expect:
    """Expected values and bet probabilities for ONE fixture."""

    def __init__(self, mu_h, mu_a, alpha, h1_share):
        self.mu_h, self.mu_a, self.alpha, self.h1_share = mu_h, mu_a, alpha, h1_share

    # -- counts --------------------------------------------------------------
    def mu(self, stat: str, about: str) -> Optional[float]:
        if stat not in self.mu_h:
            return None
        h, a = self.mu_h[stat], self.mu_a[stat]
        return h if about == "home" else a if about == "away" else h + a

    def _alpha(self, stat: str, about: str) -> float:
        al = self.alpha.get(stat, 0.0)
        if about != "total":
            return al
        h, a = self.mu_h[stat], self.mu_a[stat]
        # two independent counts with the same dispersion: variance adds, so the sum is a bit tighter
        return al * (h * h + a * a) / ((h + a) ** 2)

    def prob_over(self, stat: str, about: str, line: float) -> Optional[float]:
        if stat in ("goals_1h", "goals_2h"):
            share = self.h1_share if stat == "goals_1h" else 1 - self.h1_share
            m = self.mu("goals", about)
            return None if m is None else p_over(m * share, 0.0, line)
        m = self.mu(stat, about)
        return None if m is None else p_over(m, self._alpha(stat, about), line)

    # -- results -------------------------------------------------------------
    def _score_grid(self, share: float = 1.0):
        ph = pmf(self.mu_h["goals"] * share, self.alpha["goals"] if share == 1.0 else 0.0, _GOAL_CAP)
        pa = pmf(self.mu_a["goals"] * share, self.alpha["goals"] if share == 1.0 else 0.0, _GOAL_CAP)
        return ph, pa

    def p_result(self, about: str, direction: str, stat: str) -> Optional[float]:
        if "goals" not in self.mu_h:
            return None
        share = {"goals": 1.0, "goals_1h": self.h1_share, "goals_2h": 1 - self.h1_share}[stat]
        ph, pa = self._score_grid(share)
        win_h = sum(ph[i] * pa[j] for i in range(len(ph)) for j in range(i))
        win_a = sum(ph[i] * pa[j] for j in range(len(pa)) for i in range(j))
        draw = sum(ph[i] * pa[i] for i in range(len(ph)))
        if direction == "win":
            return win_h if about == "home" else win_a
        return (win_h if about == "home" else win_a) + draw

    def p_btts(self, yes: bool) -> Optional[float]:
        if "goals" not in self.mu_h:
            return None
        ph, pa = self._score_grid()
        p = (1 - ph[0]) * (1 - pa[0])
        return p if yes else 1 - p


def bet_probability(ex: Optional[Expect], mk, about: str) -> Optional[float]:
    """Model probability that the BET a market supports lands (None when unknown)."""
    if ex is None:
        return None
    if mk.kind == "result" or (mk.kind == "against" and mk.direction in ("win", "not_lose")):
        return ex.p_result(about, mk.direction, mk.stat)
    if mk.kind == "btts":
        return ex.p_btts(mk.direction == "yes")
    p = ex.prob_over(mk.stat, about, mk.line)
    if p is None:
        return None
    return p if mk.direction == "over" else 1.0 - p
