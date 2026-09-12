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

    results = source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026, window_days=0)

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

    results = source.get_fixtures_for_date(date(2026, 8, 22), season_start_year=2026, window_days=0)

    assert len(results) == 1
    assert results[0].home_goals == 2
    assert results[0].away_goals == 0
    assert results[0].status == "finished"
    print("PASS: test_get_fixtures_for_date_finds_bare_array_score_match")


@patch("engine.sources.openfootball_source.requests.get")
def test_get_fixtures_for_date_finds_upcoming_match(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_SEASON_JSON)
    source = OpenFootballSource()

    results = source.get_fixtures_for_date(date(2027, 3, 13), season_start_year=2026, window_days=0)

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

    results = source.get_fixtures_for_date(date(2026, 12, 25), season_start_year=2026, window_days=0)
    assert results == []
    print("PASS: test_get_fixtures_for_date_returns_empty_for_no_matches_that_day")


@patch("engine.sources.openfootball_source.requests.get")
def test_caches_season_and_does_not_refetch(mock_get):
    mock_get.return_value = make_mock_response(SAMPLE_SEASON_JSON)
    source = OpenFootballSource()

    source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026, window_days=0)
    source.get_fixtures_for_date(date(2026, 8, 22), season_start_year=2026, window_days=0)

    assert mock_get.call_count == 1, f"Expected exactly 1 fetch (cached after that), got {mock_get.call_count}"
    print("PASS: test_caches_season_and_does_not_refetch")


@patch("engine.sources.openfootball_source.requests.get")
def test_fetch_failure_returns_empty_list_not_crash(mock_get):
    mock_get.side_effect = Exception("404 Not Found")
    source = OpenFootballSource()

    results = source.get_fixtures_for_date(date(2099, 1, 1), season_start_year=2098, window_days=0)
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
        results = source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026, window_days=0)

    assert len(results) == 1
    assert results[0].home_team == "Man City", f"Expected normalized name, got {results[0].home_team!r}"
    assert results[0].away_team == "Nott'm Forest", f"Expected normalized name, got {results[0].away_team!r}"
    print("PASS: test_get_fixtures_applies_name_normalization")


def test_normalize_team_name_handles_all_confirmed_current_la_liga_clubs():
    """Real, current (2026-27) La Liga team names checked against
    xgabora's real short-name convention for the same league (both
    pulled directly from live data during development)."""
    expected = {
        "Athletic Club": "Ath Bilbao",
        "CA Osasuna": "Osasuna",
        "Club Atlético de Madrid": "Ath Madrid",
        "Deportivo Alavés": "Alaves",
        "FC Barcelona": "Barcelona",
        "Getafe CF": "Getafe",
        "RC Celta de Vigo": "Celta",
        "RCD Espanyol de Barcelona": "Espanol",
        "Rayo Vallecano de Madrid": "Vallecano",
        "Real Betis Balompié": "Betis",
        "Real Madrid CF": "Real Madrid",
        "Real Sociedad de Fútbol": "Sociedad",
        "Sevilla FC": "Sevilla",
        "Valencia CF": "Valencia",
        "Villarreal CF": "Villarreal",
    }
    for of_name, expected_short in expected.items():
        actual = _normalize_team_name(of_name, "la_liga")
        assert actual == expected_short, f"{of_name!r} -> expected {expected_short!r}, got {actual!r}"
    print(f"PASS: test_normalize_team_name_handles_all_confirmed_current_la_liga_clubs ({len(expected)} clubs checked)")


def test_normalize_team_name_handles_all_confirmed_current_bundesliga_clubs():
    expected = {
        "1. FC Köln": "FC Koln",
        "1. FC Union Berlin": "Union Berlin",
        "1. FSV Mainz 05": "Mainz",
        "Bayer 04 Leverkusen": "Leverkusen",
        "Borussia Dortmund": "Dortmund",
        "Borussia Mönchengladbach": "M'gladbach",
        "Eintracht Frankfurt": "Ein Frankfurt",
        "FC Augsburg": "Augsburg",
        "FC Bayern München": "Bayern Munich",
        "FC Schalke 04": "Schalke 04",
        "Hamburger SV": "Hamburg",
        "RB Leipzig": "RB Leipzig",
        "SC Freiburg": "Freiburg",
        "TSG 1899 Hoffenheim": "Hoffenheim",
        "VfB Stuttgart": "Stuttgart",
    }
    for of_name, expected_short in expected.items():
        actual = _normalize_team_name(of_name, "bundesliga")
        assert actual == expected_short, f"{of_name!r} -> expected {expected_short!r}, got {actual!r}"
    print(f"PASS: test_normalize_team_name_handles_all_confirmed_current_bundesliga_clubs ({len(expected)} clubs checked)")


