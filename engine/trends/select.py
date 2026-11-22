"""
engine/trends/select.py

Turns the raw trend list of a fixture into the few BETS worth showing,
shaped like the reference page: one bet = one line (e.g. "Total corners
Under 13.5"), with every trend that supports that exact line listed under it.

Steps
  1. keep trends whose streak is unusual for the league (p <= MAX_P)
  2. per team/scope/stat/direction keep only the MOST unusual line - this is
     what removes the pile of overlapping lines (over 1.5 / 2.5 / 3.5 ...)
  3. each surviving line becomes a bet; every trend at that line (any team,
     any scope) is attached to it
  4. rank bets by their best p, then take them greedily while respecting
        - a cap per category (settings.BET_CAPS)
        - at most MAX_PER_DIRECTION bets per category/stat/team/direction
        - no contradictions (over 9.5 and under 9.5 total corners, both teams
          to win, BTTS yes with a team not scoring, ...)
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import List

from engine.trends import settings as S
from engine.trends.engine import Trend


@dataclass
class Bet:
    key: str
    group: str
    label: str
    about: str
    kind: str
    stat: str
    direction: str
    line: object
    p: float
    base_rate: object
    trends: List[Trend] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "group": self.group, "label": self.label, "about": self.about,
            "stat": self.stat, "direction": self.direction, "line": self.line,
            "p": float(f"{self.p:.3g}"),
            "base_rate": None if self.base_rate is None else round(self.base_rate, 3),
            "trends": [{"text": t.text, "hits": t.hits, "window": t.window,
                        "scope": t.scope, "subject": t.subject} for t in self.trends],
        }


def _kind(t: Trend) -> str:
    return "team" if t.kind in ("team", "against") else t.kind


def conflicts(a: Bet, b: Bet) -> bool:
    # results: only one team can win / win a given half; a win rules out the other team not losing
    if a.kind == "result" and b.kind == "result" and a.about != b.about:
        wins = {a.direction, b.direction} <= {"win"} and a.stat == b.stat
        win_vs_nolose = ({a.direction, b.direction} == {"win", "not_lose"}
                         and a.stat == b.stat == "goals")
        if wins or win_vs_nolose:
            return True
    # both teams to score: yes and no are opposites; yes needs both teams to be able to score
    if a.kind == "btts" and b.kind == "btts":
        return a.direction != b.direction
    for x, y in ((a, b), (b, a)):
        if (x.kind == "btts" and x.direction == "yes" and y.kind == "team"
                and y.stat == "goals" and y.direction == "under" and y.line <= 0.5):
            return True
    # same count, opposite directions, lines that cannot both be true
    if (a.kind == b.kind and a.kind in ("team", "total") and a.stat == b.stat
            and a.about == b.about and a.direction != b.direction):
        over, under = (a, b) if a.direction == "over" else (b, a)
        return over.line >= under.line - 1
    return False


def select_bets(trends: List[Trend]) -> List[Bet]:
    pool = [t for t in trends if t.p <= S.MAX_P]

    # step 2: most unusual line per team / scope / stat / direction
    def tight(t):                      # higher = tighter line (over: high, under: low)
        return 0 if t.line is None else (t.line if t.direction == "over" else -t.line)

    best = {}
    for t in pool:
        k = (t.subject, t.scope, _kind(t), t.stat, t.direction, t.about)
        if k not in best or (t.p, -tight(t)) < (best[k].p, -tight(best[k])):
            best[k] = t
    wanted = {t.bet_key for t in best.values()}

    # step 3: a bet per surviving line, with every supporting trend attached
    by_key = defaultdict(list)
    for t in pool:
        if t.bet_key in wanted:
            by_key[t.bet_key].append(t)
    bets = []
    for key, ts in by_key.items():
        ts.sort(key=lambda t: (t.p, -t.window))
        t0 = ts[0]
        bets.append(Bet(key=key, group=t0.group, label=t0.bet, about=t0.about, kind=_kind(t0),
                        stat=t0.stat, direction=t0.direction, line=t0.line, p=t0.p,
                        base_rate=t0.base_rate, trends=ts[:S.MAX_TRENDS_PER_BET]))

    # step 4: greedy selection
    bets.sort(key=lambda b: (b.p, -len(b.trends)))
    chosen: List[Bet] = []
    per_group = defaultdict(int)
    per_dir = defaultdict(int)
    for b in bets:
        dkey = (b.group, b.stat, b.about, b.direction)
        if per_group[b.group] >= S.BET_CAPS.get(b.group, 3):
            continue
        if b.line is not None and per_dir[dkey] >= S.MAX_PER_DIRECTION:
            continue
        if any(conflicts(b, c) for c in chosen):
            continue
        chosen.append(b)
        per_group[b.group] += 1
        per_dir[dkey] += 1

    gi = {g: i for i, g in enumerate(S.GROUP_ORDER)}
    chosen.sort(key=lambda b: (gi.get(b.group, 99), b.p))
    return chosen
