"""
engine/trends/select.py

Turns the raw trend list of a fixture into the BETS worth showing.

  1. CHALK IS REMOVED. Every trend carries the fair-line model's chance for the bet it
     supports (engine/trends/fair.py). Above settings.CHALK_MAX_P the bet is dropped: a
     line that lands that often is one a bookmaker will not offer or prices at nothing.
     Below settings.MIN_MODEL_P it is dropped too: the matchup says it is a long shot, so
     a streak behind it is more likely a fluke than a pattern.

  2. One bet = one line (e.g. "Juventus shots Over 17.5") with the trends behind it,
     each shown as hits/games.

  Inside every category the bets run from the most relevant to the least relevant that still
  passed the filters. Relevance = half the rows' hit rate (big samples trusted more), half the
  fair-line model's chance for this match.

  3. Lines of the same team / stat / direction form a LADDER. Only the best LADDER_MAX_LINES
     (one) is kept, the line with the best hit rate, so the same games are not shown again
     at a neighbouring line. Rows inside a bet go from best to worst.

Two sections:

  MATCHING - both sides line up: Team A's own form and the opponent's "allowed" form back
  the same line (Juventus shots / Venezia allow shots). For totals and BTTS: both teams' own
  form backs it. Each side needs a form row (head-to-head rows are the same games seen from
  both teams, so they cannot count as a second opinion).

  GENERAL - one side has a strong row (>= 7/10) and the other does not back it.

Both sections respect per-category ladder caps and never contradict each other.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional

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
    strength: float                  # relevance score used for ordering: best (highest) first
    fair: Optional[float] = None     # fair line (expected count), when the model has one
    trends: List[Trend] = field(default_factory=list)
    matching: bool = False
    ladder: str = ""

    def to_dict(self) -> dict:
        return {
            "group": self.group, "label": self.label, "about": self.about,
            "stat": self.stat, "direction": self.direction, "line": self.line,
            "matching": self.matching,
            "fair": None if self.fair is None else round(self.fair, 1),
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


def chalk_cap(stat: str, direction: str = "over", about: str = "home") -> float:
    """Highest chance (by the model) a bet may have before it counts as chalk, for this market."""
    scope = "total" if about == "total" else "team"
    return S.CHALK_MAX_P_BY_MARKET.get((stat, direction, scope), S.CHALK_MAX_P)


def rate(t: Trend) -> float:
    return t.hits / t.window


def _is_form(t: Trend) -> bool:
    return t.scope.startswith("form")


def shrunk(t: Trend) -> float:
    """Hit rate that trusts big samples more: 28/30 counts for more than 8/8."""
    return (t.hits + 2) / (t.window + 4)


def _row_order(t: Trend):
    return (-shrunk(t), -t.window)


def relevance(rows: List[Trend]) -> float:
    """How likely the bet is to hit, by our numbers: half the trend rows' (size-adjusted) hit rate,
    half the model's chance for THIS match. With no model, the hit rate alone."""
    hist = sum(shrunk(t) for t in rows) / len(rows)
    p = rows[0].model_p
    return hist if p is None else 0.5 * hist + 0.5 * p


def _aggressive(b: Bet) -> float:
    """Higher = more aggressive line (overs: high line, unders: low line)."""
    if b.line is None:
        return 0.0
    return b.line if b.direction == "over" else -b.line


def _make_bet(key: str, ts: List[Trend], first: List[Trend], strength: float, matching: bool) -> Bet:
    t0 = ts[0]
    rest = sorted((t for t in ts if t not in first), key=_row_order)
    return Bet(key=key, group=t0.group, label=t0.bet, about=t0.about, kind=t0.target_kind,
               stat=t0.stat, direction=t0.direction, line=t0.line, strength=strength,
               fair=t0.fair, trends=(first + rest)[:S.MAX_TRENDS_PER_BET], matching=matching,
               ladder=_ladder_key(t0))


def _ladder_key(t: Trend) -> str:
    if t.line is None or t.target_kind not in ("team", "total"):
        return f"{t.bet_key}"
    return f"{t.about}|{t.target_kind}|{t.stat}|{t.direction}"


def _candidates(trends: List[Trend]):
    """(matching bets, general bets) after the chalk filter."""
    kept = [t for t in trends
            if t.model_p is None or S.MIN_MODEL_P <= t.model_p <= chalk_cap(t.stat, t.direction, t.about)]
    by_key = defaultdict(list)
    for t in kept:
        by_key[t.bet_key].append(t)
    matching, general = [], []
    for key, ts in by_key.items():
        form = [t for t in ts if _is_form(t) and t.window >= S.MIN_FORM_WINDOW]
        shared = ts[0].kind in ("total", "btts")
        roles = ("home", "away") if shared else ("for", "against")
        best = {}
        for t in form:
            if t.role in roles and (t.role not in best or _row_order(t) < _row_order(best[t.role])):
                best[t.role] = t
        if len(best) == 2:
            first = [best[roles[0]], best[roles[1]]]
            matching.append(_make_bet(key, ts, first, relevance(first), True))
            continue
        strong = [t for t in form if t.window >= S.GENERAL_MIN_WINDOW and rate(t) >= S.GENERAL_MIN_RATE]
        if strong:
            top = min(strong, key=_row_order)
            general.append(_make_bet(key, ts, [top], relevance([top]), False))
    return matching, general


def _ladders(bets: List[Bet]) -> List[List[Bet]]:
    """Group bets into ladders; keep the most aggressive lines; list them best hit rate first."""
    by_ladder = defaultdict(list)
    for b in bets:
        by_ladder[b.ladder].append(b)
    out = []
    for lines in by_ladder.values():
        lines.sort(key=lambda b: (-b.strength, -_aggressive(b)))   # best hit rate first
        lines = lines[:S.LADDER_MAX_LINES]
        out.append(lines)
    out.sort(key=lambda ls: -ls[0].strength)
    return out


def _take(ladders: List[List[Bet]], chosen: List[Bet], per_group, limit=None) -> None:
    taken = 0
    for lines in ladders:
        if limit is not None and taken >= limit:
            break
        g = lines[0].group
        if per_group[g] >= S.BET_CAPS.get(g, 3):
            continue
        ok = [b for b in lines if not any(conflicts(b, c) for c in chosen)]
        ok = [b for i, b in enumerate(ok) if not any(conflicts(b, o) for o in ok[:i])]
        if not ok:
            continue
        chosen.extend(ok)
        per_group[g] += 1
        taken += 1


def select_bets(trends: List[Trend]) -> List[Bet]:
    """Matching ladders first, then general ones; each section ordered by category, then strength."""
    matching, general = _candidates(trends)
    chosen: List[Bet] = []
    per_group = defaultdict(int)

    _take(_ladders(matching), chosen, per_group, S.MATCH_MAX)
    # a ladder already shown as a matching bet is not shown again, at another line, as a general one
    taken_ladders = {b.ladder for b in chosen}
    _take(_ladders([b for b in general if b.ladder not in taken_ladders]), chosen, per_group)

    gi = {g: i for i, g in enumerate(S.GROUP_ORDER)}
    best_in_ladder = defaultdict(float)
    for b in chosen:
        best_in_ladder[(b.matching, b.ladder)] = max(best_in_ladder[(b.matching, b.ladder)], b.strength)
    chosen.sort(key=lambda b: (not b.matching, gi.get(b.group, 99),
                               -best_in_ladder[(b.matching, b.ladder)], b.ladder, -b.strength,
                               -_aggressive(b)))
    return chosen
