"""
engine/main.py

Entry point for the trend-surfacing dashboard (used by
.github/workflows/daily_dashboard.yml). Pipeline, per league, per day
in a rolling window:

1. Fetch fixtures for the next few days from OpenFootballSource.
2. Fetch historical match data (goals, shots on target, corners) from
   XgaboraMatchDataSource.
3. For each fixture, run StreakAnalyzer.find_mismatches() (double-sided
   overlaps) AND compute single-sided recent-form streaks for the home
   team's home form and the away team's away form specifically - using
   THAT FIXTURE'S OWN DATE as the as-of reference in both cases.
4. Write a predictions/streaks JSON snapshot and render the HTML
   dashboard, grouped by day, then by league, with each match collapsed
   by default.

DATA MODEL: all_mismatches / all_recent_form_streaks are nested
date -> league -> fixture_label -> ..., rather than flattening league
into the fixture_label string and parsing it back out at render time
(which was the previous approach - fragile and harder to group
correctly). See engine/output/generate_html.py for how this nested
structure is rendered.

TIMEZONE FIX: uses _today_bd() (Bangladesh, UTC+6) rather than
date.today() (which returns the GitHub Actions runner's UTC date) -
see that function's docstring for why this matters.

MULTI-DAY WINDOW: fixture fetching covers a 3-day window (today,
tomorrow, day after tomorrow) via OpenFootballSource's window_days
parameter.

MULTI-LEAGUE SUPPORT: covers the top 5 European leagues (EPL, La Liga,
Bundesliga, Serie A, Ligue 1) - see
engine/sources/openfootball_source.py's LEAGUE_CODES. Champions League
/ Europa League are NOT supported: xgabora's dataset only contains
domestic league divisions, confirmed by checking every division code
actually present in the live file - no historical stats source exists
for those competitions.

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

STREAK_HISTORY_SEASONS_BACK = 5
FIXTURE_WINDOW_DAYS = 2

# Recent-form streaks (Problem 2) are filtered to this minimum
# percentage/sample size before being surfaced on the dashboard, per
# spec - otherwise every stat/threshold/window combination for every
# team would be dumped onto the page regardless of how weak or
# unreliable the signal is.
RECENT_FORM_MIN_PERCENTAGE = 0.60
RECENT_FORM_MIN_WINDOW = 5

LEAGUE_DISPLAY_NAMES = {
    "epl": "Premier League",
    "la_liga": "La Liga",
    "bundesliga": "Bundesliga",
    "serie_a": "Serie A",
    "ligue_1": "Ligue 1",
}

BD_TZ = timezone(timedelta(hours=6))


def _today_bd() -> date:
    """Return the current date in Bangladesh time (UTC+6), NOT the
    system/runner's local date - see module docstring's TIMEZONE FIX.
    """
    return datetime.now(BD_TZ).date()


def _compute_recent_form_streaks(analyzer: StreakAnalyzer, home_team: str, away_team: str, as_of: date) -> dict:
    """Compute single-sided recent-form streaks for a fixture, per
    Problem 2's spec: home team's HOME-ONLY form, away team's AWAY-ONLY
    form specifically (not every filter combination for both teams -
    that produced a 37,000-line page). Filtered to
    RECENT_FORM_MIN_PERCENTAGE / RECENT_FORM_MIN_WINDOW, sorted by
    percentage descending.

    Returns {"home": [StreakResult, ...], "away": [StreakResult, ...]},
    where "home" covers home_team's home-only for/against streaks and
    "away" covers away_team's away-only for/against streaks - matching
    "the actual match context (the home team is playing at home, the
    away team is playing away)" per spec.
    """
    def _streaks_for(team: str, filter_type: str) -> list:
        results = []
        for stat, thresholds in config.STREAK_THRESHOLDS.items():
            for threshold in thresholds:
                for window in config.STREAK_WINDOWS:
                    for direction in ("for", "against"):
                        s = analyzer.get_streak(team, stat, threshold, window, direction, as_of, filter_type)
                        if s.total >= RECENT_FORM_MIN_WINDOW and s.percentage >= RECENT_FORM_MIN_PERCENTAGE:
                            results.append(s)
        results.sort(key=lambda s: s.percentage, reverse=True)
        return results

    return {
        "home": _streaks_for(home_team, "home_only"),
        "away": _streaks_for(away_team, "away_only"),
    }


def run(target_date: date, leagues: list = None, window_days: int = FIXTURE_WINDOW_DAYS):
    leagues = leagues or list(LEAGUE_CODES.keys())
    fixture_source = OpenFootballSource()
    season = target_date.year if target_date.month >= 7 else target_date.year - 1

    # fixtures_by_date_league maps date -> league -> list[MatchResult]
    fixtures_by_date_league: dict = defaultdict(lambda: defaultdict(list))

    for league in leagues:
        league_name = LEAGUE_DISPLAY_NAMES.get(league, league)
        logger.info(f"[{league_name}] Fetching fixtures for {target_date} (+{window_days} days) from openfootball...")
        window_fixtures = fixture_source.get_fixtures_for_date(
            target_date, league=league, season_start_year=season, window_days=window_days,
        )
        scheduled = [f for f in window_fixtures if f.status in ("scheduled", "in_play")]
        logger.info(f"[{league_name}] Found {len(scheduled)} scheduled fixtures across the {window_days + 1}-day window")

        for fixture in scheduled:
            fixtures_by_date_league[fixture.date][league].append(fixture)

    total_scheduled = sum(
        len(fixtures) for league_dict in fixtures_by_date_league.values() for fixtures in league_dict.values()
    )
    if total_scheduled == 0:
        logger.info("No fixtures found in this window across any league - writing an empty dashboard.")
        _write_outputs(target_date, window_days, 0, {}, {})
        return

    leagues_with_fixtures = {
        league for league_dict in fixtures_by_date_league.values() for league in league_dict
    }

    # Nested date -> league -> fixture_label -> ... per Problem 4's
    # data model requirement.
    all_mismatches: dict = defaultdict(lambda: defaultdict(dict))
    all_recent_form: dict = defaultdict(lambda: defaultdict(dict))

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

        for fixture_date, league_dict in fixtures_by_date_league.items():
            league_fixtures_this_day = league_dict.get(league, [])
            if not league_fixtures_this_day:
                continue

            for fixture in league_fixtures_this_day:
                fixture_label = f"{fixture.home_team} vs {fixture.away_team}"
                try:
                    # Uses fixture.date (this specific match's own
                    # date), NOT the window's target_date - each
                    # fixture's streaks reflect team form "as of" that
                    # fixture's actual date.
                    mismatches = analyzer.find_mismatches(fixture.home_team, fixture.away_team, fixture.date)
                except Exception as e:
                    logger.warning(f"[{league_name}] Streak analysis failed for {fixture_label}: {e}")
                    continue
                if mismatches:
                    all_mismatches[fixture_date][league][fixture_label] = mismatches

                try:
                    recent_form = _compute_recent_form_streaks(analyzer, fixture.home_team, fixture.away_team, fixture.date)
                except Exception as e:
                    logger.warning(f"[{league_name}] Recent-form computation failed for {fixture_label}: {e}")
                    recent_form = {"home": [], "away": []}
                if recent_form["home"] or recent_form["away"]:
                    all_recent_form[fixture_date][league][fixture_label] = {
                        "home_team": fixture.home_team,
                        "away_team": fixture.away_team,
                        **recent_form,
                    }

    total_mismatches = sum(
        len(mismatches)
        for league_dict in all_mismatches.values()
        for fixture_dict in league_dict.values()
        for mismatches in fixture_dict.values()
    )
    logger.info(f"Found {total_mismatches} total mismatches across {total_scheduled} fixtures across all leagues/days")

    _write_outputs(target_date, window_days, total_scheduled, all_mismatches, all_recent_form,
                   dates_with_fixtures=list(fixtures_by_date_league.keys()))


def _write_outputs(
    target_date: date, window_days: int, fixture_count: int,
    all_mismatches: dict, all_recent_form: dict,
    dates_with_fixtures: list = None,
):
    os.makedirs(os.path.dirname(config.PREDICTIONS_JSON_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(config.HTML_OUTPUT_PATH), exist_ok=True)

    total_mismatches = sum(
        len(mismatches)
        for league_dict in all_mismatches.values()
        for fixture_dict in league_dict.values()
        for mismatches in fixture_dict.values()
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

    output_path = write_html(
        fixture_count=fixture_count,
        all_mismatches=all_mismatches,
        all_recent_form=all_recent_form,
        target_date=target_date,
        league_display_names=LEAGUE_DISPLAY_NAMES,
        league_order=list(LEAGUE_CODES.keys()),
        dates_with_fixtures=dates_with_fixtures,
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
