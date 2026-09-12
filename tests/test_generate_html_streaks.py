"""
tests/test_generate_html_streaks.py

Tests for engine/output/generate_html.py's streak/mismatch dashboard
rendering - the only rendering this module does after the project
pivot away from prediction models (see config.py's module docstring).

Critical checks:
- Never uses "Bet this" or "Recommend" language.
- Always uses "Potential Opportunity" phrasing.
- The "Mismatches Found" section is omitted entirely when there are no
  mismatches (not shown empty).
- Flagged streaks (those that contributed to a mismatch) are visually
  marked in the "All Streaks by Category" section.
- generate_html() works correctly with no arguments at all (produces a
  valid, informative empty-state page, not a crash).
"""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.sources.football_data_co_uk_source import HistoricalMatchOdds
from engine.streaks.analyzer import StreakAnalyzer
from engine.output.generate_html import generate_html, _mismatches_section_html, _streaks_by_category_html


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
    html = _mismatches_section_html({})
    assert html == "", f"Expected empty string for no mismatches, got: {html!r}"

    html2 = _mismatches_section_html({"A vs B": []})
    assert html2 == "", "Expected empty string when the mismatch list itself is empty"
    print("PASS: test_mismatches_section_omitted_when_empty")


def test_mismatches_section_renders_when_present():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    assert len(mismatches) > 0, "Test setup should produce real mismatches"

    html = _mismatches_section_html({"TeamA vs TeamB": mismatches})
    assert "Mismatches Found" in html
    assert "TeamA" in html and "TeamB" in html
    print(f"PASS: test_mismatches_section_renders_when_present ({len(mismatches)} mismatches)")


def test_mismatches_section_shows_league_prefix_from_dict_key():
    """Regression test for a real bug found during multi-league
    development: the fixture label dict key (which carries the league
    prefix, e.g. "[La Liga] Real Madrid vs Barcelona") was being
    discarded during rendering - the card's title was reconstructed
    from Mismatch.home_team/away_team directly, which has no league
    field at all, so the league name silently disappeared even though
    it was present in the data passed to the renderer.
    """
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    assert len(mismatches) > 0

    html = _mismatches_section_html({"[La Liga] TeamA vs TeamB": mismatches})
    assert "[La Liga]" in html, "Expected the league prefix from the dict key to appear in the rendered card"
    print("PASS: test_mismatches_section_shows_league_prefix_from_dict_key")


def test_mismatch_card_never_uses_forbidden_language():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    html = _mismatches_section_html({"TeamA vs TeamB": mismatches})

    forbidden = ["bet this", "recommend", "you should bet", "we recommend"]
    html_lower = html.lower()
    for phrase in forbidden:
        assert phrase not in html_lower, f"FORBIDDEN phrase found in mismatch card: {phrase!r}"
    assert "potential opportunity" in html_lower
    print("PASS: test_mismatch_card_never_uses_forbidden_language")


def test_mismatches_sorted_strongest_first():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    html = _mismatches_section_html({"TeamA vs TeamB": mismatches})

    strong_pos = html.find("STRONG MISMATCH")
    watch_pos = html.find("WATCH")
    if strong_pos != -1 and watch_pos != -1:
        assert strong_pos < watch_pos, "Expected Strong mismatches to appear before Watch mismatches"
    print("PASS: test_mismatches_sorted_strongest_first")


def test_streaks_by_category_groups_correctly():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    all_streaks = {
        "TeamA": [analyzer.get_streak("TeamA", "shots_on_target", 5, 8, "for", as_of, "home_only")],
        "TeamB": [analyzer.get_streak("TeamB", "shots_on_target", 5, 8, "against", as_of, "away_only")],
    }
    html = _streaks_by_category_html(all_streaks, flagged_streak_keys=set())

    assert "Shots on Target" in html
    assert "TeamA" in html and "TeamB" in html
    print("PASS: test_streaks_by_category_groups_correctly")


def test_streaks_by_category_flags_mismatched_streaks():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    assert len(mismatches) > 0

    flagged_keys = set()
    for m in mismatches:
        for s in (m.for_streak, m.against_streak):
            flagged_keys.add((s.team, s.stat, s.threshold, s.window, s.direction, s.filter_type))

    flagged_streak = mismatches[0].for_streak
    unflagged_streak = analyzer.get_streak("TeamA", "goals", 3, 18, "for", as_of, "away_only")

    all_streaks = {"TeamA": [flagged_streak, unflagged_streak]}
    html = _streaks_by_category_html(all_streaks, flagged_keys)

    assert "Mismatch flagged" in html
    print("PASS: test_streaks_by_category_flags_mismatched_streaks")


