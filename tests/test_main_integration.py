"""
tests/test_main_integration.py

Integration test for engine/main.py's run() function, rebuilt for the
project pivot to a pure trend-surfacing dashboard (see config.py's
module docstring for the pivot rationale). The pipeline is now:
OpenFootballSource (today's fixtures) -> XgaboraMatchDataSource
(historical data) -> StreakAnalyzer -> generate_html. No model
fitting, no predictions, no per-fixture statistics caching.
"""

import sys
import os
import json
import tempfile
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import config
import engine.main as main_module
from engine.sources.football_data_co_uk_source import HistoricalMatchOdds


def _generate_fake_openfootball_season(n_teams=8, matches_per_pair=4, seed=1, season_start_year=2025):
    np.random.seed(seed)
    teams = [f"Team{i} FC" for i in range(n_teams)]
    matches = []
    start = date(season_start_year, 8, 1)
    day_counter = 0
    for _ in range(matches_per_pair):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                home, away = teams[i], teams[j]
                match_date = start + timedelta(days=day_counter)
                matches.append({
                    "round": "Matchday X", "date": match_date.isoformat(), "time": "15:00",
                    "team1": home, "team2": away,
                    "score": {"ht": [0, 0], "ft": [1, 1]},
                })
                day_counter += 1
    return matches, teams


def _add_upcoming_fixtures(matches, target_date, fixtures):
    for home, away in fixtures:
        matches.append({
            "round": "Matchday Y", "date": target_date.isoformat(), "time": "15:00",
            "team1": home, "team2": away,
        })


def _generate_fake_xgabora_history(teams, n_matches=40, seed=2):
    np.random.seed(seed)
    short_teams = [t.replace(" FC", "") for t in teams]
    records = []
    start = date(2020, 8, 1)
    day = 0
    for _ in range(n_matches):
        i, j = np.random.choice(len(short_teams), 2, replace=False)
        records.append(HistoricalMatchOdds(
            date=start + timedelta(days=day), home_team=short_teams[i], away_team=short_teams[j],
            home_goals=1, away_goals=1,
            home_shots_on_target=int(np.random.poisson(5)), away_shots_on_target=int(np.random.poisson(4)),
            home_corners=int(np.random.poisson(6)), away_corners=int(np.random.poisson(5)),
        ))
        day += 3
    return records


def test_full_pipeline_runs_without_crashing():
    """Explicitly restricted to a single league (leagues=["epl"]) so
    this test's fixture-count assertion stays deterministic - by
    default run() now iterates over all 5 top leagues (see
    test_multi_league_pipeline_covers_all_leagues below for that
    behavior specifically).
    """
    target_date = date(2025, 12, 1)
    matches, teams = _generate_fake_openfootball_season()
    _add_upcoming_fixtures(matches, target_date, [(teams[0], teams[1])])
    history = _generate_fake_xgabora_history(teams)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch("engine.sources.xgabora_match_data_source.XgaboraMatchDataSource.fetch_season", return_value=history), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date, leagues=["epl"])

        assert os.path.exists(predictions_path), "predictions.json was not written"
        assert os.path.exists(html_path), "index.html was not written"

        with open(predictions_path) as f:
            data = json.load(f)
        assert data["target_date"] == target_date.isoformat()
        assert data["fixture_count"] == 1
        assert "total_mismatches" in data

        with open(html_path) as f:
            html = f.read()
        assert "<!DOCTYPE html>" in html

    print(f"PASS: test_full_pipeline_runs_without_crashing (fixture_count={data['fixture_count']})")


