"""
tests/test_generate_html_streaks.py

Tests for engine/output/generate_html.py's dashboard rendering:
day -> league -> collapsed match structure, Mismatches Found, Recent
Form Streaks, and the never-recommend-bets language requirement.
"""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sources.football_data_co_uk_source import HistoricalMatchOdds
from engine.streaks.analyzer import StreakAnalyzer
from engine.output.generate_html import (
    generate_html, _mismatches_section_html, _recent_form_section_html,
    _match_details_html, _match_sort_key,
)
import engine.main as main_module


def make_match(d, home, away, hst=4, ast=3, hc=5, ac=4):
    return HistoricalMatchOdds(
        date=d, home_team=home, away_team=away, home_goals=1, away_goals=1,
        home_shots_on_target=hst, away_shots_on_target=ast, home_corners=hc, away_corners=ac,
    )


def _build_clear_mismatch_scenario():
    matches = []
    start = date(2023, 8, 1)
    day = 0
    for i in range(8):
        matches.append(make_match(start + timedelta(days=day), "TeamA", f"Filler{i}", hst=7))
        day += 7
    for i in range(8):
        matches.append(make_match(start + timedelta(days=day), f"Filler{i+10}", "TeamB", hst=7, ast=1))
        day += 7

    analyzer = StreakAnalyzer(matches)
    as_of = start + timedelta(days=day + 30)
    mismatches = analyzer.find_mismatches("TeamA", "TeamB", as_of)
    return analyzer, as_of, mismatches


def test_generate_html_with_no_args_produces_valid_empty_state():
    html = generate_html()
    assert "<!DOCTYPE html>" in html
    assert "Mismatches Found" not in html
    assert "No fixtures today" in html
    print("PASS: test_generate_html_with_no_args_produces_valid_empty_state")


def test_mismatches_section_omitted_when_empty():
    html = _mismatches_section_html([])
    assert html == "", f"Expected empty string for no mismatches, got: {html!r}"
    print("PASS: test_mismatches_section_omitted_when_empty")


def test_mismatches_section_renders_when_present():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    assert len(mismatches) > 0
    html = _mismatches_section_html(mismatches)
    assert "Mismatches Found" in html
    print(f"PASS: test_mismatches_section_renders_when_present ({len(mismatches)} mismatches)")


def test_mismatch_card_never_uses_forbidden_language():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    html = _mismatches_section_html(mismatches)
    forbidden = ["bet this", "recommend", "you should bet", "we recommend"]
    html_lower = html.lower()
    for phrase in forbidden:
        assert phrase not in html_lower, f"FORBIDDEN phrase found: {phrase!r}"
    assert "potential opportunity" in html_lower
    print("PASS: test_mismatch_card_never_uses_forbidden_language")


def test_recent_form_section_renders_home_and_away():
    analyzer, as_of, _ = _build_clear_mismatch_scenario()
    recent_form = main_module._compute_recent_form_streaks(analyzer, "TeamA", "TeamB", as_of)
    recent_form["home_team"] = "TeamA"
    recent_form["away_team"] = "TeamB"

    html = _recent_form_section_html(recent_form)
    assert "Recent Form Streaks" in html
    assert "TeamA (Home Form)" in html
    print("PASS: test_recent_form_section_renders_home_and_away")


def test_recent_form_section_empty_when_no_streaks_qualify():
    html = _recent_form_section_html({"home": [], "away": [], "home_team": "A", "away_team": "B"})
    assert html == ""
    print("PASS: test_recent_form_section_empty_when_no_streaks_qualify")


def test_match_details_collapsed_by_default():
    """Core Problem-5 test: the rendered <details> block must NOT have
    an `open` attribute, so it's collapsed until the user taps it.
    """
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    recent_form = main_module._compute_recent_form_streaks(analyzer, "TeamA", "TeamB", as_of)
    recent_form["home_team"] = "TeamA"
    recent_form["away_team"] = "TeamB"

    html = _match_details_html("TeamA vs TeamB", mismatches, recent_form)
    assert "<details" in html
    assert "<details open" not in html, "Match should be collapsed by default (no `open` attribute)"
    assert "TeamA vs TeamB" in html
    assert "mismatch" in html and "streak" in html  # badge text
    print("PASS: test_match_details_collapsed_by_default")


