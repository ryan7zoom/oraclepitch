"""
engine/trends/engine.py

TrendEngine: reads the match history (data/matches/<league>.csv) and, for one
upcoming fixture, returns every trend that passes the streak rule.

For fixture HOME v AWAY it looks at, for each team:
    form_all   all of the team's league games
    form_home  the home team's home games   (form_away: the away team's away games)
    h2h_all    all league meetings of the two teams
    h2h_venue  meetings at this venue (HOME at home v AWAY)
and for every market in the catalogue (win, half wins, double chance, goals,
conceded, BTTS, corners, yellow cards, shots, shots on target, offsides; total
and per team; over AND under for a range of lines) keeps the longest
"N of last M" window that passes engine.trends.rules.

Only games BEFORE the fixture date are used. Unknown stats (None) are skipped,
never counted as zero or as a miss.

Noise control: the base rate - how often the event happens in the league
anyway (last 3 seasons). Near-certain events (>= MAX_BASE_RATE) are dropped, and
the rate plus a luck probability `p` is attached to every trend. This module
returns ALL qualifying trends; engine/trends/select.py picks the few that matter.
"""

import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

from engine.data.model import Match
from engine.data.store import Store
from engine.trends import settings as S
from engine.trends.markets import Market, SeqData, build_catalog, market_flags
from engine.trends.rules import find_window, tail_p
from engine.trends.text import about_side, bet_label, trend_text

SCOPE_ORDER = {"form_all": 0, "form_home": 1, "form_away": 1, "h2h_all": 2, "h2h_venue": 3}
ABOUT_ORDER = {"home": 0, "away": 1, "total": 2}


@dataclass(frozen=True)
class Fixture:
    league: str
    date: date
    home: str
    away: str
    kickoff: Optional[datetime] = None     # aware UTC datetime, when known


@dataclass
class Trend:
    league: str
    home: str
    away: str
    group: str
    market: str               # catalogue key, e.g. "team:corners:under:4.5"
    bet: str                  # e.g. "Villarreal corners Under 4.5"
    about: str                # home | away | total
    subject: str              # team whose games form the sample
    scope: str                # form_all | form_home | form_away | h2h_all | h2h_venue
    stat: str
    kind: str
    direction: str
    line: Optional[float]
    hits: int
    window: int
    misses: int
    base_rate: Optional[float]   # how often this happens in the league anyway (0-1)
    skipped_unknown: int         # games with unknown data skipped inside the window
    text: str
    p: float = 1.0               # chance of a streak this good by luck (league rate); small = unusual
    bet_key: str = ""            # same bet => same key (conceded-goals trends merge into team goals)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["base_rate"] is not None:
            d["base_rate"] = round(d["base_rate"], 3)
        d["p"] = float(f"{d['p']:.3g}")
        return d


def _side_key(scope: str, side: str) -> str:
    return side if scope in ("form_home", "form_away", "h2h_venue") else "both"


