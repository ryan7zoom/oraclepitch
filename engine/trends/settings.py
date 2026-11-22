"""
engine/trends/settings.py

Every tunable number of the trend engine lives here.

WHAT COUNTS AS A TREND
----------------------
A trend is a fixed-size sample, shown as "hits/games" (9/12, 7/8, 5/8). For each
team, scope and market the engine looks at the last 8, 10, 12, 15, 20, 30 games
and keeps ONE window: the longest one with a hit rate of at least 70%. Nothing below 70% is shown.

WHAT GETS SHOWN (engine/trends/select.py)
-----------------------------------------
Chalk is removed. The fair-line model (engine/trends/fair.py) says how likely each
bet is for THIS match, given both teams' strength, home/away and the opponent's
level. A bet the model gives more than CHALK_MAX_P is dropped, because that is the
kind of line a bookmaker either will not offer or prices at nothing. What is left is
shown as a ladder per team/stat/direction: the most aggressive lines that still hold,
sorted by hit rate.
"""

MIN_HIT_RATE = 0.70              # floor for any trend row (7/10 or better; 5/8 is no longer shown)
GOOD_HIT_RATE = 0.70             # preferred rate when choosing a window
FORM_WINDOWS = (8, 10, 12, 15, 20, 30)
H2H_WINDOWS = (5, 6, 8, 10)      # meetings of the two teams are rare
H2H_VENUE_WINDOWS = (5, 6, 8)
MAX_SEQ = 60                     # only the newest games of a scope are ever looked at


def _rng(lo: int, hi: int):
    """_rng(0, 4) -> [0.5, 1.5, 2.5, 3.5, 4.5]"""
    return [i + 0.5 for i in range(lo, hi + 1)]


# Lines generated per stat (each one gets BOTH over and under).
TEAM_LINES = {                       # a single team's own count
    "goals": _rng(0, 5),
    "goals_1h": [0.5],               # scored / did not score in the 1st half
    "goals_2h": [0.5],               # scored / did not score in the 2nd half
    "shots": _rng(5, 26),
    "sot": _rng(1, 11),
    "corners": _rng(1, 13),
    "yellow": _rng(0, 5),
    "offsides": _rng(0, 6),
}
# What the subject team ALLOWED its opponents (goals conceded, shots allowed, ...): same lines as
# TEAM_LINES. These are the "other half" of a matching trend (Team A does X / Team B allows X).
CONCEDED_LINES = dict(TEAM_LINES)
TOTAL_LINES = {                      # both teams added together
    "goals": _rng(1, 5),
    "shots": _rng(12, 40),
    "sot": _rng(3, 17),
    "corners": _rng(5, 17),
    "yellow": _rng(1, 8),
    "offsides": _rng(1, 9),
}

LEAGUE_LABELS = {
    "epl": "EPL", "la_liga": "La Liga", "bundesliga": "Bundesliga",
    "serie_a": "Serie A", "ligue_1": "Ligue 1",
}

GROUP_ORDER = ["result", "double_chance", "goals", "btts", "corners",
               "cards", "shots", "shots_on_target", "offsides"]
GROUP_LABELS = {
    "result": "Match result", "double_chance": "Double chance",
    "goals": "Goals", "btts": "Both teams to score", "corners": "Corners",
    "cards": "Yellow cards", "shots": "Shots",
    "shots_on_target": "Shots on target", "offsides": "Offsides",
}


