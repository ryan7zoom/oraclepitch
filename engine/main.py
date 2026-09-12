"""
engine/main.py

Entry point for the trend-surfacing dashboard (used by
.github/workflows/daily_dashboard.yml). Pipeline, per league, per day
in a rolling window:

1. Fetch fixtures for the next few days from OpenFootballSource.
2. Fetch historical match data (goals, shots on target, corners) from
   XgaboraMatchDataSource.
3. For each fixture, run StreakAnalyzer.find_mismatches() using THAT
   FIXTURE'S OWN DATE as the as-of reference (not a single shared
   date), and compute the full set of individual streaks for both
   teams as of that same date.
4. Write a predictions/streaks JSON snapshot and render the HTML
   dashboard, grouped by day.

TIMEZONE FIX: originally this used date.today(), which returns the
GitHub Actions runner's UTC date. The user is in Bangladesh (UTC+6) -
this can differ from the UTC date by a full day during roughly the
first 6 hours of the Bangladesh day, causing "today" fixtures to be
missed entirely. _today_bd() below computes the date using
Bangladesh's fixed UTC+6 offset instead of the runner's local/UTC
clock.

MULTI-DAY WINDOW: rather than relying on getting the timezone
boundary exactly right in every case, fixture fetching now covers a
3-day window (today, tomorrow, day after tomorrow - see
OpenFootballSource.get_fixtures_for_date's window_days parameter) as a
second, complementary layer of robustness. This also gives the user
useful advance visibility into upcoming fixtures, which is a
reasonable feature on its own, not just a bug workaround.

MULTI-LEAGUE SUPPORT: covers the top 5 European leagues (EPL, La Liga,
Bundesliga, Serie A, Ligue 1) - see
engine/sources/openfootball_source.py's LEAGUE_CODES for the full
mapping and per-league team-name normalization tables. Champions
League / Europa League were investigated and are NOT supported: no
free source with the required historical stats (shots on target,
corners) covering those competitions was found - xgabora's dataset
only contains domestic league divisions, confirmed by checking every
division code actually present in the live file.

Usage: python -m engine.main --date today
       python -m engine.main --date 2026-09-06
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

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

# How many days beyond the target date to also show fixtures for -
# see module docstring's "MULTI-DAY WINDOW" section. 2 means a 3-day
# total window: target date, +1, +2.
FIXTURE_WINDOW_DAYS = 2

LEAGUE_DISPLAY_NAMES = {
    "epl": "Premier League",
    "la_liga": "La Liga",
    "bundesliga": "Bundesliga",
    "serie_a": "Serie A",
    "ligue_1": "Ligue 1",
}

# Bangladesh Standard Time is a fixed UTC+6 offset (no daylight saving
# time observed) - see module docstring's "TIMEZONE FIX" section.
BD_TZ = timezone(timedelta(hours=6))


def _today_bd() -> date:
    """Return the current date in Bangladesh time (UTC+6), NOT the
    system/runner's local date. GitHub Actions runners use UTC, so
    date.today() there returns the UTC date - during roughly the first
    6 hours of the Bangladesh day, this differs from the actual
    Bangladesh date, which was confirmed to cause real fixtures to be
    missed (see module docstring).
    """
    return datetime.now(BD_TZ).date()


def run(target_date: date, leagues: list = None, window_days: int = FIXTURE_WINDOW_DAYS):
    leagues = leagues or list(LEAGUE_CODES.keys())
    fixture_source = OpenFootballSource()
    season = target_date.year if target_date.month >= 7 else target_date.year - 1

    # fixtures_by_date maps date -> list of (league_key, MatchResult)
    # tuples, so the dashboard can be grouped by day. Built up across
    # all leagues before any streak analysis happens, since we need to
    # know the full day-by-day fixture list before deciding which
    # historical data to fetch per league.
    fixtures_by_date: dict = defaultdict(list)

    for league in leagues:
        league_name = LEAGUE_DISPLAY_NAMES.get(league, league)
        logger.info(f"[{league_name}] Fetching fixtures for {target_date} (+{window_days} days) from openfootball...")
        window_fixtures = fixture_source.get_fixtures_for_date(
            target_date, league=league, season_start_year=season, window_days=window_days,
        )
        scheduled = [f for f in window_fixtures if f.status in ("scheduled", "in_play")]
        logger.info(f"[{league_name}] Found {len(scheduled)} scheduled fixtures across the {window_days + 1}-day window")

        for fixture in scheduled:
            fixtures_by_date[fixture.date].append((league, fixture))

    total_scheduled = sum(len(v) for v in fixtures_by_date.values())
    if total_scheduled == 0:
        logger.info("No fixtures found in this window across any league - writing an empty dashboard.")
        _write_outputs(target_date, window_days, 0, {}, {})
        return

    # Group by league now (across all days) so historical data is only
    # fetched once per league for the whole window, not once per
    # league per day.
    leagues_with_fixtures = {league for date_fixtures in fixtures_by_date.values() for league, _ in date_fixtures}

    # all_mismatches / all_streaks are keyed by date first, matching
    # what the HTML generator needs to render day-grouped sections -
    # see engine/output/generate_html.py's date-grouped rendering.
    all_mismatches_by_date: dict = {d: {} for d in fixtures_by_date}
    all_streaks_by_date: dict = {d: {} for d in fixtures_by_date}

    for league in leagues_with_fixtures:
        league_name = LEAGUE_DISPLAY_NAMES.get(league, league)
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

        for fixture_date, date_fixtures in fixtures_by_date.items():
            league_fixtures_this_day = [f for lg, f in date_fixtures if lg == league]
            if not league_fixtures_this_day:
                continue

            involved_teams = set()
            for fixture in league_fixtures_this_day:
                fixture_label = f"[{league_name}] {fixture.home_team} vs {fixture.away_team}"
                try:
                    # IMPORTANT: uses fixture.date (this specific
                    # match's own date), NOT the window's target_date -
                    # each fixture's streaks must reflect team form
                    # "as of" that fixture's actual date, not the
                    # window's start date, so a match 2 days into the
                    # window correctly includes any matches played in
                    # between (per the pivot spec's requirement that
                    # trend computation is unchanged and uses each
                    # fixture's own date as the as-of reference).
                    mismatches = analyzer.find_mismatches(fixture.home_team, fixture.away_team, fixture.date)
                except Exception as e:
                    logger.warning(f"[{league_name}] Streak analysis failed for {fixture_label}: {e}")
                    continue
                if mismatches:
                    all_mismatches_by_date[fixture_date][fixture_label] = mismatches
                involved_teams.add(fixture.home_team)
                involved_teams.add(fixture.away_team)

            for team in involved_teams:
                team_streaks = []
                for stat, thresholds in config.STREAK_THRESHOLDS.items():
                    for threshold in thresholds:
                        for window in config.STREAK_WINDOWS:
                            for direction in ("for", "against"):
                                for filter_type in ("home_only", "away_only"):
                                    s = analyzer.get_streak(team, stat, threshold, window, direction, fixture_date, filter_type)
                                    if s.total >= config.MISMATCH_MIN_WINDOW:
                                        team_streaks.append(s)
                all_streaks_by_date[fixture_date][f"{league}:{team}"] = team_streaks

    total_mismatches = sum(
        len(mismatches) for date_dict in all_mismatches_by_date.values() for mismatches in date_dict.values()
    )
    logger.info(f"Found {total_mismatches} total mismatches across {total_scheduled} fixtures across all leagues/days")

    _write_outputs(target_date, window_days, total_scheduled, all_mismatches_by_date, all_streaks_by_date)


def _write_outputs(
    target_date: date, window_days: int, fixture_count: int,
    all_mismatches_by_date: dict, all_streaks_by_date: dict,
):
    # Ensure the output directories exist before writing. git does not
    # track empty directories, so a fresh checkout of this repo may be
    # missing data/predictions/ (and potentially docs/) entirely - this
    # was confirmed as a real production crash (FileNotFoundError) on
    # a genuine GitHub Actions run, not a hypothetical edge case.
    os.makedirs(os.path.dirname(config.PREDICTIONS_JSON_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(config.HTML_OUTPUT_PATH), exist_ok=True)

    total_mismatches = sum(
        len(mismatches) for date_dict in all_mismatches_by_date.values() for mismatches in date_dict.values()
    )

    with open(config.PREDICTIONS_JSON_PATH, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "target_date": target_date.isoformat(),
            "window_days": window_days,
            "fixture_count": fixture_count,
            "total_mismatches": total_mismatches,
        }, f, indent=2)
    logger.info(f"Wrote summary to {config.PREDICTIONS_JSON_PATH}")

    # all_streaks_by_date's inner dicts are keyed "league:TeamName"
    # internally (collision guard for same-name clubs in different
    # leagues) - strip that prefix for display, since the HTML
    # shouldn't show the internal key format.
    display_streaks_by_date = {}
    for d, streaks_dict in all_streaks_by_date.items():
        display_streaks_by_date[d] = {
            (key.split(":", 1)[1] if ":" in key else key): streaks
            for key, streaks in streaks_dict.items()
        }

    output_path = write_html(
        fixture_count=fixture_count,
        all_mismatches_by_date=all_mismatches_by_date,
        all_streaks_by_date=display_streaks_by_date,
        target_date=target_date,
    )
    logger.info(f"Wrote HTML dashboard to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run the multi-league trend-surfacing dashboard")
    parser.add_argument(
        "--date", default="today",
        help="Target date (YYYY-MM-DD) or 'today' (uses Bangladesh local date, not UTC)",
    )
    parser.add_argument(
        "--leagues", default=None,
        help=f"Comma-separated league codes to include (default: all). Options: {list(LEAGUE_CODES.keys())}",
    )
    parser.add_argument(
        "--window-days", type=int, default=FIXTURE_WINDOW_DAYS,
        help=f"Number of additional days beyond --date to include (default: {FIXTURE_WINDOW_DAYS})",
    )
    args = parser.parse_args()

    if args.date == "today":
        target_date = _today_bd()
    else:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    leagues = args.leagues.split(",") if args.leagues else None

    run(target_date, leagues=leagues, window_days=args.window_days)


if __name__ == "__main__":
    main()
