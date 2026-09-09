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
            main_module.run(target_date)

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


def test_pipeline_aborts_on_insufficient_historical_data():
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
            try:
                main_module.run(target_date)
                assert False, "Expected SystemExit due to insufficient historical data"
            except SystemExit:
                pass

    print("PASS: test_pipeline_aborts_on_insufficient_historical_data")


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
            main_module.run(target_date)

        assert os.path.exists(predictions_path)

    print("PASS: test_pipeline_handles_streak_source_fetch_failure_gracefully")


if __name__ == "__main__":
    test_full_pipeline_runs_without_crashing()
    test_pipeline_handles_no_fixtures_today()
    test_pipeline_aborts_on_insufficient_historical_data()
    test_pipeline_handles_streak_source_fetch_failure_gracefully()
    print("\nAll tests passed.")
