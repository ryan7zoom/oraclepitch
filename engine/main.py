"""
engine/main.py

Entry point for the daily trend-surfacing dashboard (used by
.github/workflows/daily_dashboard.yml). Pipeline, per league:

1. Fetch today's fixtures from OpenFootballSource.
2. Fetch historical match data (goals, shots on target, corners) from
   XgaboraMatchDataSource.
3. For each fixture, run StreakAnalyzer.find_mismatches() and compute
   the full set of individual streaks for both teams.
4. Write a predictions/streaks JSON snapshot and render the HTML
   dashboard, with all leagues combined into one page.

MULTI-LEAGUE SUPPORT: originally EPL-only, extended to cover the top 5
European leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1) - see
engine/sources/openfootball_source.py's LEAGUE_CODES for the full
mapping and per-league team-name normalization tables. Champions
League / Europa League were investigated and are NOT supported: no
free source with the required historical stats (shots on target,
corners) covering those competitions was found - xgabora's dataset
only contains domestic league divisions, confirmed by checking every
division code actually present in the live file. If a future season's
openfootball or xgabora data changes this, the LEAGUE_CODES mapping is
the place to add it, but as of this writing this is a real, confirmed
gap, not an oversight.

Usage: python -m engine.main --date today
       python -m engine.main --date 2026-09-06
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime

import config
from engine.sources.openfootball_source import OpenFootballSource, LEAGUE_CODES
from engine.sources.xgabora_match_data_source import XgaboraMatchDataSource
from engine.output.generate_html import write_html
from engine.streaks.analyzer import StreakAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("engine.main")

# How many seasons of history to pull for the streak analyzer. Long
# enough to give the longest configured window (STREAK_WINDOWS' max of
# 18, or H2H_WINDOWS' max of 15 meetings) real data to work with.
STREAK_HISTORY_SEASONS_BACK = 5

LEAGUE_DISPLAY_NAMES = {
    "epl": "Premier League",
    "la_liga": "La Liga",
    "bundesliga": "Bundesliga",
    "serie_a": "Serie A",
    "ligue_1": "Ligue 1",
}


def run(target_date: date, leagues: list = None):
    leagues = leagues or list(LEAGUE_CODES.keys())
    fixture_source = OpenFootballSource()
    season = target_date.year if target_date.month >= 7 else target_date.year - 1

    all_mismatches = {}
    all_streaks = {}
    total_scheduled = 0

    for league in leagues:
        league_name = LEAGUE_DISPLAY_NAMES.get(league, league)
        logger.info(f"[{league_name}] Fetching fixtures for {target_date} from openfootball...")
        todays_fixtures = fixture_source.get_fixtures_for_date(target_date, league=league, season_start_year=season)
        scheduled = [f for f in todays_fixtures if f.status in ("scheduled", "in_play")]
        logger.info(f"[{league_name}] Found {len(scheduled)} scheduled fixtures for {target_date}")

        if not scheduled:
            continue
        total_scheduled += len(scheduled)

        logger.info(f"[{league_name}] Fetching historical match data for streak analysis...")
        division_code = LEAGUE_CODES[league]["xgabora_division"]
        streak_source = XgaboraMatchDataSource(division_code=division_code)
        streak_seasons = range(season - STREAK_HISTORY_SEASONS_BACK, season + 1)
        streak_matches = []
        for s in streak_seasons:
            try:
                streak_matches.extend(streak_source.fetch_season(s))
            except Exception as e:
                logger.warning(f"[{league_name}] Could not fetch season {s} for streak analysis: {e}")

        logger.info(f"[{league_name}] Loaded {len(streak_matches)} historical matches for streak analysis")

        if len(streak_matches) < 20:
            logger.warning(
                f"[{league_name}] Only {len(streak_matches)} historical matches available - "
                f"insufficient for meaningful streak analysis. Skipping this league "
                f"for this run rather than aborting the entire dashboard over one league."
            )
            continue

        analyzer = StreakAnalyzer(streak_matches)
        involved_teams = set()

        for fixture in scheduled:
            fixture_label = f"[{league_name}] {fixture.home_team} vs {fixture.away_team}"
            try:
                mismatches = analyzer.find_mismatches(fixture.home_team, fixture.away_team, target_date)
            except Exception as e:
                logger.warning(f"[{league_name}] Streak analysis failed for {fixture_label}: {e}")
                continue
            if mismatches:
                all_mismatches[fixture_label] = mismatches
            involved_teams.add(fixture.home_team)
            involved_teams.add(fixture.away_team)

        for team in involved_teams:
            team_streaks = []
            for stat, thresholds in config.STREAK_THRESHOLDS.items():
                for threshold in thresholds:
                    for window in config.STREAK_WINDOWS:
                        for direction in ("for", "against"):
                            for filter_type in ("home_only", "away_only"):
                                s = analyzer.get_streak(team, stat, threshold, window, direction, target_date, filter_type)
                                if s.total >= config.MISMATCH_MIN_WINDOW:
                                    team_streaks.append(s)
            # Prefix team name with league in the streaks dict key so
            # two clubs with the same name in different leagues (rare,
            # but possible) don't collide - the dict key isn't shown
            # directly in the HTML (team.name is used for display), so
            # this is purely an internal collision guard.
            all_streaks[f"{league}:{team}"] = team_streaks

    if total_scheduled == 0:
        logger.info("No fixtures found for this date across any league - writing an empty dashboard.")

    total_mismatches = sum(len(v) for v in all_mismatches.values())
    logger.info(f"Found {total_mismatches} total mismatches across {total_scheduled} fixtures across all leagues")

    _write_outputs(target_date, total_scheduled, all_mismatches, all_streaks)


def _write_outputs(target_date: date, fixture_count: int, all_mismatches: dict, all_streaks: dict):
    # Ensure the output directories exist before writing. git does not
    # track empty directories, so a fresh checkout of this repo may be
    # missing data/predictions/ (and potentially docs/) entirely - this
    # was confirmed as a real production crash (FileNotFoundError) on
    # a genuine GitHub Actions run, not a hypothetical edge case.
    os.makedirs(os.path.dirname(config.PREDICTIONS_JSON_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(config.HTML_OUTPUT_PATH), exist_ok=True)

    with open(config.PREDICTIONS_JSON_PATH, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "target_date": target_date.isoformat(),
            "fixture_count": fixture_count,
            "total_mismatches": sum(len(v) for v in all_mismatches.values()),
        }, f, indent=2)
    logger.info(f"Wrote summary to {config.PREDICTIONS_JSON_PATH}")

    # all_streaks keys are prefixed "league:TeamName" internally (see
    # run()'s collision-guard comment) - strip that prefix for display,
    # since the HTML shouldn't show the internal key format.
    display_streaks = {}
    for key, streaks in all_streaks.items():
        team_name = key.split(":", 1)[1] if ":" in key else key
        display_streaks[team_name] = streaks

    output_path = write_html(fixture_count=fixture_count, all_mismatches=all_mismatches, all_streaks=display_streaks)
    logger.info(f"Wrote HTML dashboard to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run the multi-league trend-surfacing dashboard")
    parser.add_argument(
        "--date", default="today",
        help="Target date (YYYY-MM-DD) or 'today'",
    )
    parser.add_argument(
        "--leagues", default=None,
        help=f"Comma-separated league codes to include (default: all). Options: {list(LEAGUE_CODES.keys())}",
    )
    args = parser.parse_args()

    if args.date == "today":
        target_date = date.today()
    else:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    leagues = args.leagues.split(",") if args.leagues else None

    run(target_date, leagues=leagues)


if __name__ == "__main__":
    main()
