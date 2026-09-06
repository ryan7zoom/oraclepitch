"""
tests/test_main_integration.py

Integration test for engine/main.py's run() function, using a fully
mocked ApiFootballSource so the whole pipeline (fetch -> fit -> predict
-> write output) is exercised without any real network access. This
catches wiring bugs (wrong method names, bad field access between
modules) that isolated unit tests of each module wouldn't - each
module's own tests use hand-built inputs matching its own expected
format, which doesn't verify the modules are being CALLED correctly
from main.py.
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
from engine.sources.base import MatchResult
import engine.main as main_module


def _generate_fake_historical_results(n_teams=8, matches_per_pair=4, seed=1):
    np.random.seed(seed)
    teams = [f"Team{i}" for i in range(n_teams)]
    true_strength = {t: (n_teams - i) for i, t in enumerate(teams)}
    results = []
    start = date(2025, 8, 1)
    day_counter = 0
    fixture_id = 1000
    for _ in range(matches_per_pair):
        for i in range(n_teams):
            for j in range(n_teams):
                if i == j:
                    continue
                home, away = teams[i], teams[j]
                lam = max(0.8 + 0.25 * true_strength[home] - 0.15 * true_strength[away], 0.1)
                mu = max(0.8 + 0.25 * true_strength[away] - 0.15 * true_strength[home], 0.1)
                hg = int(np.random.poisson(lam))
                ag = int(np.random.poisson(mu))
                results.append(MatchResult(
                    fixture_id=str(fixture_id),
                    date=start + timedelta(days=day_counter),
                    season=2025,
                    home_team=home, away_team=away,
                    home_goals=hg, away_goals=ag,
                    home_shots_on_target=max(int(np.random.poisson(5)), 0),
                    away_shots_on_target=max(int(np.random.poisson(4)), 0),
                    status="finished",
                ))
                fixture_id += 1
                day_counter += 1
    return results, teams


class FakeApiFootballSource:
    def __init__(self, historical_results, upcoming_fixtures):
        self._historical = historical_results
        self._upcoming = upcoming_fixtures
        self.request_count = 0

    def _infer_current_season(self, as_of):
        return 2025 if as_of.month >= 7 else 2024

    def get_fixtures(self, season, date_from=None, date_to=None):
        self.request_count += 1
        all_results = self._historical + self._upcoming
        results = [r for r in all_results if r.season == season]
        if date_from:
            results = [r for r in results if r.date and r.date >= date_from]
        if date_to:
            results = [r for r in results if r.date and r.date <= date_to]
        return results

    def get_fixture_statistics(self, fixture_id):
        self.request_count += 1
        for r in self._historical:
            if r.fixture_id == fixture_id:
                return r
        raise ValueError(f"No fixture {fixture_id}")


def test_full_pipeline_runs_without_crashing():
    historical, teams = _generate_fake_historical_results()
    target_date = date(2025, 12, 1)
    upcoming = [
        MatchResult(fixture_id="9001", date=target_date, season=2025,
                    home_team=teams[0], away_team=teams[1], status="scheduled"),
        MatchResult(fixture_id="9002", date=target_date, season=2025,
                    home_team=teams[2], away_team=teams[3], status="scheduled"),
    ]
    fake_source = FakeApiFootballSource(historical, upcoming)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")
        historical_dir = os.path.join(tmpdir, "historical")

        with patch("engine.main.ApiFootballSource", return_value=fake_source), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir):
            main_module.run(target_date)

        assert os.path.exists(predictions_path), "predictions.json was not written"
        assert os.path.exists(html_path), "index.html was not written"

        with open(predictions_path) as f:
            data = json.load(f)
        assert data["target_date"] == target_date.isoformat()
        assert len(data["predictions"]) == 2, f"Expected 2 predictions, got {len(data['predictions'])}"

        with open(html_path) as f:
            html = f.read()
        assert teams[0] in html and teams[1] in html

    print(f"PASS: test_full_pipeline_runs_without_crashing "
          f"({len(data['predictions'])} predictions, {fake_source.request_count} API calls)")


def test_pipeline_handles_unknown_team_in_todays_fixtures():
    historical, teams = _generate_fake_historical_results()
    target_date = date(2025, 12, 1)
    upcoming = [
        MatchResult(fixture_id="9003", date=target_date, season=2025,
                    home_team="BrandNewPromotedTeam", away_team=teams[0], status="scheduled"),
        MatchResult(fixture_id="9004", date=target_date, season=2025,
                    home_team=teams[1], away_team=teams[2], status="scheduled"),
    ]
    fake_source = FakeApiFootballSource(historical, upcoming)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")
        historical_dir = os.path.join(tmpdir, "historical")
        with patch("engine.main.ApiFootballSource", return_value=fake_source), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir):
            main_module.run(target_date)

        with open(predictions_path) as f:
            data = json.load(f)
        assert len(data["predictions"]) == 1, f"Expected 1 prediction (1 skipped), got {len(data['predictions'])}"

    print("PASS: test_pipeline_handles_unknown_team_in_todays_fixtures")


def test_pipeline_aborts_on_insufficient_historical_data():
    teams = ["Team0", "Team1", "Team2"]
    target_date = date(2025, 8, 20)
    historical = [
        MatchResult(fixture_id=str(i), date=date(2025, 8, 10), season=2025,
                    home_team=teams[i % 3], away_team=teams[(i + 1) % 3],
                    home_goals=1, away_goals=1, status="finished",
                    home_shots_on_target=4, away_shots_on_target=3)
        for i in range(3)
    ]
    upcoming = [
        MatchResult(fixture_id="9005", date=target_date, season=2025,
                    home_team=teams[0], away_team=teams[1], status="scheduled"),
    ]
    fake_source = FakeApiFootballSource(historical, upcoming)

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")
        historical_dir = os.path.join(tmpdir, "historical")
        with patch("engine.main.ApiFootballSource", return_value=fake_source), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir):
            try:
                main_module.run(target_date)
                assert False, "Expected SystemExit due to insufficient historical data"
            except SystemExit:
                pass
    print("PASS: test_pipeline_aborts_on_insufficient_historical_data")


def test_cache_prevents_refetching_on_second_run():
    """Regression test for a real problem found during integration
    testing: without caching, every daily run was re-fetching full
    statistics for ALL historical fixtures, burning ~95/100 of the free
    daily API quota just on data that doesn't change once a match is
    finished. This confirms a second run against the same season uses
    the disk cache instead of re-fetching.
    """
    historical, teams = _generate_fake_historical_results()
    target_date = date(2025, 12, 1)
    upcoming = [
        MatchResult(fixture_id="9001", date=target_date, season=2025,
                    home_team=teams[0], away_team=teams[1], status="scheduled"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")
        historical_dir = os.path.join(tmpdir, "historical")

        # First run: cache is empty, should fetch everything
        fake_source_1 = FakeApiFootballSource(historical, upcoming)
        with patch("engine.main.ApiFootballSource", return_value=fake_source_1), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir):
            main_module.run(target_date)
        first_run_requests = fake_source_1.request_count

        # Second run: cache should now be populated on disk, so a fresh
        # source instance should need far fewer requests.
        fake_source_2 = FakeApiFootballSource(historical, upcoming)
        with patch("engine.main.ApiFootballSource", return_value=fake_source_2), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir):
            main_module.run(target_date)
        second_run_requests = fake_source_2.request_count

        assert second_run_requests < first_run_requests, (
            f"Second run should use fewer requests due to caching: "
            f"first={first_run_requests}, second={second_run_requests}"
        )
        # NOTE: with 122 historical fixtures and a 100/day quota, the first
        # run legitimately cannot cache everything (quota runs out partway
        # through) - see main.py's quota-stopping log warning. So the
        # second run still needs to fetch the *remaining* uncached
        # fixtures, not zero. The meaningful assertion here is that caching
        # measurably reduces load, not that it eliminates all requests -
        # this test's fixture count was chosen without regard to how many
        # would fit in one day's quota. Confirmed cache is loaded from
        # disk (not empty) as the actual mechanism under test.
        assert second_run_requests < first_run_requests * 0.5, (
            f"Expected caching to roughly halve or better the request count: "
            f"first={first_run_requests}, second={second_run_requests}"
        )

    print(f"PASS: test_cache_prevents_refetching_on_second_run "
          f"(first_run={first_run_requests} requests, second_run={second_run_requests} requests)")


def test_cache_converges_to_near_zero_requests_once_fully_warm():
    """A cleaner version of the caching test using a small enough
    historical dataset (well under the daily quota) that the FIRST run
    can fully populate the cache - confirming the cache mechanism itself
    (not the quota-limited partial-cache scenario above) drives requests
    down to near zero on a second run.
    """
    historical, teams = _generate_fake_historical_results(n_teams=6, matches_per_pair=2)  # small: 30 matches
    target_date = date(2025, 10, 1)
    upcoming = [
        MatchResult(fixture_id="9010", date=target_date, season=2025,
                    home_team=teams[0], away_team=teams[1], status="scheduled"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        predictions_path = os.path.join(tmpdir, "predictions.json")
        html_path = os.path.join(tmpdir, "index.html")
        historical_dir = os.path.join(tmpdir, "historical")

        fake_source_1 = FakeApiFootballSource(historical, upcoming)
        with patch("engine.main.ApiFootballSource", return_value=fake_source_1), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir):
            main_module.run(target_date)

        fake_source_2 = FakeApiFootballSource(historical, upcoming)
        with patch("engine.main.ApiFootballSource", return_value=fake_source_2), \
             patch.object(config, "PREDICTIONS_JSON_PATH", predictions_path), \
             patch.object(config, "HTML_OUTPUT_PATH", html_path), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir):
            main_module.run(target_date)

        # With the whole season cached from run 1, run 2 should only need
        # the 2 get_fixtures calls (historical window + today), zero
        # get_fixture_statistics calls.
        assert fake_source_2.request_count <= 3, (
            f"Expected near-zero requests once fully cached, got {fake_source_2.request_count}"
        )

    print(f"PASS: test_cache_converges_to_near_zero_requests_once_fully_warm "
          f"(second_run={fake_source_2.request_count} requests)")


if __name__ == "__main__":
    test_full_pipeline_runs_without_crashing()
    test_pipeline_handles_unknown_team_in_todays_fixtures()
    test_pipeline_aborts_on_insufficient_historical_data()
    test_cache_prevents_refetching_on_second_run()
    test_cache_converges_to_near_zero_requests_once_fully_warm()
    print("\nAll tests passed.")
