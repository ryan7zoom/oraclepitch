"""
tests/test_openfootball_source.py

Tests for OpenFootballSource, using mocked responses shaped exactly
like the real openfootball/football.json data.
"""

import sys
import os
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sources.openfootball_source import (
    OpenFootballSource, _season_folder, _extract_score, _parse_match_date,
    _normalize_team_name,
)


SAMPLE_SEASON_JSON = {
    "name": "English Premier League 2026/27",
    "matches": [
        {
            "round": "Matchday 1",
            "date": "2026-08-21",
            "time": "20:00",
            "team1": "Arsenal FC",
            "team2": "Coventry City FC",
            "score": {"ht": [2, 0], "ft": [3, 0]},
        },
        {
            "round": "Matchday 1",
            "date": "2026-08-22",
            "time": "12:30",
            "team1": "Hull City AFC",
            "team2": "Manchester United FC",
            "score": [2, 0],
        },
        {
            "round": "Matchday 29",
            "date": "2027-03-13",
            "time": "15:00",
            "team1": "Liverpool FC",
            "team2": "Ipswich Town FC",
        },
    ],
}


def make_mock_response(json_body):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_body
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_season_folder_formatting():
    assert _season_folder(2026) == "2026-27"
    assert _season_folder(2025) == "2025-26"
    assert _season_folder(1999) == "1999-00"
    print("PASS: test_season_folder_formatting")


def test_extract_score_handles_dict_shape():
    assert _extract_score({"score": {"ht": [1, 0], "ft": [3, 0]}}) == (3, 0)
    print("PASS: test_extract_score_handles_dict_shape")


def test_extract_score_handles_bare_array_shape():
    assert _extract_score({"score": [2, 0]}) == (2, 0)
    print("PASS: test_extract_score_handles_bare_array_shape")


def test_extract_score_returns_none_for_unplayed_match():
    assert _extract_score({}) == (None, None)
    assert _extract_score({"score": None}) == (None, None)
    print("PASS: test_extract_score_returns_none_for_unplayed_match")


def test_parse_match_date_iso_format():
    assert _parse_match_date("2026-08-21") == date(2026, 8, 21)
    assert _parse_match_date("") is None
    print("PASS: test_parse_match_date_iso_format")


@patch("engine.sources.openfootball_source.requests.get")
def test_get_fixtures_for_date_finds_played_match(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_SEASON_JSON)
    source = OpenFootballSource()

    results = source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026)

    assert len(results) == 1
    match = results[0]
    assert match.home_team == "Arsenal", f"Expected normalized name, got {match.home_team!r}"
    assert match.away_team == "Coventry", f"Expected normalized name, got {match.away_team!r}"
    assert match.home_goals == 3
    assert match.away_goals == 0
    assert match.status == "finished"
    print("PASS: test_get_fixtures_for_date_finds_played_match")


@patch("engine.sources.openfootball_source.requests.get")
def test_get_fixtures_for_date_finds_bare_array_score_match(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_SEASON_JSON)
    source = OpenFootballSource()

    results = source.get_fixtures_for_date(date(2026, 8, 22), season_start_year=2026)

    assert len(results) == 1
    assert results[0].home_goals == 2
    assert results[0].away_goals == 0
    assert results[0].status == "finished"
    print("PASS: test_get_fixtures_for_date_finds_bare_array_score_match")


@patch("engine.sources.openfootball_source.requests.get")
def test_get_fixtures_for_date_finds_upcoming_match(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_SEASON_JSON)
    source = OpenFootballSource()

    results = source.get_fixtures_for_date(date(2027, 3, 13), season_start_year=2026)

    assert len(results) == 1
    match = results[0]
    assert match.home_team == "Liverpool", f"Expected normalized name, got {match.home_team!r}"
    assert match.status == "scheduled"
    assert match.home_goals is None, "Unplayed match should have None goals, not 0"
    assert match.away_goals is None
    print("PASS: test_get_fixtures_for_date_finds_upcoming_match")


@patch("engine.sources.openfootball_source.requests.get")
def test_get_fixtures_for_date_returns_empty_for_no_matches_that_day(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_SEASON_JSON)
    source = OpenFootballSource()

    results = source.get_fixtures_for_date(date(2026, 12, 25), season_start_year=2026)
    assert results == []
    print("PASS: test_get_fixtures_for_date_returns_empty_for_no_matches_that_day")


@patch("engine.sources.openfootball_source.requests.get")
def test_caches_season_and_does_not_refetch(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_SEASON_JSON)
    source = OpenFootballSource()

    source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026)
    source.get_fixtures_for_date(date(2026, 8, 22), season_start_year=2026)

    assert mock_get.call_count == 1, f"Expected exactly 1 fetch (cached after that), got {mock_get.call_count}"
    print("PASS: test_caches_season_and_does_not_refetch")