class TrendEngine:
    def __init__(self, matches_by_league: Dict[str, List[Match]], markets: Optional[List[Market]] = None):
        self.markets = markets or build_catalog()
        self._by_market = {m.key: m for m in self.markets}
        self.league_matches: Dict[str, List[Match]] = {}
        self.by_team: Dict[str, Dict[str, List[Match]]] = {}
        for league, ms in matches_by_league.items():
            ms = sorted((m for m in ms if m.finished), key=lambda m: m.date, reverse=True)
            self.league_matches[league] = ms
            idx = defaultdict(list)
            for m in ms:
                idx[m.home].append(m)
                idx[m.away].append(m)
            self.by_team[league] = idx
        self._base: Dict[str, dict] = {}

    @classmethod
    def from_dir(cls, directory: str, leagues=None) -> "TrendEngine":
        data = {}
        for league in (leagues or list(S.LEAGUE_LABELS)):
            path = os.path.join(directory, f"{league}.csv")
            if os.path.exists(path):
                store = Store(league, path)
                store.load()
                data[league] = store.rows
        return cls(data)

    def teams(self, league: str):
        return sorted(self.by_team.get(league, {}))

    # ---- base rates ------------------------------------------------------
    def _base_table(self, league: str) -> dict:
        if league not in self._base:
            ms = self.league_matches.get(league, [])
            tab = {}
            if ms:
                latest = max(m.season for m in ms)
                sample = [m for m in ms if m.season > latest - S.BASE_RATE_SEASONS]
                for side in ("home", "away"):
                    sd = SeqData([(m, side) for m in sample])
                    d = {}
                    for mk in self.markets:
                        known = [f for f in market_flags(mk, sd) if f is not None]
                        d[mk.key] = (sum(known), len(known))
                    tab[side] = d
            self._base[league] = tab
        return self._base[league]

    def base_rate(self, league: str, side_key: str, key: str) -> Optional[float]:
        tab = self._base_table(league)
        if not tab:
            return None
        if side_key == "both":
            h = tab["home"][key][0] + tab["away"][key][0]
            k = tab["home"][key][1] + tab["away"][key][1]
        else:
            h, k = tab[side_key][key]
        return h / k if k >= S.MIN_BASE_SAMPLE else None

    # ---- main entry point ------------------------------------------------
    def trends_for_fixture(self, fx: Fixture) -> List[Trend]:
        found: List[Trend] = []
        history = self.by_team.get(fx.league, {})
        for subject, side, opp in ((fx.home, "home", fx.away), (fx.away, "away", fx.home)):
            games = [(m, "home" if m.home == subject else "away")
                     for m in history.get(subject, []) if m.date < fx.date]
            if not games:
                continue
            h2h = [g for g in games if opp in (g[0].home, g[0].away)]
            scopes = {
                "form_all": games,
                f"form_{side}": [g for g in games if g[1] == side],
                "h2h_all": h2h,
                "h2h_venue": [g for g in h2h if g[0].home == fx.home],
            }
            for scope, seq in scopes.items():
                if len(seq) < S.MIN_WINDOW:
                    continue
                sd = SeqData(seq)
                for mk in self.markets:
                    # totals/BTTS are identical for both teams' head-to-head: do it once
                    if mk.shared and scope.startswith("h2h") and side != "home":
                        continue
                    w = find_window(market_flags(mk, sd))
                    if w is None:
                        continue
                    base = self.base_rate(fx.league, _side_key(scope, side), mk.key)
                    if base is not None and base >= S.MAX_BASE_RATE:
                        continue
                    about = about_side(mk, side)
                    about_name = fx.home if about == "home" else fx.away
                    found.append(Trend(
                        league=fx.league, home=fx.home, away=fx.away, group=mk.group,
                        market=mk.key, bet=bet_label(mk, about_name), about=about,
                        subject=subject, scope=scope, stat=mk.stat, kind=mk.kind,
                        direction=mk.direction, line=mk.line, hits=w.hits, window=w.size,
                        misses=w.misses, base_rate=base, skipped_unknown=w.skipped,
                        text=trend_text(mk, subject, opp, side, scope, w.hits, w.size, fx.league),
                        p=tail_p(w.size, w.hits, base),
                        bet_key=f"{mk.key.replace('against:', 'team:', 1)}|{about}",
                    ))
        return self._order(found)

    # ---- post-processing ---------------------------------------------------
    @staticmethod
    def _order(trends: List[Trend]) -> List[Trend]:
        gi = {g: i for i, g in enumerate(S.GROUP_ORDER)}
        return sorted(trends, key=lambda t: (
            gi.get(t.group, 99), ABOUT_ORDER[t.about], t.stat, t.direction,
            t.line if t.line is not None else -1, SCOPE_ORDER[t.scope], -t.window, t.misses))


def build_report(engine: TrendEngine, fixtures: List[Fixture]) -> dict:
    """JSON-ready structure for the dashboard, plus fixtures that were skipped."""
    from engine.trends.select import select_bets
    out, skipped = [], []
    for fx in fixtures:
        known = set(engine.teams(fx.league))
        missing = [t for t in (fx.home, fx.away) if t not in known]
        if missing:
            skipped.append({"league": fx.league, "home": fx.home, "away": fx.away,
                            "reason": f"no match history for {missing}"})
            continue
        raw = engine.trends_for_fixture(fx)
        bets = select_bets(raw)
        out.append({"league": fx.league, "date": fx.date.isoformat(),
                    "kickoff": fx.kickoff.isoformat() if fx.kickoff else None,
                    "home": fx.home, "away": fx.away,
                    "raw_trend_count": len(raw),
                    "bets": [b.to_dict() for b in bets]})
    return {"fixtures": out, "skipped": skipped}
