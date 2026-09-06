"""
config.py

Central configuration for the EPL prediction system.
All tunable parameters live here so they can be adjusted without touching
model or engine code.
"""

import os

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_HOST = "v3.football.api-sports.io"
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"

# ---------------------------------------------------------------------------
# League
# ---------------------------------------------------------------------------
LEAGUE_ID = 39  # Premier League (API-Football)
LEAGUE_NAME = "English Premier League"

# ---------------------------------------------------------------------------
# Model Parameters
# ---------------------------------------------------------------------------
TIME_DECAY_HALF_LIFE = 107  # days
MONTE_CARLO_ITERATIONS = 10000
MAX_GOALS_FOR_DIST = 10  # ceiling for scoreline grid (0..10 per team)

ENSEMBLE_WEIGHTS = {
    "dixon_coles": 0.5,
    "monte_carlo": 0.5,
}

# ---------------------------------------------------------------------------
# Bet Types
# ---------------------------------------------------------------------------
# NOTE ON CORNERS: team_corners is disabled for the LIVE DAILY prediction
# pipeline (engine/main.py) because no free, reliable, low-fragility LIVE
# data source has been confirmed for upcoming/in-progress fixtures - see
# engine/sources/ for the sources investigated (API-Football, sofascrape,
# ScraperFC/Sofascore).
#
# This is now DIFFERENT from the backtest situation: football-data.co.uk
# (engine/sources/football_data_co_uk_source.py) provides real historical
# corners (HC/AC columns) for free, no key required. That source is
# suitable for backtesting a corners model against real historical
# results, but is a static/weekly-updated CSV archive, not a live API -
# it cannot power daily predictions for upcoming matches. A corners
# backtest module has not yet been built to take advantage of this
# (engine/prediction/corners.py does not exist yet), but the data
# ingredient for one is now available where it wasn't before.
BET_TYPES = {
    "match_winner": True,
    "double_chance": True,          # 1X and X2 ONLY (NO 12)
    "match_total_goals": True,      # O 0.5, 1.5, 2.5, 3.5, 4.5
    "team_total_goals": True,       # O 0.5, 1.5, 2.5, 3.5 (per team)
    "team_corners": False,          # DISABLED for LIVE predictions - see note above
    "team_shots_on_target": True,   # O 2.5, 3.5, 4.5, 5.5 (per team, OVER only)
    "match_shots_on_target": True,  # O 7.5, 8.5, 9.5, 10.5 (OVER only)
}

# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------
GOAL_LINES = [0.5, 1.5, 2.5, 3.5, 4.5]
TEAM_GOAL_LINES = [0.5, 1.5, 2.5, 3.5]

# Corners and shots-on-target lines are now DYNAMIC per team/match rather
# than a fixed list. Fixed lines were too high for weak teams (e.g. a
# team averaging 3.5 corners has no meaningful signal at Over 3.5+) and
# capped too low for strong teams (e.g. Liverpool-level sides regularly
# clear 8-10 corners, so a 6.5 ceiling loses real value on higher lines).
# See engine/prediction/corners.py's and shots.py's generate_lines()
# helpers for how these are actually used - MIN_LINE/STEP define the
# range shape, and the per-team/per-match expected value determines how
# far the range extends (see each module's docstring for the exact
# formula: min_line up to round(expected_value + 3.0), stepping by STEP).
CORNERS_MIN_LINE = 1.5
CORNERS_STEP = 1.0
SHOTS_MIN_LINE = 1.5
SHOTS_STEP = 1.0

# ---------------------------------------------------------------------------
# Backtesting
# ---------------------------------------------------------------------------
# The backtest CLI (engine/backtest/simulator.py) now uses
# football-data.co.uk, which has EPL data back to the 1993-94 season, so
# this range is no longer constrained by an uncertain free-tier API
# depth (as it was when this assumed API-Football would be the backtest
# source). 2021 is still a reasonable default: recent enough that squad
# strength is still broadly relevant to today's teams, without needing
# to handle the greater number of relegated/promoted/renamed clubs that
# come with a much longer historical window.
BACKTEST_START_SEASON = 2021
BACKTEST_END_SEASON = 2025
KELLY_FRACTION = 0.25

# ---------------------------------------------------------------------------
# API-Football rate limiting
# ---------------------------------------------------------------------------
API_FOOTBALL_FREE_TIER_DAILY_LIMIT = 100
API_FOOTBALL_REQUEST_DELAY_SECONDS = 1.0  # be polite between calls

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
PREDICTIONS_JSON_PATH = "data/predictions/latest.json"
HTML_OUTPUT_PATH = "docs/index.html"
HISTORICAL_DATA_DIR = "data/historical"
