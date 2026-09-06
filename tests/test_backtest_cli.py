"""
tests/test_backtest_cli.py

Tests the `python -m engine.backtest.simulator --start Y --end Y` CLI
path used by .github/workflows/backtest_run.yml, using a mocked
FootballDataCoUkSource (the real odds source now wired in - see
simulator.py's updated module docstring). Confirms:
- The pipeline runs end-to-end without crashing.
- Real bets ARE placed for match_winner and match_total_goals (over_2.5),
  since those now have genuine odds coverage.
- The report honestly states which markets still have NO odds coverage
  (double_chance, team_total_goals, shots_on_target), rather than
  silently reporting zero bets for them with no explanation.
"""

import sys
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import config
from engine.sources.football_data_co_uk_source import HistoricalMatchOdds
import engine.backtest.simulator as simulator_module


def _generate_fake_season_with_odds(season, n_teams=10, matches_per_pair=2, seed=5):
    """Generate synthetic HistoricalMatchOdds records - goals AND odds,
    since the real data source provides both from the same CSV row.
    Odds are generated as a simple noisy function of true team strength
    so they're at least directionally realistic (favorites get shorter
    odds), rather than pure random noise that would never produce any
    edge for the model to find.
    """
    np.random.seed(seed)
    teams = [f"Team{i}" for i in range(n_teams)]
    true_strength = {t: (n_teams - i) for i, t in enumerate(teams)}
    results = []
    start = date(season, 8, 1)
    day_counter = 0

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

                strength_diff = true_strength[home] - true_strength[away]
                home_odds = max(1.3, 2.5 - 0.15 * strength_diff)
                away_odds = max(1.3, 2.5 + 0.15 * strength_diff)
                draw_odds = 3.4

                results.append(HistoricalMatchOdds(
                    date=start + timedelta(days=day_counter),
                    home_team=home, away_team=away,
                    home_goals=hg, away_goals=ag,
                    home_corners=max(int(np.random.poisson(5)), 0),
                    away_corners=max(int(np.random.poisson(4)), 0),
                    odds_home_win=round(home_odds, 2),
                    odds_draw=draw_odds,
                    odds_away_win=round(away_odds, 2),
                    odds_over_2_5=1.9, odds_under_2_5=1.95,
                    bookmaker_used="B365",
                ))
                day_counter += 1
    return results


class FakeFootballDataCoUkSource:
    def __init__(self, results_by_season):
        self._results_by_season = results_by_season

    def fetch_season(self, start_year):
        if start_year not in self._results_by_season:
            raise Exception(f"No data for season {start_year}")
        return self._results_by_season[start_year]


def test_backtest_cli_places_real_bets_with_odds_coverage():
    season = 2024
    results = {season: _generate_fake_season_with_odds(season)}
    fake_source = FakeFootballDataCoUkSource(results)

    with tempfile.TemporaryDirectory() as tmpdir:
        historical_dir = os.path.join(tmpdir, "historical")

        with patch("engine.sources.football_data_co_uk_source.FootballDataCoUkSource", return_value=fake_source), \
             patch.object(config, "HISTORICAL_DATA_DIR", historical_dir), \
             patch("sys.argv", ["simulator.py", "--start", str(season), "--end", str(season)]):
            simulator_module._cli_main()

        report_path = os.path.join(tmpdir, "backtest_report.txt")
        assert os.path.exists(report_path), "backtest_report.txt was not written"
        with open(report_path) as f:
            report_text = f.read()

        assert "ODDS COVERAGE NOTE" in report_text
        assert "match_winner" in report_text
        assert "NO real odds source connected" in report_text

        assert "Total bets: 0" not in report_text, (
            "Expected real bets to be placed with genuine odds coverage - "
            "report showing zero bets would mean the odds wiring isn't working"
        )

        # Corners has no odds source but IS backed by real historical
        # data (home_corners/away_corners set above) - confirm the
        # calibration section appears with real numbers, not "skipped".
        assert "Corners model calibration" in report_text
        assert "skipped - insufficient corners data" not in report_text, (
            "Expected enough corners data in this test fixture for a real "
            "calibration check to run, not be skipped"
        )

    print("PASS: test_backtest_cli_places_real_bets_with_odds_coverage")


def test_backtest_cli_only_bets_covered_markets():
    """Directly verify via run_backtest (bypassing the CLI's file I/O)
    that bets only appear for match_winner and match_total_goals -
    never double_chance, team_total_goals, or shots_on_target, since
    those have no real odds source connected.
    """
    from engine.backtest.simulator import run_backtest
    from engine.prediction.dixon_coles import MatchInput

    season = 2024
    results = _generate_fake_season_with_odds(season, n_teams=8, matches_per_pair=3)
    matches = [MatchInput(r.home_team, r.away_team, r.home_goals, r.away_goals, r.date) for r in results]
    odds_lookup = {(r.home_team, r.away_team, r.date): r for r in results}

    def odds_provider(home_team, away_team, match_date, market, selection):
        record = odds_lookup.get((home_team, away_team, match_date))
        if record is None:
            return None
        if market == "match_winner":
            return {"home_win": record.odds_home_win, "draw": record.odds_draw,
                    "away_win": record.odds_away_win}.get(selection)
        if market == "match_total_goals" and selection == "over_2.5":
            return record.odds_over_2_5
        return None

    report = run_backtest(matches, odds_provider=odds_provider, min_training_matches=30, refit_every_n_matches=5)

    markets_with_bets = {b.market for b in report.bets}
    assert markets_with_bets.issubset({"match_winner", "match_total_goals"}), (
        f"Found bets in uncovered markets: {markets_with_bets - {'match_winner', 'match_total_goals'}}"
    )
    if "match_total_goals" in markets_with_bets:
        selections_used = {b.selection for b in report.bets if b.market == "match_total_goals"}
        assert selections_used == {"over_2.5"}, (
            f"Only over_2.5 has real odds - found bets on {selections_used}"
        )

    print(f"PASS: test_backtest_cli_only_bets_covered_markets (markets found: {markets_with_bets})")


if __name__ == "__main__":
    test_backtest_cli_places_real_bets_with_odds_coverage()
    test_backtest_cli_only_bets_covered_markets()
    print("\nAll tests passed.")