def test_normalize_team_name_handles_all_confirmed_current_serie_a_clubs():
    expected = {
        "AC Milan": "Milan",
        "AS Roma": "Roma",
        "Atalanta BC": "Atalanta",
        "FC Internazionale Milano": "Inter",
        "Juventus FC": "Juventus",
        "SS Lazio": "Lazio",
        "SSC Napoli": "Napoli",
        "Torino FC": "Torino",
        "Udinese Calcio": "Udinese",
    }
    for of_name, expected_short in expected.items():
        actual = _normalize_team_name(of_name, "serie_a")
        assert actual == expected_short, f"{of_name!r} -> expected {expected_short!r}, got {actual!r}"
    print(f"PASS: test_normalize_team_name_handles_all_confirmed_current_serie_a_clubs ({len(expected)} clubs checked)")


def test_normalize_team_name_handles_all_confirmed_current_ligue_1_clubs():
    expected = {
        "AS Monaco FC": "Monaco",
        "Lille OSC": "Lille",
        "OGC Nice": "Nice",
        "Olympique Lyonnais": "Lyon",
        "Olympique de Marseille": "Marseille",
        "Paris Saint-Germain FC": "Paris SG",
        "Racing Club de Lens": "Lens",
        "Stade Rennais FC 1901": "Rennes",
        "Toulouse FC": "Toulouse",
    }
    for of_name, expected_short in expected.items():
        actual = _normalize_team_name(of_name, "ligue_1")
        assert actual == expected_short, f"{of_name!r} -> expected {expected_short!r}, got {actual!r}"
    print(f"PASS: test_normalize_team_name_handles_all_confirmed_current_ligue_1_clubs ({len(expected)} clubs checked)")


def test_normalize_team_name_leagues_do_not_cross_contaminate():
    """A team name that happens to exist in one league's mapping should
    NOT be normalized using a different league's table - confirms the
    per-league dict lookup is actually scoped correctly, not silently
    falling back to a shared/merged mapping.
    """
    # "Real Madrid CF" is only in the la_liga mapping - looking it up
    # under "epl" should return it UNCHANGED (no match), not accidentally
    # find it via some shared fallback.
    assert _normalize_team_name("Real Madrid CF", "epl") == "Real Madrid CF"
    assert _normalize_team_name("Real Madrid CF", "la_liga") == "Real Madrid"
    print("PASS: test_normalize_team_name_leagues_do_not_cross_contaminate")


def test_get_fixtures_for_date_respects_league_parameter():
    """get_fixtures_for_date() must pass the league through to both the
    season fetch AND the name normalization - confirms the parameter
    actually threads through the whole call, not just accepted and
    ignored.
    """
    la_liga_json = {
        "matches": [{
            "round": "Matchday 1", "date": "2026-08-21", "time": "20:00",
            "team1": "Real Madrid CF", "team2": "FC Barcelona",
            "score": {"ht": [1, 0], "ft": [2, 1]},
        }]
    }
    with patch("engine.sources.openfootball_source.requests.get", return_value=make_mock_response(la_liga_json)):
        source = OpenFootballSource()
        results = source.get_fixtures_for_date(date(2026, 8, 21), league="la_liga", season_start_year=2026, window_days=0)

    assert len(results) == 1
    assert results[0].home_team == "Real Madrid", f"Expected La Liga normalization, got {results[0].home_team!r}"
    assert results[0].away_team == "Barcelona"
    print("PASS: test_get_fixtures_for_date_respects_league_parameter")


