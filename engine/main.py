"""
engine/main.py

Entry point for the daily EPL trend-surfacing dashboard (used by
.github/workflows/daily_dashboard.yml). Pipeline:

1. Fetch today's fixtures from OpenFootballSource.
2. Fetch historical match data (goals, shots on target, corners) from
   XgaboraMatchDataSource.
3. For each fixture, run StreakAnalyzer.find_mismatches() and compute
   the full set of individual streaks for both teams.
4. Write a predictions/streaks JSON snapshot and render the HTML
   dashboard.

REWRITTEN FOR THE PROJECT PIVOT: this file previously fit a
Dixon-Coles model, ran a Monte Carlo simulator, built a shots-on-target
model, and combined them via an ensemble predictor to produce
probability-based predictions. All of that was deleted - see
config.py's module docstring for why. This is now a pure trend/streak
surfacing tool: it computes and displays historical statistical
patterns, and never predicts outcomes, recommends bets, or computes
stake sizes.

Usage: python -m engine.main --date today
       python -m engine.main --date 2026-09-06
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime

import config
from engine.sources.openfootball_source import OpenFootballSource
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
# 18, or H2H_WINDOWS' max of 15 meetings) real data to work with - H2H
# streaks specifically benefit from multiple seasons since two specific
# teams may only meet twice a season.
STREAK_HISTORY_SEASONS_BACK = 5


def run(target_date: date):
    fixture_source = OpenFootballSource()
    season = target_date.year if target_date.month >= 7 else target_date.year - 1

    logger.info(f"Fetching fixtures for {target_date} from openfootball...")
    todays_fixtures = fixture_source.get_fixtures_for_date(target_date, season_start_year=season)
    scheduled = [f for f in todays_fixtures if f.status in ("scheduled", "in_play")]
    logger.info(f"Found {len(scheduled)} scheduled fixtures for {target_date}")

    if not scheduled:
        logger.info("No fixtures found for this date - writing an empty dashboard.")
        _write_outputs(target_date, 0, {}, {})
        return

    logger.info("Fetching historical match data for streak analysis...")
    streak_source = XgaboraMatchDataSource()
    streak_seasons = range(season - STREAK_HISTORY_SEASONS_BACK, season + 1)
    streak_matches = []
    for s in streak_seasons:
        try:
            streak_matches.extend(streak_source.fetch_season(s))
        except Exception as e:
            logger.warning(f"Could not fetch season {s} for streak analysis: {e}")

    logger.info(f"Loaded {len(streak_matches)} historical matches for streak analysis")

    if len(streak_matches) < 20:
        logger.error(
            f"Only {len(streak_matches)} historical matches available - "
            f"insufficient for meaningful streak analysis. Aborting this "
            f"run rather than producing a dashboard with unreliable data."
        )
        sys.exit(1)

    analyzer = StreakAnalyzer(streak_matches)

    all_mismatches = {}
    all_streaks = {}
    involved_teams = set()

    for fixture in scheduled:
        fixture_label = f"{fixture.home_team} vs {fixture.away_team}"
        try:
            mismatches = analyzer.find_mismatches(fixture.home_team, fixture.away_team, target_date)
        except Exception as e:
            logger.warning(f"Streak analysis failed for {fixture_label}: {e}")
            continue
        if mismatches:
            all_mismatches[fixture_label] = mismatches
        involved_teams.add(fixture.home_team)
        involved_teams.add(fixture.away_team)

    # Populate all_streaks for the "All Streaks by Category" section:
    # for each team involved in today's fixtures, compute their streaks
    # across every configured stat/threshold/window combination (not
    # just the ones that happened to produce a mismatch), so the user
    # sees the full picture, not just the flagged subset.
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
        all_streaks[team] = team_streaks

    total_mismatches = sum(len(v) for v in all_mismatches.values())
    logger.info(f"Found {total_mismatches} total mismatches across {len(scheduled)} fixtures")

    _write_outputs(target_date, len(scheduled), all_mismatches, all_streaks)


def _write_outputs(target_date: date, fixture_count: int, all_mismatches: dict, all_streaks: dict):
    with open(config.PREDICTIONS_JSON_PATH, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "target_date": target_date.isoformat(),
            "fixture_count": fixture_count,
            "total_mismatches": sum(len(v) for v in all_mismatches.values()),
        }, f, indent=2)
    logger.info(f"Wrote summary to {config.PREDICTIONS_JSON_PATH}")

    output_path = write_html(fixture_count=fixture_count, all_mismatches=all_mismatches, all_streaks=all_streaks)
    logger.info(f"Wrote HTML dashboard to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run the EPL trend-surfacing dashboard")
    parser.add_argument(
        "--date", default="today",
        help="Target date (YYYY-MM-DD) or 'today'",
    )
    args = parser.parse_args()

    if args.date == "today":
        target_date = date.today()
    else:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    run(target_date)


if __name__ == "__main__":
    main()
