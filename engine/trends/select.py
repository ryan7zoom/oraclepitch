"""
engine/trends/select.py

Turns the raw trend list of a fixture into the few BETS worth showing,
shaped like the reference page: one bet = one line (e.g. "Total corners
Under 13.5"), with every trend that supports that exact line listed under it.

Two kinds of bet, shown in two sections:

  MATCHING - both sides line up. Team A's own streak and the opponent's
  "allowed" streak back the same line (Juventus score / Venezia concede;
  Juventus 5+ shots on target / Venezia allow 5+). For totals and BTTS: both
  teams' own form backs the same line. Each side must pass the streak rule on
  its own (form games only - head-to-head lines are the same games seen from
  both teams, so they cannot count as a second opinion) with luck-probability
  <= MATCH_P, and the two probabilities are multiplied (different teams'
  games): the product must be <= MATCH_JOINT_P.

  GENERAL - one strong streak (p <= MAX_P). Per team/scope/stat/direction only
  the most unusual line is kept, which removes the pile of overlapping lines.

Both sections respect the per-category caps (settings.BET_CAPS), at most
MAX_PER_DIRECTION bets per category/stat/team/direction, and never contradict
each other (over 9.5 and under 9.5 corners, both teams to win, ...).
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
    matching: bool = False

    def to_dict(self) -> dict:
        return {
            "group": self.group, "label": self.label, "about": self.about,
            "stat": self.stat, "direction": self.direction, "line": self.line,
            "matching": self.matching,
            "p": float(f"{self.p:.3g}"),
            "base_rate": None if self.base_rate is None else round(self.base_rate, 3),
            "trends": [{"text": t.text, "hits": t.hits, "window": t.window,
                        "scope": t.scope, "subject": t.subject, "role": t.role} for t in self.trends],
        }


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


def _tight(t: Trend) -> float:                 # higher = tighter line (over: high, under: low)
    return 0 if t.line is None else (t.line if t.direction == "over" else -t.line)


def _make_bet(key: str, ts: List[Trend], p: float, matching: bool, limit: int) -> Bet:
    t0 = ts[0]
    return Bet(key=key, group=t0.group, label=t0.bet, about=t0.about, kind=t0.target_kind,
               stat=t0.stat, direction=t0.direction, line=t0.line, p=p,
               base_rate=t0.base_rate, trends=ts[:limit], matching=matching)


def _is_form(t: Trend) -> bool:
    return t.scope.startswith("form")


def _matching_bets(loose: List[Trend]) -> List[Bet]:
    by_key = defaultdict(list)
    for t in loose:
        by_key[t.bet_key].append(t)
    out = []
    for key, ts in by_key.items():
        shared = ts[0].kind in ("total", "btts")
        roles = ("home", "away") if shared else ("for", "against")
        best = {}
        for t in ts:
            if _is_form(t) and t.role in roles and (t.role not in best or t.p < best[t.role].p):
                best[t.role] = t
        if len(best) < 2:
            continue
        joint = best[roles[0]].p * best[roles[1]].p
        if joint > S.MATCH_JOINT_P:
            continue
        first = [best[roles[0]], best[roles[1]]]
        rest = sorted((t for t in ts if t not in first), key=lambda t: (t.p, -t.window))
        out.append(_make_bet(key, first + rest, joint, True, S.MAX_TRENDS_PER_MATCHING_BET))
    return out


def _general_bets(strict: List[Trend]) -> List[Bet]:
    best = {}
    for t in strict:                          # most unusual line per team / scope / stat / direction
        k = (t.subject, t.scope, t.target_kind, t.stat, t.direction, t.about, t.role)
        if k not in best or (t.p, -_tight(t)) < (best[k].p, -_tight(best[k])):
            best[k] = t
    wanted = {t.bet_key for t in best.values()}
    by_key = defaultdict(list)
    for t in strict:
        if t.bet_key in wanted:
            by_key[t.bet_key].append(t)
    out = []
    for key, ts in by_key.items():
        ts.sort(key=lambda t: (t.p, -t.window))
        out.append(_make_bet(key, ts, ts[0].p, False, S.MAX_TRENDS_PER_BET))
    return out


def _take(bets: List[Bet], chosen: List[Bet], per_group, per_dir, limit=None) -> None:
    taken = 0
    for b in bets:
        if limit is not None and taken >= limit:
            break
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
        taken += 1


def select_bets(trends: List[Trend]) -> List[Bet]:
    """Matching bets first (strongest first), then general bets; each section ordered by category."""
    loose = [t for t in trends if t.p <= S.MATCH_P]
    strict = [t for t in loose if t.p <= S.MAX_P]

    chosen: List[Bet] = []
    per_group, per_dir = defaultdict(int), defaultdict(int)

    matching = sorted(_matching_bets(loose), key=lambda b: (b.p, -len(b.trends)))
    _take(matching, chosen, per_group, per_dir, S.MATCH_MAX)
    taken_keys = {b.key for b in chosen}

    general = sorted((b for b in _general_bets(strict) if b.key not in taken_keys),
                     key=lambda b: (b.p, -len(b.trends)))
    _take(general, chosen, per_group, per_dir)

    gi = {g: i for i, g in enumerate(S.GROUP_ORDER)}
    chosen.sort(key=lambda b: (not b.matching, gi.get(b.group, 99), b.p))
    return chosen