# ---------------------------------------------------------------------------
# Fair-line model (engine/trends/fair.py)
# ---------------------------------------------------------------------------
FAIR_LOOKBACK_DAYS = 730         # about two seasons of league games
FAIR_HALF_LIFE_DAYS = 200        # a game this old counts half as much
FAIR_MIN_ROWS = 150              # fewer league games than this: no model (nothing is filtered)
FAIR_ITERS = 12                  # rounds solving attack/defence ratings together
FAIR_PRIOR_K = 5.0               # ratings: pull toward average (in games-worth)
FAIR_VENUE_K = 10.0              # home/away factor: pull toward 1
FAIR_TIER_K = 12.0               # opponent-level factor: pull toward 1
FAIR_TIER_BW = 0.12              # how alike (log scale) an opponent must be to count as "similar"
# Two optional refinements, both OFF because a backtest over the last season (3,184 matches,
# 5 leagues) showed they made the predictions slightly WORSE on every stat, not better:
#   FAIR_USE_VENUE  team-specific home/away factor on top of the league home/away split
#   FAIR_USE_TIER   re-weight a team's past games by how similar the opponent was
# The opponent adjustment itself (attack x opponent's defence) is always on.
FAIR_USE_VENUE = False
FAIR_USE_TIER = False

# ---------------------------------------------------------------------------
# Selection (engine/trends/select.py)
# ---------------------------------------------------------------------------
# Only bets in this band of the model's own probability are shown:
#   above CHALK_MAX_P  -> chalk (a bookmaker will not offer it / prices it at nothing): dropped
#   below MIN_MODEL_P  -> the matchup says it is a long shot, so a streak is probably a fluke
#                         (e.g. "Barcelona under 9.5 shots" when they are expected to have ~18)
CHALK_MAX_P = 0.85               # default cap on how likely a bet may be (by the model)
# Tighter caps per market, set from real bookmaker lines (Juventus v Venezia), where a bookmaker's
# lowest over / highest under sits at these model chances:
#   shots over ~50-70%, shots on target over ~50-75%, corners over ~65-80%,
#   a team's corners under up to ~66%, match-total corners under up to ~82%.
# Key: (stat, direction, "team" or "total"). Anything not listed uses CHALK_MAX_P.
CHALK_MAX_P_BY_MARKET = {
    ("shots", "over", "team"): 0.70, ("shots", "over", "total"): 0.70,
    ("sot", "over", "team"): 0.75, ("sot", "over", "total"): 0.75,
    ("corners", "over", "team"): 0.80, ("corners", "over", "total"): 0.80,
    ("corners", "under", "team"): 0.70, ("corners", "under", "total"): 0.83,
}
MIN_MODEL_P = 0.25

# Stats where the bookmaker has no Under market: never shown as an under.
NO_UNDER_STATS = ("shots", "sot")

# MATCHING bets: both sides back the same line (Team A does it, the opponent allows it; or
# both teams' own form agrees for totals / BTTS). Each side needs a form row (>= 5/8 over >= 8 games).
MIN_FORM_WINDOW = 8
# GENERAL bets: one side only. Needs a stronger row.
GENERAL_MIN_WINDOW = 10
GENERAL_MIN_RATE = 0.70

# Lines kept per ladder (team/stat/direction): ONE, the line with the best hit rate (e.g. only
# one of Under 4.5 / 3.5 / 2.5, not all three: they are the same games counted again).
LADDER_MAX_LINES = 1
MAX_TRENDS_PER_BET = 4           # trend rows listed under one line

# Max ladders shown per category, per section.
BET_CAPS = {
    "result": 3, "double_chance": 2, "goals": 6, "btts": 1, "corners": 6,
    "cards": 4, "shots": 4, "shots_on_target": 4, "offsides": 3,
}
MATCH_MAX = 99                   # overall cap on matching bets: off. BET_CAPS limits each category instead

# A stale page is flagged after this many hours (data is rebuilt a few times a day).
STALE_AFTER_HOURS = 20

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
DAYS_AHEAD = 3             # today + this many days of fixtures
ESPN_FIXTURE_BUDGET = 60   # ESPN requests per run for fixtures (needs ~5 per day)
TZ_OFFSET_HOURS = 8        # display kickoff times in GMT+8
TRENDS_JSON_PATH = "data/trends/latest.json"
HTML_PATH = "docs/index.html"