@patch("engine.sources.openfootball_source.requests.get")
def test_fetch_failure_returns_empty_list_not_crash(mock_get):
    mock_get.side_effect = Exception("404 Not Found")
    source = OpenFootballSource()

    results = source.get_fixtures_for_date(date(2099, 1, 1), season_start_year=2098)
    assert results == []
    print("PASS: test_fetch_failure_returns_empty_list_not_crash")


def test_normalize_team_name_handles_all_confirmed_current_epl_clubs():
    """Regression test using the REAL, current (2026-27 season) list of
    all 20 openfootball EPL team names, checked against the REAL
    current xgabora short-name convention (both pulled directly from
    live data during development, not guessed) - confirms every one
    normalizes to the correct short name the streak analyzer's
    historical lookups actually use.
    """
    expected = {
        "AFC Bournemouth": "Bournemouth",
        "Arsenal FC": "Arsenal",
        "Aston Villa FC": "Aston Villa",
        "Brentford FC": "Brentford",
        "Brighton & Hove Albion FC": "Brighton",
        "Chelsea FC": "Chelsea",
        "Coventry City FC": "Coventry",
        "Crystal Palace FC": "Crystal Palace",
        "Everton FC": "Everton",
        "Fulham FC": "Fulham",
        "Hull City AFC": "Hull",
        "Ipswich Town FC": "Ipswich",
        "Leeds United FC": "Leeds",
        "Liverpool FC": "Liverpool",
        "Manchester City FC": "Man City",
        "Manchester United FC": "Man United",
        "Newcastle United FC": "Newcastle",
        "Nottingham Forest FC": "Nott'm Forest",
        "Sunderland AFC": "Sunderland",
        "Tottenham Hotspur FC": "Tottenham",
    }
    for openfootball_name, expected_short_name in expected.items():
        actual = _normalize_team_name(openfootball_name)
        assert actual == expected_short_name, (
            f"{openfootball_name!r} -> expected {expected_short_name!r}, got {actual!r}"
        )
    print(f"PASS: test_normalize_team_name_handles_all_confirmed_current_epl_clubs ({len(expected)} clubs checked)")


def test_normalize_team_name_catches_non_suffix_mismatches():
    """The specific failure cases a naive 'strip FC/AFC suffix' rule
    would have missed - these three clubs' short names differ by more
    than just the suffix, confirmed against real data.
    """
    assert _normalize_team_name("Manchester City FC") == "Man City"
    assert _normalize_team_name("Manchester United FC") == "Man United"
    assert _normalize_team_name("Nottingham Forest FC") == "Nott'm Forest"
    print("PASS: test_normalize_team_name_catches_non_suffix_mismatches")


def test_normalize_team_name_returns_unchanged_for_unknown_team():
    """A team not in the mapping (e.g. a club never seen in either
    dataset yet) should pass through unchanged, not raise - this
    results in "no history found" for that team rather than a crash.
    """
    assert _normalize_team_name("Some Brand New FC") == "Some Brand New FC"
    print("PASS: test_normalize_team_name_returns_unchanged_for_unknown_team")


def test_get_fixtures_applies_name_normalization():
    """End-to-end check that get_fixtures_for_date() actually applies
    the normalization, not just that the helper function works in
    isolation.
    """
    season_json = {
        "matches": [{
            "round": "Matchday 1", "date": "2026-08-21", "time": "20:00",
            "team1": "Manchester City FC", "team2": "Nottingham Forest FC",
            "score": {"ht": [1, 0], "ft": [2, 0]},
        }]
    }
    with patch("engine.sources.openfootball_source.requests.get", return_value=make_mock_response(season_json)):
        source = OpenFootballSource()
        results = source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026)

    assert len(results) == 1
    assert results[0].home_team == "Man City", f"Expected normalized name, got {results[0].home_team!r}"
    assert results[0].away_team == "Nott'm Forest", f"Expected normalized name, got {results[0].away_team!r}"
    print("PASS: test_get_fixtures_applies_name_normalization")


if __name__ == "__main__":
    test_season_folder_formatting()
    test_extract_score_handles_dict_shape()
    test_extract_score_handles_bare_array_shape()
    test_extract_score_returns_none_for_unplayed_match()
    test_parse_match_date_iso_format()
    test_normalize_team_name_handles_all_confirmed_current_epl_clubs()
    test_normalize_team_name_catches_non_suffix_mismatches()
    test_normalize_team_name_returns_unchanged_for_unknown_team()
    test_get_fixtures_applies_name_normalization()
    test_get_fixtures_for_date_finds_played_match()
    test_get_fixtures_for_date_finds_bare_array_score_match()
    test_get_fixtures_for_date_finds_upcoming_match()
    test_get_fixtures_for_date_returns_empty_for_no_matches_that_day()
    test_caches_season_and_does_not_refetch()
    test_fetch_failure_returns_empty_list_not_crash()
    print("\nAll tests passed.")