def test_full_generate_html_integration_with_mismatches_and_streaks():
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()
    all_mismatches_by_date = {as_of: {"TeamA vs TeamB": mismatches}}
    all_streaks_by_date = {as_of: {
        "TeamA": [analyzer.get_streak("TeamA", "shots_on_target", 5, 8, "for", as_of, "home_only")],
        "TeamB": [analyzer.get_streak("TeamB", "shots_on_target", 5, 8, "against", as_of, "away_only")],
    }}

    html = generate_html(
        fixture_count=2,
        all_mismatches_by_date=all_mismatches_by_date,
        all_streaks_by_date=all_streaks_by_date,
        target_date=as_of,
    )

    assert "<!DOCTYPE html>" in html
    assert "Mismatches Found" in html
    assert "All Streaks by Category" in html
    assert "Today" in html  # as_of == target_date, so should be labeled "Today"

    import re
    from collections import Counter
    open_tags = re.findall(r"<(div|h2|h3)\b", html)
    close_tags = re.findall(r"</(div|h2|h3)>", html)
    oc, cc = Counter(open_tags), Counter(close_tags)
    for tag in set(list(oc.keys()) + list(cc.keys())):
        assert oc[tag] == cc[tag], f"Tag mismatch for <{tag}>: open={oc[tag]} close={cc[tag]}"

    print("PASS: test_full_generate_html_integration_with_mismatches_and_streaks")


def test_mismatch_card_shows_h2h_and_alignment_when_enriched():
    """When a Mismatch has been enriched with H2H data (see
    StreakAnalyzer._enrich_with_h2h()), the card should show separate
    "Recent Form" and "Head-to-Head" sections plus the alignment
    verdict (Aligned/Mixed/Conflict), per the pivot spec's sample
    output layout.
    """
    matches = []
    start = date(2020, 8, 1)
    day = 0
    for i in range(8):
        matches.append(make_match(start + timedelta(days=day), "TeamA", f"Filler{i}", hst=7))
        day += 7
    for i in range(8):
        matches.append(make_match(start + timedelta(days=day), f"Filler{i+10}", "TeamB", hst=7, ast=1))
        day += 7
    for i in range(5):
        matches.append(make_match(start + timedelta(days=day), "TeamA", "TeamB", hst=8, ast=1))
        day += 200

    analyzer = StreakAnalyzer(matches)
    as_of = start + timedelta(days=day + 30)
    mismatches = analyzer.find_mismatches("TeamA", "TeamB", as_of)
    enriched = [m for m in mismatches if m.h2h_streak is not None]
    assert len(enriched) > 0, "Test setup should produce at least one H2H-enriched mismatch"

    html = _mismatches_section_html({"TeamA vs TeamB": enriched})
    assert "Recent Form:" in html
    assert "Head-to-Head:" in html
    assert any(word in html for word in ("ALIGNED", "MIXED", "CONFLICT"))
    print("PASS: test_mismatch_card_shows_h2h_and_alignment_when_enriched")


def test_generate_html_groups_by_date_with_correct_labels():
    """Core test for the 3-day window dashboard feature: three days
    passed in should render as three separate sections labeled
    "Today", "Tomorrow", and "Day After Tomorrow" respectively (per
    the exact spec wording), in date order, each showing its own
    fixtures/empty-state independently.
    """
    from datetime import timedelta
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()

    today = as_of
    tomorrow = as_of + timedelta(days=1)
    day_after = as_of + timedelta(days=2)

    all_mismatches_by_date = {
        today: {"TeamA vs TeamB": mismatches},
        tomorrow: {},  # no mismatches tomorrow - should still get its own section
        day_after: {},
    }

    html = generate_html(
        fixture_count=1,
        all_mismatches_by_date=all_mismatches_by_date,
        all_streaks_by_date={},
        target_date=today,
    )

    assert "Today" in html
    assert "Tomorrow" in html
    assert "Day After Tomorrow" in html
    # Tomorrow and day-after have no mismatches - each should show the
    # "no fixtures" message, not silently omit the day entirely.
    assert html.count("No fixtures scheduled for this day") == 2

    # Today's section should come before Tomorrow's in the rendered
    # order (dates sorted ascending).
    assert html.find("Today") < html.find("Tomorrow") < html.find("Day After Tomorrow")

    print("PASS: test_generate_html_groups_by_date_with_correct_labels")


def test_generate_html_infers_target_date_when_not_provided():
    """If target_date isn't explicitly passed, it should be inferred as
    the earliest date present in the input, so "Today" labeling still
    makes sense without requiring every caller to pass target_date
    redundantly.
    """
    from datetime import timedelta
    analyzer, as_of, mismatches = _build_clear_mismatch_scenario()

    html = generate_html(
        fixture_count=1,
        all_mismatches_by_date={as_of: {"TeamA vs TeamB": mismatches}},
        all_streaks_by_date={},
        # target_date deliberately omitted
    )
    assert "Today" in html, "Expected target_date to be inferred from the earliest date present"
    print("PASS: test_generate_html_infers_target_date_when_not_provided")


if __name__ == "__main__":
    test_generate_html_with_no_args_produces_valid_empty_state()
    test_mismatches_section_omitted_when_empty()
    test_mismatches_section_renders_when_present()
    test_mismatches_section_shows_league_prefix_from_dict_key()
    test_mismatch_card_never_uses_forbidden_language()
    test_mismatches_sorted_strongest_first()
    test_streaks_by_category_groups_correctly()
    test_streaks_by_category_flags_mismatched_streaks()
    test_full_generate_html_integration_with_mismatches_and_streaks()
    test_mismatch_card_shows_h2h_and_alignment_when_enriched()
    test_generate_html_groups_by_date_with_correct_labels()
    test_generate_html_infers_target_date_when_not_provided()
    print("\nAll tests passed.")
