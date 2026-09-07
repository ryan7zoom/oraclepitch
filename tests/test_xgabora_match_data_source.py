"""
tests/test_xgabora_match_data_source.py

Tests for XgaboraMatchDataSource, using a mocked CSV shaped exactly like
the real xgabora/Club-Football-Match-Data file (confirmed via direct
inspection - see the module's docstring for verification details, not
just an assumed schema).
"""

import sys
import os
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sources.xgabora_match_data_source import (
    XgaboraMatchDataSource, _parse_match_date, _parse_int_from_float_string, _parse_float,
)


SAMPLE_CSV = """Division,MatchDate,MatchTime,HomeTeam,AwayTeam,HomeElo,AwayElo,Form3Home,Form5Home,Form3Away,Form5Away,FTHome,FTAway,FTResult,HTHome,HTAway,HTResult,HomeShots,AwayShots,HomeTarget,AwayTarget,HomeFouls,AwayFouls,HomeCorners,AwayCorners,HomeYellow,AwayYellow,HomeRed,AwayRed,OddHome,OddDraw,OddAway,MaxHome,MaxDraw,MaxAway,Over25,Under25,MaxOver25,MaxUnder25,HandiSize,HandiHome,HandiAway,C_LTH,C_LTA,C_VHD,C_VAD,C_HTB,C_PHB
E0,2024-08-16,20:00:00,Man United,Fulham,1900.0,1650.0,3.0,6.0,3.0,6.0,1.0,0.0,H,0.0,0.0,D,17.0,14.0,4.0,7.0,6.0,7.0,2.0,3.0,0.0,2.0,0.0,0.0,1.5,4.2,6.5,1.55,4.3,6.8,1.9,1.95,1.95,2.0,-1.0,1.9,1.9,,,,,,
F1,2024-08-16,20:00:00,Marseille,Troyes,1700.0,1600.0,0.0,0.0,0.0,0.0,2.0,0.0,H,1.0,0.0,H,,,,,,,,,,,,,1.65,3.3,4.3,,,,,,,,,,,,,,,,
E0,2023-08-11,15:00:00,Arsenal,Chelsea,1850.0,1800.0,2.0,4.0,3.0,5.0,3.0,1.0,H,1.0,0.0,H,15.0,10.0,6.0,3.0,8.0,7.0,7.0,4.0,2.0,1.0,0.0,0.0,1.85,3.6,4.2,1.9,3.7,4.3,1.9,1.95,1.95,2.0,-0.5,1.83,2.03,,,,,,
E0,2024-08-17,17:30:00,Liverpool,Ipswich,2000.0,1400.0,,,,,2.0,0.0,H,1.0,0.0,H,,,,,,,,,,,,,1.2,6.5,15.0,,,,,,,,,,,,,,,,
"""


def make_mock_response(content_bytes, status_code=200):
    mock_resp = MagicMock()
    mock_resp.content = content_bytes
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_parse_match_date_uses_iso_format():
    assert _parse_match_date("2024-08-16") == date(2024, 8, 16)
    assert _parse_match_date("") is None
    assert _parse_match_date("16/08/2024") is None
    print("PASS: test_parse_match_date_uses_iso_format")


def test_parse_int_from_float_string_handles_trailing_decimal():
    assert _parse_int_from_float_string("3.0") == 3
    assert _parse_int_from_float_string("0.0") == 0
    assert _parse_int_from_float_string("") is None
    print("PASS: test_parse_int_from_float_string_handles_trailing_decimal")


@patch("engine.sources.xgabora_match_data_source.requests.get")
def test_fetch_season_filters_to_correct_division_and_date_range(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_CSV.encode("utf-8"))
    source = XgaboraMatchDataSource()

    results = source.fetch_season(2024)

    assert len(results) == 2, f"Expected 2 matches for 2024-25 season, got {len(results)}"
    team_pairs = {(r.home_team, r.away_team) for r in results}
    assert ("Man United", "Fulham") in team_pairs
    assert ("Liverpool", "Ipswich") in team_pairs
    assert ("Marseille", "Troyes") not in team_pairs
    assert ("Arsenal", "Chelsea") not in team_pairs
    print(f"PASS: test_fetch_season_filters_to_correct_division_and_date_range ({team_pairs})")


@patch("engine.sources.xgabora_match_data_source.requests.get")
def test_fetch_season_parses_all_stat_fields_correctly(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_CSV.encode("utf-8"))
    source = XgaboraMatchDataSource()

    results = source.fetch_season(2024)
    man_utd_match = next(r for r in results if r.home_team == "Man United")

    assert man_utd_match.home_goals == 1
    assert man_utd_match.away_goals == 0
    assert man_utd_match.home_shots_on_target == 4
    assert man_utd_match.away_shots_on_target == 7
    assert man_utd_match.home_corners == 2
    assert man_utd_match.away_corners == 3
    assert man_utd_match.odds_home_win == 1.5
    assert man_utd_match.odds_draw == 4.2
    assert man_utd_match.odds_away_win == 6.5
    assert man_utd_match.odds_over_2_5 == 1.9
    assert man_utd_match.odds_under_2_5 == 1.95
    print("PASS: test_fetch_season_parses_all_stat_fields_correctly")


@patch("engine.sources.xgabora_match_data_source.requests.get")
def test_fetch_season_handles_missing_stat_columns_gracefully(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_CSV.encode("utf-8"))
    source = XgaboraMatchDataSource()

    results = source.fetch_season(2024)
    liverpool_match = next(r for r in results if r.home_team == "Liverpool")

    assert liverpool_match.home_goals == 2
    assert liverpool_match.away_goals == 0
    assert liverpool_match.home_shots_on_target is None
    assert liverpool_match.home_corners is None
    assert liverpool_match.odds_home_win == 1.2
    print("PASS: test_fetch_season_handles_missing_stat_columns_gracefully")


@patch("engine.sources.xgabora_match_data_source.requests.get")
def test_fetch_season_caches_and_does_not_redownload(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_CSV.encode("utf-8"))
    source = XgaboraMatchDataSource()

    source.fetch_season(2024)
    source.fetch_season(2023)
    source.fetch_season(2024)

    assert mock_get.call_count == 1, f"Expected exactly 1 download call, got {mock_get.call_count}"
    print("PASS: test_fetch_season_caches_and_does_not_redownload")


@patch("engine.sources.xgabora_match_data_source.requests.get")
def test_produces_same_type_as_football_data_co_uk_source(mock_get):
    from engine.sources.football_data_co_uk_source import HistoricalMatchOdds

    mock_get.return_value = make_mock_response(SAMPLE_CSV.encode("utf-8"))
    source = XgaboraMatchDataSource()
    results = source.fetch_season(2024)

    assert len(results) > 0
    assert all(isinstance(r, HistoricalMatchOdds) for r in results)
    print("PASS: test_produces_same_type_as_football_data_co_uk_source")


if __name__ == "__main__":
    test_parse_match_date_uses_iso_format()
    test_parse_int_from_float_string_handles_trailing_decimal()
    test_fetch_season_filters_to_correct_division_and_date_range()
    test_fetch_season_parses_all_stat_fields_correctly()
    test_fetch_season_handles_missing_stat_columns_gracefully()
    test_fetch_season_caches_and_does_not_redownload()
    test_produces_same_type_as_football_data_co_uk_source()
    print("\nAll tests passed.")
