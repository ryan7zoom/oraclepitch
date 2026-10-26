"""
tests/test_timezone.py

Tests for engine/main.py's _today_local() - the GMT+8-aware "today"
calculation. GitHub Actions runs in UTC, 8 hours behind GMT+8, so for
the first 8 hours of the GMT+8 day the UTC date is still "yesterday".
The GMT+8 day rolls over at 16:00 UTC.
"""

import sys
import os
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.main as main_module


def _today_at(fake_utc_now):
    with patch("engine.main.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: fake_utc_now.astimezone(tz) if tz else fake_utc_now
        return main_module._today_local()


def test_matches_utc_date_during_gmt8_daytime():
    assert _today_at(datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc)).isoformat() == "2026-09-10"


def test_advances_a_day_during_the_utc_gap():
    assert _today_at(datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)).isoformat() == "2026-09-11"


def test_at_exact_utc_midnight():
    assert _today_at(datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)).isoformat() == "2026-09-10"


def test_just_before_the_rollover():
    assert _today_at(datetime(2026, 9, 10, 15, 59, tzinfo=timezone.utc)).isoformat() == "2026-09-10"


def test_just_after_the_rollover():
    assert _today_at(datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc)).isoformat() == "2026-09-11"
