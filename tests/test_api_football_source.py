"""
tests/test_api_football_source.py

Unit tests for ApiFootballSource's PARSING logic, using mocked HTTP
responses shaped like API-Football's documented format.

IMPORTANT CAVEAT: these mocked response shapes are based on widely
reproduced community examples of API-Football's JSON, NOT a response
we've verified live. If real responses differ (e.g. different "type"
strings for statistics), these tests will pass while the real
integration silently fails. Treat a passing test suite here as
"the parsing code is internally consistent," not "this works against
the real API." That still needs a live check with a real key.
"""

import sys
import os
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sources.api_football_source import ApiFootballSource


MOCK_FIXTURE_RESPONSE = {
    "errors": [],
    "response": [
        {
            "fixture": {
                "id": 12345,
                "date": "2026-08-30T14:00:00+00:00",
                "status": {"short": "FT", "long": "Match Finished"},
            },
            "league": {"id": 39, "season": 2025},
            "teams": {
                "home": {"id": 1, "name": "Arsenal"},
                "away": {"id": 2, "name": "Chelsea"},
            },
            "goals": {"home": 2, "away": 1},
        }
    ],
}

MOCK_STATISTICS_RESPONSE = {
    "errors": [],
    "response": [
        {
            "team": {"id": 1, "name": "Arsenal"},
            "statistics": [
                {"type": "Shots on Goal", "value": 6},
                {"type": "Corner Kicks", "value": 7},
                {"type": "Ball Possession", "value": "58%"},
            ],
        },
        {
            "team": {"id": 2, "name": "Chelsea"},
            "statistics": [
                {"type": "Shots on Goal", "value": 3},
                {"type": "Corner Kicks", "value": 4},
                {"type": "Ball Possession", "value": "42%"},
            ],
        },
    ],
}

MOCK_STATISTICS_MISSING_RESPONSE = {
    "errors": [],
    "response": [],  # some old/minor fixtures return no statistics at all
}


def make_mock_response(json_body, status_code=200):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_body
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


@patch("engine.sources.api_football_source.time.sleep")  # skip real delays in tests
@patch("engine.sources.api_football_source.requests.get")
def test_get_fixtures_parses_basic_fields(mock_get, mock_sleep):
    mock_get.return_value = make_mock_response(MOCK_FIXTURE_RESPONSE)
    source = ApiFootballSource(api_key="fake-key-for-test")

    results = source.get_fixtures(season=2025)

    assert len(results) == 1
    match = results[0]
    assert match.fixture_id == "12345"
    assert match.home_team == "Arsenal"
    assert match.away_team == "Chelsea"
    assert match.home_goals == 2
    assert match.away_goals == 1
    assert match.status == "finished"
    assert match.date == date(2026, 8, 30)
    assert match.has_goals is True
    print("PASS: test_get_fixtures_parses_basic_fields")


@patch("engine.sources.api_football_source.time.sleep")
@patch("engine.sources.api_football_source.requests.get")
def test_get_fixture_statistics_parses_shots_and_corners(mock_get, mock_sleep):
    # First call returns fixture info, second returns statistics
    mock_get.side_effect = [
        make_mock_response(MOCK_FIXTURE_RESPONSE),
        make_mock_response(MOCK_STATISTICS_RESPONSE),
    ]
    source = ApiFootballSource(api_key="fake-key-for-test")

    result = source.get_fixture_statistics("12345")

    assert result.home_shots_on_target == 6
    assert result.away_shots_on_target == 3
    assert result.home_corners == 7
    assert result.away_corners == 4
    assert result.has_shots_on_target is True
    assert result.has_corners is True
    print("PASS: test_get_fixture_statistics_parses_shots_and_corners")


@patch("engine.sources.api_football_source.time.sleep")
@patch("engine.sources.api_football_source.requests.get")
def test_get_fixture_statistics_handles_missing_stats_gracefully(mock_get, mock_sleep):
    mock_get.side_effect = [
        make_mock_response(MOCK_FIXTURE_RESPONSE),
        make_mock_response(MOCK_STATISTICS_MISSING_RESPONSE),
    ]
    source = ApiFootballSource(api_key="fake-key-for-test")

    result = source.get_fixture_statistics("12345")

    # Should NOT raise, and should leave stats as None rather than 0
    assert result.home_shots_on_target is None
    assert result.away_shots_on_target is None
    assert result.has_shots_on_target is False
    # But goals should still be populated from the fixture call
    assert result.home_goals == 2
    print("PASS: test_get_fixture_statistics_handles_missing_stats_gracefully")


@patch("engine.sources.api_football_source.time.sleep")
@patch("engine.sources.api_football_source.requests.get")
def test_api_error_in_body_raises(mock_get, mock_sleep):
    mock_get.return_value = make_mock_response(
        {"errors": {"rateLimit": "Too many requests"}, "response": []}
    )
    source = ApiFootballSource(api_key="fake-key-for-test")

    try:
        source.get_fixtures(season=2025)
        assert False, "Expected RuntimeError to be raised"
    except RuntimeError as e:
        assert "rateLimit" in str(e)
    print("PASS: test_api_error_in_body_raises")


def test_missing_api_key_raises():
    import config
    original = config.API_FOOTBALL_KEY
    config.API_FOOTBALL_KEY = ""
    try:
        try:
            ApiFootballSource(api_key="")
            assert False, "Expected ValueError for missing API key"
        except ValueError:
            pass
        print("PASS: test_missing_api_key_raises")
    finally:
        config.API_FOOTBALL_KEY = original


def test_infer_current_season():
    assert ApiFootballSource._infer_current_season(date(2026, 9, 4)) == 2026
    assert ApiFootballSource._infer_current_season(date(2026, 3, 15)) == 2025
    assert ApiFootballSource._infer_current_season(date(2026, 7, 1)) == 2026
    print("PASS: test_infer_current_season")


if __name__ == "__main__":
    test_get_fixtures_parses_basic_fields()
    test_get_fixture_statistics_parses_shots_and_corners()
    test_get_fixture_statistics_handles_missing_stats_gracefully()
    test_api_error_in_body_raises()
    test_missing_api_key_raises()
    test_infer_current_season()
    print("\nAll tests passed.")