def test_match_details_shows_strong_indicator():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    strong_mismatches = [m for m in mismatches if m.strength_label == "Strong"]
    assert len(strong_mismatches) > 0, "Test setup should produce a Strong mismatch"

    html = _match_details_html("TeamA vs TeamB", strong_mismatches, {"home": [], "away": []})
    assert "\U0001F680" in html, "Expected the rocket emoji indicator for a Strong mismatch"
    print("PASS: test_match_details_shows_strong_indicator")


def test_match_sort_key_prioritizes_strong_then_any_then_none():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    strong = [m for m in mismatches if m.strength_label == "Strong"]
    solid_or_watch = [m for m in mismatches if m.strength_label != "Strong"]

    key_strong = _match_sort_key("Zeta vs Omega", strong)
    key_any = _match_sort_key("Alpha vs Beta", solid_or_watch) if solid_or_watch else (1, "Alpha")
    key_none = _match_sort_key("Alpha vs Beta", [])

    assert key_strong[0] == 0, "Strong mismatches should sort into tier 0"
    assert key_none[0] == 2, "No mismatches should sort into tier 2 (last)"
    assert key_strong < key_none, "Strong-tier match should sort before no-mismatch match regardless of team name"
    print("PASS: test_match_sort_key_prioritizes_strong_then_any_then_none")


def test_generate_html_groups_by_date_and_league():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    recent_form = main_module._compute_recent_form_streaks(analyzer, "TeamA", "TeamB", as_of)
    recent_form["home_team"] = "TeamA"
    recent_form["away_team"] = "TeamB"

    all_mismatches = {as_of: {"epl": {"TeamA vs TeamB": mismatches}}}
    all_recent_form = {as_of: {"epl": {"TeamA vs TeamB": recent_form}}}

    html = generate_html(
        fixture_count=1,
        all_mismatches=all_mismatches,
        all_recent_form=all_recent_form,
        target_date=as_of,
        league_display_names={"epl": "Premier League"},
        league_order=["epl"],
    )

    assert "Today" in html
    assert "Premier League" in html
    assert "TeamA vs TeamB" in html
    assert "<details" in html
    assert "<details open" not in html

    import re
    from collections import Counter
    open_divs = len(re.findall(r"<div\b", html))
    close_divs = html.count("</div>")
    assert open_divs == close_divs, f"div mismatch: open={open_divs} close={close_divs}"

    print("PASS: test_generate_html_groups_by_date_and_league")


def test_generate_html_shows_day_heading_even_with_no_qualifying_trends():
    """Regression test for a real bug found during development: a day
    with real fixtures but zero mismatches/streaks clearing the 60%
    threshold was silently disappearing from the page entirely,
    indistinguishable from a day with no fixtures at all. Passing
    dates_with_fixtures explicitly fixes this.
    """
    target_date = date(2025, 12, 1)
    html = generate_html(
        fixture_count=1,
        all_mismatches={},
        all_recent_form={},
        target_date=target_date,
        dates_with_fixtures=[target_date],
    )
    assert "Today" in html, "Expected a day heading even though no mismatches/streaks qualified"
    assert "No qualifying trends" in html
    print("PASS: test_generate_html_shows_day_heading_even_with_no_qualifying_trends")


def test_leagues_with_no_fixtures_are_omitted():
    """Per Problem 4: only leagues that actually have fixtures on a
    given day should show a subheading - a league with zero fixtures
    that day must not appear at all.
    """
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    all_mismatches = {as_of: {"epl": {"TeamA vs TeamB": mismatches}}}

    html = generate_html(
        fixture_count=1,
        all_mismatches=all_mismatches,
        all_recent_form={},
        target_date=as_of,
        league_display_names={"epl": "Premier League", "la_liga": "La Liga"},
        league_order=["epl", "la_liga"],
    )
    assert "Premier League" in html
    assert "La Liga" not in html, "La Liga has no fixtures this day and should not appear"
    print("PASS: test_leagues_with_no_fixtures_are_omitted")


if __name__ == "__main__":
    test_generate_html_with_no_args_produces_valid_empty_state()
    test_mismatches_section_omitted_when_empty()
    test_mismatches_section_renders_when_present()
    test_mismatch_card_never_uses_forbidden_language()
    test_recent_form_section_renders_home_and_away()
    test_recent_form_section_empty_when_no_streaks_qualify()
    test_match_details_collapsed_by_default()
    test_match_details_shows_strong_indicator()
    test_match_sort_key_prioritizes_strong_then_any_then_none()
    test_generate_html_groups_by_date_and_league()
    test_generate_html_shows_day_heading_even_with_no_qualifying_trends()
    test_leagues_with_no_fixtures_are_omitted()
    print("\nAll tests passed.")