def test_per_fixture_streak_analysis_uses_own_match_date_not_window_start():
    """Core regression/feature test for per-fixture as-of dates: with
    two fixtures on different days within the 3-day window, each
    fixture's streak analysis must use ITS OWN date as the as-of
    reference, not the window's start date. Verified by instrumenting
    StreakAnalyzer.find_mismatches to record which date it was called
    with for each fixture.
    """
    from engine.streaks.analyzer import StreakAnalyzer

    target_date = date(2025, 12, 1)
    tomorrow = target_date + timedelta(days=1)
    matches, teams = _generate_fake_openfootball_season()
    _add_upcoming_fixtures(matches, target_date, [(teams[0], teams[1])])
    _add_upcoming_fixtures(matches, tomorrow, [(teams[2], teams[3])])
    history = _generate_fake_xgabora_history(teams)

    calls = []
    original_find_mismatches = StreakAnalyzer.find_mismatches

    def instrumented_find_mismatches(self, home_team, away_team, as_of):
        calls.append((home_team, away_team, as_of))
        return original_find_mismatches(self, home_team, away_team, as_of)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch("engine.sources.xgabora_match_data_source.XgaboraMatchDataSource.fetch_season", return_value=history), \
             patch.object(StreakAnalyzer, "find_mismatches", instrumented_find_mismatches), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date, leagues=["epl"])

    team0_1_calls = [c for c in calls if c[0] == teams[0] and c[1] == teams[1]]
    team2_3_calls = [c for c in calls if c[0] == teams[2] and c[1] == teams[3]]

    assert len(team0_1_calls) > 0, f"Expected a call for {teams[0]} vs {teams[1]}"
    assert len(team2_3_calls) > 0, f"Expected a call for {teams[2]} vs {teams[3]}"

    assert team0_1_calls[0][2] == target_date, (
        f"Expected {teams[0]} vs {teams[1]} (scheduled on {target_date}) to use "
        f"as_of={target_date}, got {team0_1_calls[0][2]}"
    )
    assert team2_3_calls[0][2] == tomorrow, (
        f"Expected {teams[2]} vs {teams[3]} (scheduled on {tomorrow}) to use "
        f"as_of={tomorrow} (its OWN date), not the window start {target_date} - "
        f"got {team2_3_calls[0][2]}"
    )

    print(f"PASS: test_per_fixture_streak_analysis_uses_own_match_date_not_window_start "
          f"(fixture 1 as_of={team0_1_calls[0][2]}, fixture 2 as_of={team2_3_calls[0][2]})")


def test_pipeline_writes_fixtures_grouped_by_date_in_html():
    """End-to-end check that fixtures on different days within the
    window actually end up in different date-grouped sections of the
    rendered HTML, not all merged into one section.
    """
    target_date = date(2025, 12, 1)
    tomorrow = target_date + timedelta(days=1)
    matches, teams = _generate_fake_openfootball_season()
    _add_upcoming_fixtures(matches, target_date, [(teams[0], teams[1])])
    _add_upcoming_fixtures(matches, tomorrow, [(teams[2], teams[3])])
    history = _generate_fake_xgabora_history(teams)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch("engine.sources.xgabora_match_data_source.XgaboraMatchDataSource.fetch_season", return_value=history), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date, leagues=["epl"])

        with open(html_path) as f:
            html = f.read()

    assert "Today" in html
    assert "Tomorrow" in html

    print("PASS: test_pipeline_writes_fixtures_grouped_by_date_in_html")


def test_multi_league_pipeline_covers_all_leagues_by_default():
    """When no explicit leagues list is passed, run() should iterate
    over all 5 top leagues (see LEAGUE_CODES), not just EPL - this is
    the core multi-league capability added after the project's
    original EPL-only scope.
    """
    from engine.sources.openfootball_source import LEAGUE_CODES

    target_date = date(2025, 12, 1)
    matches, teams = _generate_fake_openfootball_season()
    _add_upcoming_fixtures(matches, target_date, [(teams[0], teams[1])])
    history = _generate_fake_xgabora_history(teams)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch("engine.sources.xgabora_match_data_source.XgaboraMatchDataSource.fetch_season", return_value=history), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date)  # no leagues= arg - should default to all 5

        with open(predictions_path) as f:
            data = json.load(f)
        # The same 1 mocked fixture is returned for every league (since
        # _fetch_season_raw is mocked identically regardless of which
        # league argument it's called with) - so with 5 leagues, we
        # expect 5 total fixtures, not 1.
        assert data["fixture_count"] == len(LEAGUE_CODES), (
            f"Expected {len(LEAGUE_CODES)} fixtures (one per league), got {data['fixture_count']}"
        )

    print(f"PASS: test_multi_league_pipeline_covers_all_leagues_by_default (fixture_count={data['fixture_count']})")


def test_pipeline_handles_no_fixtures_today():
    target_date = date(2025, 12, 25)
    matches, teams = _generate_fake_openfootball_season()

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date)

        with open(predictions_path) as f:
            data = json.load(f)
        assert data["fixture_count"] == 0

        with open(html_path) as f:
            html = f.read()
        assert "<!DOCTYPE html>" in html

    print("PASS: test_pipeline_handles_no_fixtures_today")


