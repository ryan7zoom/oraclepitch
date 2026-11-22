"""
engine/trends/text.py

Human-readable wording, in the style of the reference page:
    "Barcelona won in 15 of last 16 home games (La Liga)"
    "Over 9.5 corners in 7 of last 8 Barcelona home games (La Liga)"
"""

from engine.trends import settings as S
from engine.trends.markets import Market

STAT_NAME = {
    "goals": "goals", "goals_1h": "1st-half goals", "goals_2h": "2nd-half goals",
    "shots": "shots", "sot": "shots on target", "corners": "corners",
    "yellow": "yellow cards", "offsides": "offsides",
}
_VERB = {"goals": "scored", "shots": "had", "sot": "had", "corners": "took",
         "yellow": "had", "offsides": "had"}
_HALF = {"goals_1h": "1st half", "goals_2h": "2nd half"}


def num(x) -> str:
    return f"{x:g}"


def condition(mk: Market, name: str) -> str:
    """What happened, e.g. 'Barcelona won' / 'Under 13.5 corners'."""
    over = mk.direction == "over"
    if mk.kind == "result":
        if mk.direction == "not_lose":
            return f"{name} did not lose"
        return {"goals": f"{name} won", "goals_1h": f"{name} won the 1st half",
                "goals_2h": f"{name} won the 2nd half"}[mk.stat]
    if mk.kind == "btts":
        return "Both teams scored" if mk.direction == "yes" else "At least one team did not score"
    if mk.kind == "total":
        return f"{'Over' if over else 'Under'} {num(mk.line)} {STAT_NAME[mk.stat]}"
    if mk.kind == "against":
        if mk.direction == "win":
            return {"goals": f"{name} lost", "goals_1h": f"{name} lost the 1st half",
                    "goals_2h": f"{name} lost the 2nd half"}[mk.stat]
        if mk.direction == "not_lose":
            return f"{name} did not win"
        d = "over" if over else "under"
        if mk.stat in ("goals", "goals_1h", "goals_2h"):
            when = f" in the {_HALF[mk.stat]}" if mk.stat in _HALF else ""
            if mk.line == 0.5:
                return (f"{name} conceded{when}" if over else
                        (f"{name} did not concede{when}" if when else f"{name} kept a clean sheet"))
            return f"{name} conceded {d} {num(mk.line)} goals"
        if mk.stat in ("shots", "sot", "corners"):
            return f"{name} allowed {d} {num(mk.line)} {STAT_NAME[mk.stat]}"
        return f"{name}'s opponents had {d} {num(mk.line)} {STAT_NAME[mk.stat]}"
    # team
    if mk.stat in _HALF:
        return (f"{name} scored in the {_HALF[mk.stat]}" if over
                else f"{name} did not score in the {_HALF[mk.stat]}")
    if mk.stat == "goals" and mk.line == 0.5:
        return f"{name} scored" if over else f"{name} failed to score"
    return f"{name} {_VERB[mk.stat]} {'over' if over else 'under'} {num(mk.line)} {STAT_NAME[mk.stat]}"


def about_side(mk: Market, side: str) -> str:
    """Which side of the fixture the bet is about: home / away / total."""
    if mk.shared:
        return "total"
    if mk.kind == "against":
        return "away" if side == "home" else "home"
    return side


def bet_label(mk: Market, name: str) -> str:
    """The bet the trend supports. `name` = team the bet is about (ignored for totals)."""
    over = mk.direction == "over"
    if mk.kind == "result" or (mk.kind == "against" and mk.direction in ("win", "not_lose")):
        if mk.direction == "not_lose":
            return f"{name} not to lose"
        return {"goals": f"{name} to win", "goals_1h": f"{name} to win the 1st half",
                "goals_2h": f"{name} to win the 2nd half"}[mk.stat]
    if mk.kind == "btts":
        return f"Both teams to score - {'Yes' if mk.direction == 'yes' else 'No'}"
    d = "Over" if over else "Under"
    if mk.kind == "total":
        return f"Total {STAT_NAME[mk.stat]} {d} {num(mk.line)}"
    if mk.stat in _HALF:
        return (f"{name} to score in the {_HALF[mk.stat]}" if over
                else f"{name} not to score in the {_HALF[mk.stat]}")
    if mk.stat == "goals" and mk.line == 0.5:
        return f"{name} to score" if over else f"{name} not to score"
    return f"{name} {STAT_NAME[mk.stat]} {d} {num(mk.line)}"


def scope_label(scope: str, subject: str, opp: str, side: str, shared: bool) -> str:
    where = {
        "form_all": "games", "form_home": "home games", "form_away": "away games",
        "h2h_all": f"games against {opp}",
        "h2h_venue": f"{'home' if side == 'home' else 'away'} games against {opp}",
    }[scope]
    return f"{subject} {where}" if shared else where


def trend_text(mk: Market, subject: str, opp: str, side: str, scope: str,
               hits: int, size: int, league: str) -> str:
    span = f"in last {size}" if hits == size else f"in {hits} of last {size}"
    where = scope_label(scope, subject, opp, side, mk.shared)
    return f"{condition(mk, subject)} {span} {where} ({S.LEAGUE_LABELS.get(league, league)})"
