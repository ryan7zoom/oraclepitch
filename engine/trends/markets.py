"""
engine/trends/markets.py

The catalogue of markets (everything a trend can be about) and the code that
turns a sequence of past games into hit/miss flags for one market.

Sequences are lists of (Match, side) where `side` ("home"/"away") is the side
the SUBJECT team played in that game. Stat values that are None stay None.

kinds
  result  win / not_lose, on full time, 1st half or 2nd half goals
  team    the subject team's own count over/under a line
  against what the subject team's OPPONENT did in the subject's games (goals conceded, shots
          allowed, ... and the subject losing): evidence for the opponent's bet
  total   both teams added together, over/under a line
  btts    both teams to score yes / no
"""

from dataclasses import dataclass
from typing import List, Optional

from engine.data.model import Match
from engine.trends import settings as S

GROUP_OF_STAT = {
    "goals": "goals", "goals_1h": "goals", "goals_2h": "goals",
    "shots": "shots", "sot": "shots_on_target", "corners": "corners",
    "yellow": "cards", "offsides": "offsides",
}
_COLS = {
    "goals": ("ft_h", "ft_a"), "shots": ("sh_h", "sh_a"), "sot": ("sot_h", "sot_a"),
    "corners": ("co_h", "co_a"), "yellow": ("yc_h", "yc_a"), "offsides": ("of_h", "of_a"),
}


@dataclass(frozen=True)
class Market:
    key: str
    group: str
    kind: str
    stat: str
    direction: str            # over/under, win/not_lose, yes/no
    line: Optional[float] = None

    @property
    def target_kind(self) -> str:
        """The kind of BET this market supports (an 'against' market supports the other team's bet)."""
        if self.kind == "against":
            return "result" if self.direction in ("win", "not_lose") else "team"
        return self.kind

    @property
    def target_key(self) -> str:
        """Key of the bet this market supports: 'against:goals:over:0.5' -> 'team:goals:over:0.5'."""
        if self.kind != "against":
            return self.key
        rest = self.key.split(":", 1)[1]
        return rest if self.direction in ("win", "not_lose") else "team:" + rest

    @property
    def shared(self) -> bool:
        """Same answer for both teams (total / btts): computed once per fixture side."""
        return self.kind in ("total", "btts")


def _directions(stat: str):
    """Over and under, except stats the bookmaker has no under for (settings.NO_UNDER_STATS)."""
    return ("over",) if stat in S.NO_UNDER_STATS else ("over", "under")


def build_catalog() -> List[Market]:
    ms: List[Market] = []
    for stat in ("goals", "goals_1h", "goals_2h"):
        ms.append(Market(f"win:{stat}", "result", "result", stat, "win"))
    ms.append(Market("not_lose", "double_chance", "result", "goals", "not_lose"))
    # evidence from the OTHER side: the subject lost / did not win -> supports the opponent's result bet
    for stat in ("goals", "goals_1h", "goals_2h"):
        ms.append(Market(f"against:win:{stat}", "result", "against", stat, "win"))
    ms.append(Market("against:not_lose", "double_chance", "against", "goals", "not_lose"))
    for stat, lines in S.TEAM_LINES.items():
        for line in lines:
            for d in _directions(stat):
                ms.append(Market(f"team:{stat}:{d}:{line}", GROUP_OF_STAT[stat], "team", stat, d, line))
    for stat, lines in S.CONCEDED_LINES.items():
        for line in lines:
            for d in _directions(stat):
                ms.append(Market(f"against:{stat}:{d}:{line}", GROUP_OF_STAT[stat], "against", stat, d, line))
    for stat, lines in S.TOTAL_LINES.items():
        for line in lines:
            for d in _directions(stat):
                ms.append(Market(f"total:{stat}:{d}:{line}", GROUP_OF_STAT[stat], "total", stat, d, line))
    for d in ("yes", "no"):
        ms.append(Market(f"btts:{d}", "btts", "btts", "goals", d))
    return ms


def _pair(m: Match, stat: str):
    """(home value, away value) of a stat for one match; None when unknown."""
    if stat == "goals_1h":
        return m.ht_h, m.ht_a
    if stat == "goals_2h":
        if None in (m.ht_h, m.ht_a, m.ft_h, m.ft_a):
            return None, None
        return m.ft_h - m.ht_h, m.ft_a - m.ht_a
    h, a = _COLS[stat]
    return getattr(m, h), getattr(m, a)


class SeqData:
    """Per-stat value lists for one sequence, computed once and reused by all markets."""

    def __init__(self, seq):
        self.seq = seq
        self._own, self._other, self._total = {}, {}, {}

    def _ensure(self, stat):
        if stat in self._own:
            return
        own, other, total = [], [], []
        for m, side in self.seq:
            h, a = _pair(m, stat)
            o, p = (h, a) if side == "home" else (a, h)
            own.append(o)
            other.append(p)
            total.append(None if h is None or a is None else h + a)
        self._own[stat], self._other[stat], self._total[stat] = own, other, total

    def own(self, stat):
        self._ensure(stat)
        return self._own[stat]

    def other(self, stat):
        self._ensure(stat)
        return self._other[stat]

    def total(self, stat):
        self._ensure(stat)
        return self._total[stat]


def market_flags(mk: Market, sd: SeqData) -> List[Optional[bool]]:
    if mk.kind == "result":
        o, p = sd.own(mk.stat), sd.other(mk.stat)
        if mk.direction == "win":
            return [None if a is None or b is None else a > b for a, b in zip(o, p)]
        return [None if a is None or b is None else a >= b for a, b in zip(o, p)]
    if mk.kind == "btts":
        o, p = sd.own("goals"), sd.other("goals")
        yes = mk.direction == "yes"
        return [None if a is None or b is None else ((a > 0 and b > 0) == yes)
                for a, b in zip(o, p)]
    if mk.kind == "against" and mk.direction in ("win", "not_lose"):
        o, p = sd.other(mk.stat), sd.own(mk.stat)          # seen from the opponent's side
        if mk.direction == "win":
            return [None if a is None or b is None else a > b for a, b in zip(o, p)]
        return [None if a is None or b is None else a >= b for a, b in zip(o, p)]
    vals = {"team": sd.own, "against": sd.other, "total": sd.total}[mk.kind](mk.stat)
    line = mk.line
    if mk.direction == "over":
        return [None if v is None else v > line for v in vals]
    return [None if v is None else v < line for v in vals]
