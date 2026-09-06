"""
engine/main.py

Entry point for the daily live-prediction workflow (used by
.github/workflows/daily_predict.yml). Pipeline:

1. Fetch historical fixtures (for model fitting) and today's fixtures
   (to predict) from API-Football.
2. Fit Dixon-Coles on historical data.
3. Build Monte Carlo simulator and shots-on-target model.
4. Generate ensemble predictions for today's fixtures.
5. Write predictions JSON and render the HTML dashboard.

Usage: python -m engine.main --date today
       python -m engine.main --date 2026-09-06

CAVEATS (read before trusting live output):
- Shots-on-target team profiles are currently built from a simple
  average of each team's own historical fixture statistics fetched via
  API-Football - this has NOT been validated against real data (see
  engine/sources/api_football_source.py's verification status). If
  fixture statistics come back empty/null in practice, this falls back
  to league-average shots profiles, which will make the shots-on-target
  predictions uniform and uninformative rather than crash - check the
  logged warning count if numbers look suspiciously flat.
- This entry point does NOT check API-Football's 100/day free-tier
  quota before running. Fetching statistics for many historical
  fixtures during model fitting can burn quota quickly - see the
  --max-historical-fixtures flag to cap this during testing.
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta

import config
from engine.sources.api_football_source import ApiFootballSource
from engine.sources.base import MatchResult
from engine.prediction.dixon_coles import DixonColesModel, MatchInput
from engine.prediction.monte_carlo import MonteCarloSimulator
from engine.prediction.shots import ShotsOnTargetModel, TeamShotsProfile
from engine.prediction.ensemble import EnsemblePredictor
from engine.output.generate_html import write_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("engine.main")


def _cache_path(season: int) -> str:
    return os.path.join(config.HISTORICAL_DATA_DIR, f"season_{season}_detailed.json")


def _load_cached_detailed_results(season: int) -> dict:
    """Load previously-fetched detailed fixture results (with statistics)
    from disk, keyed by fixture_id. Returns {} if no cache exists yet.

    This cache is the fix for a real problem found during integration
    testing: without it, main.py was re-fetching full statistics for
    EVERY historical fixture on EVERY daily run, burning ~95 of the
    100/day free-tier requests just re-fetching data that doesn't
    change once a match has finished. Caching finished-match statistics
    to disk (committed back to the repo by the GitHub Actions workflow)
    means only NEW fixtures since the last run need fetching each day.
    """
    path = _cache_path(season)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {
        fid: MatchResult(
            fixture_id=fid,
            date=date.fromisoformat(r["date"]) if r["date"] else None,
            season=r["season"], home_team=r["home_team"], away_team=r["away_team"],
            home_goals=r["home_goals"], away_goals=r["away_goals"],
            home_shots_on_target=r["home_shots_on_target"],
            away_shots_on_target=r["away_shots_on_target"],
            home_corners=r["home_corners"], away_corners=r["away_corners"],
            status=r["status"],
        )
        for fid, r in raw.items()
    }


def _save_cached_detailed_results(season: int, results: dict):
    """Persist detailed fixture results to disk. Only finished matches
    are worth caching (scheduled/in-play matches' stats will change).
    """
    os.makedirs(config.HISTORICAL_DATA_DIR, exist_ok=True)
    serializable = {
        fid: {
            "date": r.date.isoformat() if r.date else None,
            "season": r.season, "home_team": r.home_team, "away_team": r.away_team,
            "home_goals": r.home_goals, "away_goals": r.away_goals,
            "home_shots_on_target": r.home_shots_on_target,
            "away_shots_on_target": r.away_shots_on_target,
            "home_corners": r.home_corners, "away_corners": r.away_corners,
            "status": r.status,
        }
        for fid, r in results.items() if r.is_finished
    }
    with open(_cache_path(season), "w") as f:
        json.dump(serializable, f, indent=2)


def _match_results_to_dixon_coles_inputs(results: list[MatchResult]) -> list[MatchInput]:
    """Filter to finished matches with valid goals and convert to the
    format DixonColesModel.fit() expects. Matches missing goals or a
    date are skipped with a warning rather than silently dropped, so a
    systematic data problem (e.g. API not returning goals) is visible
    in the logs rather than just producing a smaller-than-expected
    training set with no explanation.
    """
    inputs = []
    skipped = 0
    for r in results:
        if not r.is_finished or not r.has_goals or r.date is None:
            skipped += 1
            continue
        inputs.append(MatchInput(
            home_team=r.home_team, away_team=r.away_team,
            home_goals=r.home_goals, away_goals=r.away_goals,
            match_date=r.date,
        ))
    if skipped:
        logger.warning(f"Skipped {skipped}/{len(results)} historical matches (missing goals/date/not finished)")
    return inputs


def _build_shots_profiles(results: list[MatchResult]) -> list[TeamShotsProfile]:
    """Aggregate per-team home/away shots-on-target for/against averages
    from historical match results. Teams with no shots data at all
    (e.g. API-Football's statistics endpoint returned nothing for every
    one of their matches) are simply absent from the returned list -
    ShotsOnTargetModel already falls back to league averages for any
    team not present in its fitted profiles (see shots.py), so this is
    a safe, explicit "we don't know" rather than a fabricated number.
    """
    from collections import defaultdict

    home_for = defaultdict(list)
    home_against = defaultdict(list)
    away_for = defaultdict(list)
    away_against = defaultdict(list)

    matches_with_stats = 0
    for r in results:
        if not r.is_finished or not r.has_shots_on_target:
            continue
        matches_with_stats += 1
        home_for[r.home_team].append(r.home_shots_on_target)
        home_against[r.home_team].append(r.away_shots_on_target)
        away_for[r.away_team].append(r.away_shots_on_target)
        away_against[r.away_team].append(r.home_shots_on_target)

    if matches_with_stats == 0:
        logger.warning(
            "No historical matches had shots-on-target statistics available. "
            "Shots predictions will fall back to league averages for ALL "
            "teams - this likely means the API-Football statistics endpoint "
            "isn't returning the expected fields (see api_football_source.py "
            "verification status). Check a raw response manually."
        )
        return []

    logger.info(f"Built shots profiles from {matches_with_stats} matches with statistics")

    teams = set(home_for.keys()) | set(away_for.keys())
    profiles = []
    for team in teams:
        def avg(lst):
            return sum(lst) / len(lst) if lst else None

        h_for = avg(home_for.get(team, []))
        h_against = avg(home_against.get(team, []))
        a_for = avg(away_for.get(team, []))
        a_against = avg(away_against.get(team, []))

        # Skip teams missing any of the four averages rather than
        # silently substituting a made-up default into a "real" profile.
        if None in (h_for, h_against, a_for, a_against):
            logger.warning(f"Incomplete shots data for {team} - excluding from profiles (will use league average)")
            continue

        profiles.append(TeamShotsProfile(
            team=team, home_for_avg=h_for, home_against_avg=h_against,
            away_for_avg=a_for, away_against_avg=a_against,
        ))
    return profiles


def run(target_date: date, max_historical_fixtures: int = None):
    source = ApiFootballSource()
    season = source._infer_current_season(target_date)

    logger.info(f"Fetching historical fixtures for season {season}...")
    season_start = date(season, 7, 1)
    historical_fixtures = source.get_fixtures(
        season=season, date_from=season_start, date_to=target_date - timedelta(days=1)
    )
    finished = [f for f in historical_fixtures if f.is_finished]
    logger.info(f"Found {len(finished)} finished fixtures this season as of {target_date}")

    if max_historical_fixtures:
        finished = finished[:max_historical_fixtures]
        logger.info(f"Capped to {len(finished)} fixtures (--max-historical-fixtures)")

    if len(finished) < 20:
        logger.warning(
            f"Only {len(finished)} finished fixtures available - Dixon-Coles "
            f"fits are unreliable on very small samples early in a season. "
            f"Predictions from this run should be treated with caution."
        )

    logger.info("Fetching detailed statistics for historical fixtures (for shots model)...")
    cached = _load_cached_detailed_results(season)
    logger.info(f"Loaded {len(cached)} cached fixture results from disk")

    detailed_results = []
    newly_fetched = 0
    for i, fixture in enumerate(finished):
        if fixture.fixture_id in cached:
            detailed_results.append(cached[fixture.fixture_id])
            continue
        try:
            detailed = source.get_fixture_statistics(fixture.fixture_id)
            detailed_results.append(detailed)
            cached[fixture.fixture_id] = detailed
            newly_fetched += 1
        except Exception as e:
            logger.warning(f"Failed to fetch statistics for fixture {fixture.fixture_id}: {e}")
        if source.request_count >= config.API_FOOTBALL_FREE_TIER_DAILY_LIMIT - 5:
            logger.warning(
                f"Approaching API-Football daily quota ({source.request_count} requests used) - "
                f"stopping historical statistics fetch early at {i+1}/{len(finished)} fixtures."
            )
            break

    logger.info(f"Fetched {newly_fetched} new fixture statistics this run (rest served from cache)")
    _save_cached_detailed_results(season, cached)

    dc_inputs = _match_results_to_dixon_coles_inputs(detailed_results or finished)
    if len(dc_inputs) < 10:
        logger.error(
            f"Only {len(dc_inputs)} usable historical matches - cannot fit a "
            f"meaningful model. Aborting this run rather than producing "
            f"predictions from insufficient data."
        )
        sys.exit(1)

    logger.info(f"Fitting Dixon-Coles model on {len(dc_inputs)} matches...")
    dc_model = DixonColesModel()
    dc_model.fit(dc_inputs, as_of=target_date)

    mc_simulator = MonteCarloSimulator(dc_model)

    shots_profiles = _build_shots_profiles(detailed_results)
    shots_model = ShotsOnTargetModel()
    shots_model.fit(shots_profiles)

    ensemble = EnsemblePredictor(dc_model, mc_simulator, shots_model)

    logger.info(f"Fetching fixtures for {target_date}...")
    todays_fixtures = source.get_fixtures(season=season, date_from=target_date, date_to=target_date)
    scheduled = [f for f in todays_fixtures if f.status in ("scheduled", "in_play")]
    logger.info(f"Found {len(scheduled)} scheduled fixtures for {target_date}")

    predictions = []
    for fixture in scheduled:
        try:
            prediction = ensemble.predict(fixture.home_team, fixture.away_team)
            predictions.append(prediction.to_dict())
        except ValueError as e:
            # Unknown team - e.g. newly promoted, not in this season's
            # historical data yet. Log and skip rather than crash the
            # whole run over one fixture.
            logger.warning(f"Skipping {fixture.home_team} vs {fixture.away_team}: {e}")

    logger.info(f"Generated {len(predictions)} predictions")

    with open(config.PREDICTIONS_JSON_PATH, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "target_date": target_date.isoformat(),
            "predictions": predictions,
        }, f, indent=2)
    logger.info(f"Wrote predictions to {config.PREDICTIONS_JSON_PATH}")

    output_path = write_html(predictions)
    logger.info(f"Wrote HTML dashboard to {output_path}")

    logger.info(f"Total API-Football requests used this run: {source.request_count}")


def main():
    parser = argparse.ArgumentParser(description="Run the EPL daily prediction pipeline")
    parser.add_argument(
        "--date", default="today",
        help="Target date (YYYY-MM-DD) or 'today'",
    )
    parser.add_argument(
        "--max-historical-fixtures", type=int, default=None,
        help="Cap the number of historical fixtures processed (useful for testing without burning API quota)",
    )
    args = parser.parse_args()

    if args.date == "today":
        target_date = date.today()
    else:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    run(target_date, max_historical_fixtures=args.max_historical_fixtures)


if __name__ == "__main__":
    main()