def test_fetch_season_raw_raises_on_unsupported_league():
    """An unrecognized league code should fail loudly (ValueError),
    not silently fetch the wrong league's file or return garbage.
    """
    source = OpenFootballSource()
    try:
        source._fetch_season_raw(2026, league="not_a_real_league")
        assert False, "Expected ValueError for unsupported league"
    except ValueError as e:
        assert "not_a_real_league" in str(e)
    print("PASS: test_fetch_season_raw_raises_on_unsupported_league")


def test_get_fixtures_for_date_default_window_includes_next_two_days():
    """Regression/feature test for the 3-day window fix: with the
    default window_days=2, a call for date(2026, 8, 21) should include
    matches on the 21st, 22nd, AND 23rd (if any), not just the 21st.
    Uses SAMPLE_SEASON_JSON, which has matches on the 21st and 22nd.
    """
    mock_get_patcher = patch("engine.sources.openfootball_source.requests.get", return_value=make_mock_response(SAMPLE_SEASON_JSON))
    with mock_get_patcher:
        source = OpenFootballSource()
        results = source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026)  # default window_days=2

    result_dates = {r.date for r in results}
    assert date(2026, 8, 21) in result_dates
    assert date(2026, 8, 22) in result_dates, "Expected the default 2-day window to include the 22nd"
    print(f"PASS: test_get_fixtures_for_date_default_window_includes_next_two_days (dates found: {sorted(result_dates)})")


def test_get_fixtures_for_date_window_excludes_dates_outside_range():
    """A match clearly outside the window (the 2027-03-13 fixture in
    SAMPLE_SEASON_JSON) should NOT appear when querying a window
    anchored at 2026-08-21, even with the default 2-day window.
    """
    with patch("engine.sources.openfootball_source.requests.get", return_value=make_mock_response(SAMPLE_SEASON_JSON)):
        source = OpenFootballSource()
        results = source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026)

    result_dates = {r.date for r in results}
    assert date(2027, 3, 13) not in result_dates, "Expected the far-future fixture to be excluded from this window"
    print("PASS: test_get_fixtures_for_date_window_excludes_dates_outside_range")


def test_get_fixtures_for_date_custom_window_days():
    """window_days=0 should behave like the original exact-date-only
    matching (used throughout this file's other tests for
    backward-compatible single-day assertions)."""
    with patch("engine.sources.openfootball_source.requests.get", return_value=make_mock_response(SAMPLE_SEASON_JSON)):
        source = OpenFootballSource()
        results = source.get_fixtures_for_date(date(2026, 8, 21), season_start_year=2026, window_days=0)

    assert len(results) == 1
    assert results[0].date == date(2026, 8, 21)
    print("PASS: test_get_fixtures_for_date_custom_window_days")


if __name__ == "__main__":
    test_season_folder_formatting()
    test_extract_score_handles_dict_shape()
    test_extract_score_handles_bare_array_shape()
    test_extract_score_returns_none_for_unplayed_match()
    test_parse_match_date_iso_format()
    test_normalize_team_name_handles_all_confirmed_current_epl_clubs()
    test_normalize_team_name_catches_non_suffix_mismatches()
    test_normalize_team_name_returns_unchanged_for_unknown_team()
    test_normalize_team_name_handles_all_confirmed_current_la_liga_clubs()
    test_normalize_team_name_handles_all_confirmed_current_bundesliga_clubs()
    test_normalize_team_name_handles_all_confirmed_current_serie_a_clubs()
    test_normalize_team_name_handles_all_confirmed_current_ligue_1_clubs()
    test_normalize_team_name_leagues_do_not_cross_contaminate()
    test_get_fixtures_applies_name_normalization()
    test_get_fixtures_for_date_respects_league_parameter()
    test_fetch_season_raw_raises_on_unsupported_league()
    test_get_fixtures_for_date_finds_played_match()
    test_get_fixtures_for_date_finds_bare_array_score_match()
    test_get_fixtures_for_date_finds_upcoming_match()
    test_get_fixtures_for_date_returns_empty_for_no_matches_that_day()
    test_caches_season_and_does_not_refetch()
    test_fetch_failure_returns_empty_list_not_crash()
    test_get_fixtures_for_date_default_window_includes_next_two_days()
    test_get_fixtures_for_date_window_excludes_dates_outside_range()
    test_get_fixtures_for_date_custom_window_days()
    print("\nAll tests passed.")
