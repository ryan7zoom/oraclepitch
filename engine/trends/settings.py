"""
engine/trends/settings.py

Every tunable number of the trend engine lives here.

STREAK RULE (inferred from the Scores24 reference page, Barcelona - Villarreal)
-------------------------------------------------------------------------------
The page shows "N of last M" lines. Across the ~80 lines on that page:
  * the smallest window is 5, and every 5-game line is perfect ("in last 5")
  * lines with 1 miss start at M=6 (5 of 6, 6 of 7, ...)
  * lines with 2 misses only appear at M>=15 (13 of 15, 14 of 16, 15 of 17,
    16 of 18, 17 of 19, 24 of 26)
  * no line has 3 or more misses
So the rule used here is a miss allowance that grows with the window:
    window  5        -> 0 misses
    window  6..14    -> up to 1 miss
    window 15 and up -> up to 2 misses
(For each market we report the LONGEST window that passes. Additionally the
oldest game of the window must be a hit, so a window is never padded with a
trailing miss.)  tests/test_trends.py checks that every line of the reference
page passes this rule.
"""

MIN_WINDOW = 5
MAX_WINDOW = 33          # the reference page never goes past 33 games
MISS_TIERS = ((5, 0), (14, 1))     # (window <= x) -> allowed misses
MISS_CAP = 2                       # allowed misses for any longer window

# A line is hidden when the event is this common in the league anyway
# (e.g. "under 5.5 goals" happens ~99% of the time, so a streak means nothing).
BASE_RATE_SEASONS = 3
MAX_BASE_RATE = 0.93
MIN_BASE_SAMPLE = 30


def _rng(lo: int, hi: int):
    """_rng(0, 4) -> [0.5, 1.5, 2.5, 3.5, 4.5]"""
    return [i + 0.5 for i in range(lo, hi + 1)]


# Lines generated per stat (each one gets BOTH over and under).
TEAM_LINES = {                       # a single team's own count
    "goals": _rng(0, 4),
    "goals_1h": [0.5],               # scored / did not score in the 1st half
    "goals_2h": [0.5],               # scored / did not score in the 2nd half
    "shots": _rng(6, 18),
    "sot": _rng(1, 8),
    "corners": _rng(2, 9),
    "yellow": _rng(0, 3),
    "offsides": _rng(0, 4),
}
CONCEDED_LINES = {"goals": _rng(0, 3)}   # goals the subject team CONCEDED
TOTAL_LINES = {                      # both teams added together
    "goals": _rng(1, 5),
    "shots": _rng(15, 30),
    "sot": _rng(4, 13),
    "corners": _rng(6, 14),
    "yellow": _rng(1, 6),
    "offsides": _rng(1, 7),
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
# Tightening (engine/trends/select.py) - mirrors the shape of the reference page:
# ~28 bets per match, each bet = one line with every trend that supports it.
# ---------------------------------------------------------------------------
# A trend only counts if a streak this good would happen by chance (given the
# league-wide rate) less than this often. Lower = fewer, stronger trends.
MAX_P = 0.01

# Max bets shown per category (the reference page: result 3, double chance 1,
# goals 8, BTTS 1, corners 8, cards 3, shots 2, offsides 2).
BET_CAPS = {
    "result": 3, "double_chance": 2, "goals": 8, "btts": 1, "corners": 8,
    "cards": 3, "shots": 3, "shots_on_target": 3, "offsides": 3,
}
MAX_PER_DIRECTION = 2      # per (category, stat, team/total, over|under)
MAX_TRENDS_PER_BET = 4     # trend lines listed under one bet

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
DAYS_AHEAD = 3             # today + this many days of fixtures
ESPN_FIXTURE_BUDGET = 60   # ESPN requests per run for fixtures (needs ~5 per day)
TZ_OFFSET_HOURS = 8        # display kickoff times in GMT+8
TRENDS_JSON_PATH = "data/trends/latest.json"
HTML_PATH = "docs/index.html"
