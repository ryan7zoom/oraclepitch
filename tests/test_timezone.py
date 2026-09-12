"""
tests/test_timezone.py

Tests for engine/main.py's _today_bd() - the Bangladesh-timezone-aware
"today" calculation. Added after a real bug: the pipeline used
date.today(), which returns the GitHub Actions runner's UTC date. The
user is in Bangladesh (UTC+6), so during roughly the first 6 hours of
the Bangladesh day, the UTC date is still "yesterday" relative to the
user - this caused real, verified-to-exist fixtures to be missed
because the pipeline was checking the wrong calendar day entirely.
"""

import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.main as main_module


def test_today_bd_matches_utc_date_during_bangladesh_daytime():
    """When it's already well into the Bangladesh day (e.g. UTC 10:00 =
    16:00 Bangladesh), the Bangladesh date should match the UTC date -
    this is the "normal" case where the bug wouldn't have been visible.
    """
    fake_utc_now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc)

    with patch("engine.main.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: fake_utc_now.astimezone(tz) if tz else fake_utc_now
        result = main_module._today_bd()

    assert result.isoformat() == "2026-09-10"
    print(f"PASS: test_today_bd_matches_utc_date_during_bangladesh_daytime ({result})")


def test_today_bd_advances_a_day_during_the_utc_to_bd_gap():
    """THE CORE REGRESSION TEST: at UTC 22:00, it's already 04:00 the
    NEXT day in Bangladesh (UTC+6). date.today() on a UTC runner at
    this moment would incorrectly return the UTC date (one day
    behind) - _today_bd() must return the correct, later Bangladesh
    date instead. This exact scenario was confirmed to cause real
    fixtures to be missed in production.
    """
    fake_utc_now = datetime(2026, 9, 10, 22, 0, 0, tzinfo=timezone.utc)

    with patch("engine.main.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: fake_utc_now.astimezone(tz) if tz else fake_utc_now
        result = main_module._today_bd()

    assert result.isoformat() == "2026-09-11", (
        f"Expected Bangladesh date to have advanced to 2026-09-11 (UTC 22:00 = "
        f"Bangladesh 04:00 next day), got {result}"
    )
    print(f"PASS: test_today_bd_advances_a_day_during_the_utc_to_bd_gap ({result})")


def test_today_bd_at_exact_utc_midnight():
    """UTC midnight = 06:00 Bangladesh, same calendar day in Bangladesh
    as the UTC date that just started - a boundary case worth checking
    explicitly since it's the other side of the day-rollover window.
    """
    fake_utc_now = datetime(2026, 9, 10, 0, 0, 0, tzinfo=timezone.utc)

    with patch("engine.main.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: fake_utc_now.astimezone(tz) if tz else fake_utc_now
        result = main_module._today_bd()

    assert result.isoformat() == "2026-09-10"
    print(f"PASS: test_today_bd_at_exact_utc_midnight ({result})")


def test_today_bd_just_before_the_rollover():
    """UTC 17:59 = Bangladesh 23:59 (still the same day) - one minute
    before the actual rollover point (UTC 18:00 = Bangladesh 00:00 next
    day). Confirms the boundary is exactly where expected, not off by
    some rounding error.
    """
    fake_utc_now = datetime(2026, 9, 10, 17, 59, 0, tzinfo=timezone.utc)

    with patch("engine.main.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: fake_utc_now.astimezone(tz) if tz else fake_utc_now
        result = main_module._today_bd()

    assert result.isoformat() == "2026-09-10"
    print(f"PASS: test_today_bd_just_before_the_rollover ({result})")


def test_today_bd_just_after_the_rollover():
    """UTC 18:00 exactly = Bangladesh 00:00 next day - the actual
    rollover instant.
    """
    fake_utc_now = datetime(2026, 9, 10, 18, 0, 0, tzinfo=timezone.utc)

    with patch("engine.main.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: fake_utc_now.astimezone(tz) if tz else fake_utc_now
        result = main_module._today_bd()

    assert result.isoformat() == "2026-09-11"
    print(f"PASS: test_today_bd_just_after_the_rollover ({result})")


if __name__ == "__main__":
    test_today_bd_matches_utc_date_during_bangladesh_daytime()
    test_today_bd_advances_a_day_during_the_utc_to_bd_gap()
    test_today_bd_at_exact_utc_midnight()
    test_today_bd_just_before_the_rollover()
    test_today_bd_just_after_the_rollover()
    print("\nAll tests passed.")
