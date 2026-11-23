"""
engine/model/ratings.py - team strength model, one LeagueModel per league.

For each stat (goals, corners, shots, shots on target):
    expected(home) = league_home_avg * home_attack * away_defence
    expected(away) = league_away_avg * away_attack * home_defence
Attack/defence = the team's time-decayed average (newest games count more)
relative to the league average, shrunk toward a prior so a few games cannot
swing it. A newly promoted team is shrunk toward the measured average profile
of past promoted teams (not toward "average"), because they are weaker.

Feed matches in date order with add(); call params_for() BEFORE add() when
backtesting so only past games are used.
"""

from collections import deque, defaultdict
from dataclasses import dataclass, field

STATS = {"g": ("ft_h", "ft_a"), "c": ("co_h", "co_a"), "s": ("sh_h", "sh_a"), "t": ("sot_h", "sot_a")}
DEFAULT_PHI = {"g": 1.0, "c": 1.35, "s": 1.5, "t": 1.4}


@dataclass
class Params:
    mu: dict                       # 'g_h','g_a','c_h',... -> expected value
    share: dict                    # first-half share of goals {'h':..,'a':..}
    phi: dict                      # dispersion per stat
    n_hist: dict = field(default_factory=dict)   # {'h': games, 'a': games}


def promoted_priors(matches: list, before_season: int) -> dict:
    """Average first-season profile of teams promoted into the league, from
    seasons strictly before `before_season`: {stat: (attack, defence)}."""
    if not matches:
        return {}
    first_overall = min(m.season for m in matches)
    first_season = {}
    for m in matches:
        for t in (m.home, m.away):
            first_season.setdefault(t, m.season)
    by_season = defaultdict(list)
    for m in matches:
        by_season[m.season].append(m)
    out = {}
    for stat, (fh, fa) in STATS.items():
        atts, defs = [], []
        for s in range(first_overall + 1, before_season):
            rows = [m for m in by_season[s] if getattr(m, fh) is not None and getattr(m, fa) is not None]
            if len(rows) < 200:
                continue
            lg = sum(getattr(m, fh) + getattr(m, fa) for m in rows) / (2 * len(rows))
            if lg <= 0:
                continue
            for team in {t for t, fs in first_season.items() if fs == s}:
                f = a = n = 0
                for m in rows:
                    if m.home == team:
                        f += getattr(m, fh); a += getattr(m, fa); n += 1
                    elif m.away == team:
                        f += getattr(m, fa); a += getattr(m, fh); n += 1
                if n >= 15:
                    atts.append(f / n / lg); defs.append(a / n / lg)
        if len(atts) >= 3:
            out[stat] = (sum(atts) / len(atts), sum(defs) / len(defs))
    return out


class LeagueModel:
    def __init__(self, phi=None, priors=None, window=760, decay=0.96,
                 prior_n=8.0, promo_n=10.0, established=20, hist_len=40):
        self.phi = {**DEFAULT_PHI, **(phi or {})}
        self.priors = priors or {}
        self.window, self.decay = window, decay
        self.prior_n, self.promo_n = prior_n, promo_n
        self.established, self.hist_len = established, hist_len
        self.hist = defaultdict(lambda: {s: [] for s in STATS})   # team -> stat -> [(for, against)]
        self.count = defaultdict(int)
        self.win = deque()
        self.sum = {s: [0.0, 0.0, 0] for s in STATS}               # sumHome, sumAway, n
        self.ht = [0.0, 0.0, 0.0, 0.0]                              # ht_h, ft_h, ht_a, ft_a (rows with ht)

    # -- streaming updates -------------------------------------------------
    def _apply(self, m, sign):
        for s, (fh, fa) in STATS.items():
            h, a = getattr(m, fh), getattr(m, fa)
            if h is not None and a is not None:
                z = self.sum[s]; z[0] += sign * h; z[1] += sign * a; z[2] += sign
        if m.ht_h is not None and m.ht_a is not None and m.ft_h is not None:
            self.ht[0] += sign * m.ht_h; self.ht[1] += sign * m.ft_h
            self.ht[2] += sign * m.ht_a; self.ht[3] += sign * m.ft_a

    def add(self, m):
        self._apply(m, +1); self.win.append(m)
        if len(self.win) > self.window:
            self._apply(self.win.popleft(), -1)
        for team, side in ((m.home, "h"), (m.away, "a")):
            self.count[team] += 1
            for s, (fh, fa) in STATS.items():
                h, a = getattr(m, fh), getattr(m, fa)
                if h is None or a is None:
                    continue
                self.hist[team][s].append((h, a) if side == "h" else (a, h))
                if len(self.hist[team][s]) > self.hist_len:
                    self.hist[team][s].pop(0)

    # -- prediction ----------------------------------------------------------
    def _ratio(self, team, s, lg, idx, prior):
        P = self.promo_n if (self.count[team] < self.established and s in self.priors) else self.prior_n
        num = den = 0.0
        for i, pair in enumerate(reversed(self.hist[team][s])):
            w = self.decay ** i
            num += w * pair[idx] / lg; den += w
        return (num + P * prior) / (den + P)

    def _prior(self, team, s, idx):
        if self.count[team] < self.established and s in self.priors:
            return self.priors[s][idx]
        return 1.0

    def ready(self) -> bool:
        return self.sum["g"][2] >= 300

    def params_for(self, home: str, away: str) -> Params:
        mu = {}
        for s in STATS:
            n = self.sum[s][2]
            if n < 100:
                continue
            Lh, La = self.sum[s][0] / n, self.sum[s][1] / n
            lg = (Lh + La) / 2
            if lg <= 0:
                continue
            att_h = self._ratio(home, s, lg, 0, self._prior(home, s, 0))
            def_h = self._ratio(home, s, lg, 1, self._prior(home, s, 1))
            att_a = self._ratio(away, s, lg, 0, self._prior(away, s, 0))
            def_a = self._ratio(away, s, lg, 1, self._prior(away, s, 1))
            mu[f"{s}_h"] = Lh * att_h * def_a
            mu[f"{s}_a"] = La * att_a * def_h
        share = {"h": self.ht[0] / self.ht[1] if self.ht[1] > 50 else 0.45,
                 "a": self.ht[2] / self.ht[3] if self.ht[3] > 50 else 0.45}
        return Params(mu=mu, share=share, phi=dict(self.phi),
                      n_hist={"h": self.count[home], "a": self.count[away]})
