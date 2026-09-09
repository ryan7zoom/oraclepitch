"""
config.py

Central configuration for the EPL trend-surfacing dashboard.

REWRITTEN FOR THE PROJECT PIVOT: this system no longer predicts match
outcomes. Dixon-Coles, Monte Carlo, Kelly Criterion, the ensemble
predictor, and the backtest engine were all deleted (see git history
for the full removal) - the user's stated edge is personal football
judgment, not statistical modeling, and a real backtest showed the
old prediction model, while well-calibrated, had no exploitable edge
over bookmaker odds anyway. What remains is a trend/streak surfacing
tool: it shows recent-form and head-to-head statistical patterns for
upcoming fixtures so the user can apply their own judgment, and never
recommends bets or stake sizes.
"""

# ---------------------------------------------------------------------------
# League
# ---------------------------------------------------------------------------
LEAGUE_NAME = "English Premier League"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
PREDICTIONS_JSON_PATH = "data/predictions/latest.json"
HTML_OUTPUT_PATH = "docs/index.html"
HISTORICAL_DATA_DIR = "data/historical"

# ---------------------------------------------------------------------------
# Streak analysis (double-streak mismatch dashboard)
# ---------------------------------------------------------------------------
# See engine/streaks/analyzer.py's module docstring for the full design
# rationale on "for"/"against"/H2H streaks and mismatch detection.
#
# NOTE: "goals_conceded" is kept here even though it wasn't listed in
# the pivot spec's example config - it was already implemented and
# tested (engine/streaks/analyzer.py's get_stat_value()/
# get_opponent_stat_value() both handle it), and removing a working,
# tested stat category for no functional reason would just be
# needless deletion. If it turns out to be unwanted noise in the
# dashboard, it's a one-line removal from this dict, not a design
# problem.
STREAK_THRESHOLDS = {
    "shots_on_target": [3, 4, 5, 6],
    "corners": [5, 6, 7, 8, 9],
    "goals": [1, 2, 3],
    "goals_conceded": [1, 2, 3],
}

# Window sizes for single-team recent-form streaks. A mix of short
# (noisy but recent) and long (stable but slower to react) windows,
# since different streak lengths surface different patterns.
STREAK_WINDOWS = [5, 6, 7, 8, 10, 13, 18]

# Window sizes for head-to-head streaks specifically (separate from
# STREAK_WINDOWS since H2H meetings between two specific teams are far
# less frequent than each team's overall match list).
H2H_WINDOWS = [5, 10, 15]

# A double-streak mismatch requires BOTH the "for" streak and the
# "against" streak to independently clear this percentage before being
# considered at all.
MISMATCH_MIN_PERCENTAGE = 0.60

# Minimum number of matches with usable data a streak must have before
# it's considered reliable enough to contribute to a mismatch.
MISMATCH_MIN_WINDOW = 5