def test_pipeline_creates_missing_output_directories():
    """Regression test for a REAL production crash: a fresh GitHub
    Actions checkout does not have data/predictions/ or docs/ as
    actual directories, since git does not track empty folders. The
    original code assumed these directories already existed and
    crashed with FileNotFoundError on the very first real run. This
    test specifically points PREDICTIONS_JSON_PATH/HTML_OUTPUT_PATH at
    subdirectories that do NOT exist yet (unlike the other tests in
    this file, which point directly at a tempdir that already exists
    as a directory, and therefore never actually exercised this bug).
    """
    target_date = date(2025, 12, 25)
    matches, teams = _generate_fake_openfootball_season()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Deliberately nested, non-existent subdirectories - matching
        # the real repo's data/predictions/ and docs/ structure, which
        # does not exist on a fresh checkout.
        predictions_path = os.path.join(tmpdir, "data", "predictions", "latest.json")
        html_path = os.path.join(tmpdir, "docs", "index.html")

        assert not os.path.exists(os.path.dirname(predictions_path))
        assert not os.path.exists(os.path.dirname(html_path))

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date)  # should not raise FileNotFoundError

        assert os.path.exists(predictions_path), "predictions.json should exist even though its directory didn't"
        assert os.path.exists(html_path), "index.html should exist even though its directory didn't"

    print("PASS: test_pipeline_creates_missing_output_directories")


def test_pipeline_skips_league_with_insufficient_historical_data():
    """UPDATED BEHAVIOR: with multi-league support, one league having
    insufficient historical data should no longer abort the entire
    run (sys.exit) - it should skip just that league and continue,
    since a data problem in one league (e.g. Ligue 1) shouldn't
    prevent the dashboard from showing correct results for the other
    four. Pinned to a single league here to test this in isolation:
    with only "epl" requested and insufficient data for it, the run
    completes successfully with zero fixtures/mismatches rather than
    raising, since there's no other league to fall back to.
    """
    target_date = date(2025, 12, 1)
    matches, teams = _generate_fake_openfootball_season()
    _add_upcoming_fixtures(matches, target_date, [(teams[0], teams[1])])
    tiny_history = _generate_fake_xgabora_history(teams, n_matches=3)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch("engine.sources.xgabora_match_data_source.XgaboraMatchDataSource.fetch_season", return_value=tiny_history), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date, leagues=["epl"])  # should NOT raise SystemExit anymore

        with open(predictions_path) as f:
            data = json.load(f)
        assert data["total_mismatches"] == 0, "Expected no mismatches computed for the skipped league"

    print("PASS: test_pipeline_skips_league_with_insufficient_historical_data")


def test_pipeline_handles_streak_source_fetch_failure_gracefully():
    target_date = date(2025, 12, 1)
    matches, teams = _generate_fake_openfootball_season()
    _add_upcoming_fixtures(matches, target_date, [(teams[0], teams[1])])
    history = _generate_fake_xgabora_history(teams, n_matches=40)

    call_count = {"n": 0}

    def flaky_fetch_season(self, season):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("simulated transient failure")
        return history

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")

        with patch("engine.sources.openfootball_source.OpenFootballSource._fetch_season_raw", return_value=matches), \
             patch("engine.sources.xgabora_match_data_source.XgaboraMatchDataSource.fetch_season", flaky_fetch_season), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path):
            main_module.run(target_date, leagues=["epl"])

        assert os.path.exists(predictions_path)

    print("PASS: test_pipeline_handles_streak_source_fetch_failure_gracefully")


if __name__ == "__main__":
    test_full_pipeline_runs_without_crashing()
    test_per_fixture_streak_analysis_uses_own_match_date_not_window_start()
    test_pipeline_writes_fixtures_grouped_by_date_in_html()
    test_multi_league_pipeline_covers_all_leagues_by_default()
    test_pipeline_handles_no_fixtures_today()
    test_pipeline_creates_missing_output_directories()
    test_pipeline_skips_league_with_insufficient_historical_data()
    test_pipeline_handles_streak_source_fetch_failure_gracefully()
    print("\nAll tests passed.")
