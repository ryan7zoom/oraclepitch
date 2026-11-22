"""
engine/trends/engine.py

TrendEngine: reads the match history (data/matches/<league>.csv) and, for one
upcoming fixture, returns every trend that holds up as "hits/games".

For fixture HOME v AWAY it looks at, for each team:
    form_all   all of the team's league games
    form_home  the home team's home games   (form_away: the away team's away games)
    h2h_all    all league meetings of the two teams
    h2h_venue  meetings at this venue (HOME at home v AWAY)
and for every market in the catalogue (win, half wins, double chance, goals,
conceded, BTTS, corners, yellow cards, shots, shots on target, offsides; total
and per team; over AND under for a range of lines) keeps one fixed window per
scope (see engine.trends.rules).

Only games BEFORE the fixture date are used. Unknown stats (None) are skipped,
never counted as zero or as a miss.

Chalk control: the fair-line model (engine.trends.fair) gives every trend the
probability of the bet it supports and, for count bets, the fair line. This module
returns ALL qualifying trends with those numbers attached;
engine/trends/select.py drops the chalk and picks what to show.
"""

import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

from engine.data.model import Match
from engine.data.store import Store
from engine.trends import settings as S
from engine.trends.names import display as D
from engine.trends.fair import FairModel, bet_probability
from engine.trends.markets import Market, SeqData, build_catalog, market_flags
from engine.trends.rules import pick_window
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
    skipped_unknown: int         # games with unknown data skipped inside the window
    text: str
    model_p: Optional[float] = None   # fair-line model: chance the bet lands in THIS match (None = no model)
    fair: Optional[float] = None      # fair line (expected count) for count bets
    bet_key: str = ""            # same bet => same key ("allowed" trends merge into the other team's bet)
    role: str = ""               # for | against (team bets) ; home | away (total / BTTS bets)
    target_kind: str = ""        # kind of the bet this trend supports (result | team | total | btts)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["model_p"] is not None:
            d["model_p"] = round(d["model_p"], 3)
        if d["fair"] is not None:
            d["fair"] = round(d["fair"], 2)
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
        self._fair: Dict[tuple, FairModel] = {}

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

    # ---- fair-line model -------------------------------------------------
    def fair_model(self, league: str, as_of: date) -> FairModel:
        key = (league, as_of)
        if key not in self._fair:
            self._fair[key] = FairModel(self.league_matches.get(league, []), as_of)
        return self._fair[key]

    # ---- main entry point ------------------------------------------------
    def trends_for_fixture(self, fx: Fixture) -> List[Trend]:
        found: List[Trend] = []
        history = self.by_team.get(fx.league, {})
        expect = self.fair_model(fx.league, fx.date).expect(fx.home, fx.away)
        for subject, side, opp in ((fx.home, "home", fx.away), (fx.away, "away", fx.home)):
            games = [(m, "home" if m.home == subject else "away")
                     for m in history.get(subject, []) if m.date < fx.date]
            if not games:
                continue
            h2h = [g for g in games if opp in (g[0].home, g[0].away)]
            scopes = {
                "form_all": (games, S.FORM_WINDOWS),
                f"form_{side}": ([g for g in games if g[1] == side], S.FORM_WINDOWS),
                "h2h_all": (h2h, S.H2H_WINDOWS),
                "h2h_venue": ([g for g in h2h if g[0].home == fx.home], S.H2H_VENUE_WINDOWS),
            }
            for scope, (seq, windows) in scopes.items():
                seq = seq[:S.MAX_SEQ]
                if len(seq) < windows[0]:
                    continue
                sd = SeqData(seq)
                for mk in self.markets:
                    # totals/BTTS are identical for both teams' head-to-head: do it once
                    if mk.shared and scope.startswith("h2h") and side != "home":
                        continue
                    w = pick_window(market_flags(mk, sd), windows)
                    if w is None:
                        continue
                    about = about_side(mk, side)
                    about_name = fx.home if about == "home" else fx.away
                    fair = None
                    if expect is not None and mk.line is not None and mk.kind != "result":
                        fair = expect.mu(mk.stat, about)
                    found.append(Trend(
                        league=fx.league, home=fx.home, away=fx.away, group=mk.group,
                        market=mk.key, bet=bet_label(mk, D(about_name)), about=about,
                        subject=subject, scope=scope, stat=mk.stat, kind=mk.kind,
                        direction=mk.direction, line=mk.line, hits=w.hits, window=w.size,
                        misses=w.misses, skipped_unknown=w.skipped,
                        text=trend_text(mk, D(subject), D(opp), side, scope, w.hits, w.size, fx.league),
                        model_p=bet_probability(expect, mk, about), fair=fair,
                        bet_key=f"{mk.target_key}|{about}",
                        role=side if mk.shared else ("against" if mk.kind == "against" else "for"),
                        target_kind=mk.target_kind,
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
