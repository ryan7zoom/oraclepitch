"""
tests/test_football_data_co_uk_source.py

Tests for FootballDataCoUkSource's CSV parsing, using a realistic mocked
CSV shaped exactly like football-data.co.uk's documented schema
(confirmed against multiple independent third-party descriptions of the
same columns during development - see the module docstring in
football_data_co_uk_source.py for the verification caveats).
"""

import sys
import os
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sources.football_data_co_uk_source import (
    FootballDataCoUkSource, _season_code, _parse_date, _parse_float, _parse_int,
)


SAMPLE_CSV = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A,B365>2.5,B365<2.5
E0,11/08/24,Arsenal,Chelsea,2,1,H,1,0,H,M Oliver,15,10,6,3,10,12,7,4,2,1,0,0,1.85,3.6,4.2,1.9,1.95
E0,12/08/24,Liverpool,Man City,1,1,D,0,1,A,A Taylor,12,14,4,5,8,9,5,6,1,3,0,0,2.5,3.4,2.9,1.7,2.1
E0,18/08/24,,Everton,0,0,D,0,0,D,K Friend,10,10,3,3,5,5,4,4,0,0,0,0,2.0,3.3,3.8,1.85,1.95
"""


def make_mock_response(content_bytes, status_code=200):
    mock_resp = MagicMock()
    mock_resp.content = content_bytes
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_season_code_conversion():
    assert _season_code(2024) == "2425"
    assert _season_code(2023) == "2324"
    assert _season_code(1999) == "9900"  # year-boundary edge case
    print("PASS: test_season_code_conversion")


def test_parse_date_handles_both_formats():
    assert _parse_date("11/08/24") == date(2024, 8, 11)
    assert _parse_date("11/08/2024") == date(2024, 8, 11)
    assert _parse_date("") is None
    assert _parse_date("garbage") is None
    print("PASS: test_parse_date_handles_both_formats")


def test_parse_float_and_int_handle_empty_strings():
    assert _parse_float("") is None
    assert _parse_float("1.85") == 1.85
    assert _parse_int("") is None
    assert _parse_int("7") == 7
    assert _parse_int("7.0") == 7  # some columns have trailing .0
    print("PASS: test_parse_float_and_int_handle_empty_strings")


@patch("engine.sources.football_data_co_uk_source.requests.get")
def test_fetch_season_parses_goals_shots_corners_odds(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_CSV.encode("utf-8"))
    source = FootballDataCoUkSource()

    results = source.fetch_season(2024)

    # Row 3 (Everton, missing HomeTeam) should be skipped
    assert len(results) == 2, f"Expected 2 valid rows (1 skipped for missing team), got {len(results)}"

    first = results[0]
    assert first.home_team == "Arsenal"
    assert first.away_team == "Chelsea"
    assert first.date == date(2024, 8, 11)
    assert first.home_goals == 2
    assert first.away_goals == 1
    assert first.home_shots_on_target == 6
    assert first.away_shots_on_target == 3
    assert first.home_corners == 7
    assert first.away_corners == 4
    assert first.odds_home_win == 1.85
    assert first.odds_draw == 3.6
    assert first.odds_away_win == 4.2
    assert first.odds_over_2_5 == 1.9
    assert first.odds_under_2_5 == 1.95
    assert first.bookmaker_used == "B365"

    print(f"PASS: test_fetch_season_parses_goals_shots_corners_odds ({len(results)} valid rows)")


@patch("engine.sources.football_data_co_uk_source.requests.get")
def test_fetch_season_falls_back_through_bookmaker_priority(mock_get):
    """If B365 odds are missing but a lower-priority bookmaker (WH) has
    them, the parser should fall back rather than report no odds at all.
    """
    csv_with_missing_b365 = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,WHH,WHD,WHA\n"
        "E0,11/08/24,Arsenal,Chelsea,2,1,1.9,3.5,4.0\n"
    )
    mock_get.return_value = make_mock_response(csv_with_missing_b365.encode("utf-8"))
    source = FootballDataCoUkSource()

    results = source.fetch_season(2024)
    assert len(results) == 1
    assert results[0].odds_home_win == 1.9
    assert results[0].bookmaker_used == "WH"
    print("PASS: test_fetch_season_falls_back_through_bookmaker_priority")


@patch("engine.sources.football_data_co_uk_source.requests.get")
def test_fetch_season_handles_completely_missing_odds(mock_get):
    """If NO bookmaker columns are present at all, odds fields should be
    None rather than crash - this can legitimately happen for very old
    seasons or lower divisions with sparser odds coverage.
    """
    csv_no_odds = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
        "E0,11/08/24,Arsenal,Chelsea,2,1\n"
    )
    mock_get.return_value = make_mock_response(csv_no_odds.encode("utf-8"))
    source = FootballDataCoUkSource()

    results = source.fetch_season(2024)
    assert len(results) == 1
    assert results[0].odds_home_win is None
    assert results[0].bookmaker_used is None
    assert results[0].home_goals == 2  # goals should still parse fine
    print("PASS: test_fetch_season_handles_completely_missing_odds")


@patch("engine.sources.football_data_co_uk_source.requests.get")
def test_fetch_season_handles_latin1_encoding(mock_get):
    """Older seasons' CSVs are sometimes latin-1 encoded rather than
    utf-8 (e.g. accented characters in referee names) - confirm this
    doesn't crash the parser.
    """
    csv_text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,Referee\nE0,11/08/24,Arsenal,Chelsea,2,1,J.\xe9douard\n"
    mock_get.return_value = make_mock_response(csv_text.encode("latin-1"))
    source = FootballDataCoUkSource()

    results = source.fetch_season(2024)
    assert len(results) == 1
    print("PASS: test_fetch_season_handles_latin1_encoding")


if __name__ == "__main__":
    test_season_code_conversion()
    test_parse_date_handles_both_formats()
    test_parse_float_and_int_handle_empty_strings()
    test_fetch_season_parses_goals_shots_corners_odds()
    test_fetch_season_falls_back_through_bookmaker_priority()
    test_fetch_season_handles_completely_missing_odds()
    test_fetch_season_handles_latin1_encoding()
    print("\nAll tests passed.")
