"""
engine/output/generate_html.py

Renders the trend-surfacing dashboard: docs/index.html - a static,
dark-themed, mobile-responsive page. No JS framework, no build step:
plain HTML/CSS generated directly from Python.

STRUCTURE (per the collapsed-by-default, league-nested rewrite):

  Today
    Premier League
      <details> Liverpool vs Man United [3 mismatches x 12 streaks]
        Mismatches Found
        Recent Form Streaks
      <details> Arsenal vs Chelsea [...]
    La Liga
      ...
  Tomorrow
    ...

Each match is a native <details>/<summary> element, collapsed by
default (no JavaScript needed) - this replaced an earlier flat
render-everything-inline layout that produced a page tens of thousands
of lines long and was unusable on mobile.

This module never recommends bets or stake sizes - phrasing is always
"Potential Opportunity" or a plain factual description, per the
project's explicit requirement that this tool surface patterns for the
user's own judgment, not make betting decisions.
"""

from datetime import datetime, timezone
from typing import Optional

import config


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "\u2014"
    return f"{value * 100:.1f}%"


def _mismatch_card_html(mismatch) -> str:
    """Render a single Mismatch (engine.streaks.analyzer.Mismatch) as an
    HTML card. If the mismatch carries H2H enrichment (see
    StreakAnalyzer._enrich_with_h2h()), show "Recent Form" and
    "Head-to-Head" as separate labeled sections with an alignment
    verdict. Otherwise falls back to the simpler for/against-only
    layout.

    Does NOT include a match_label/title of its own - the containing
    <summary> already shows the team names, so repeating "Team A vs
    Team B" inside every card would be redundant given matches are now
    grouped one-per-<details> rather than flattened into a single long
    list (which is why match_label was needed in the earlier version
    of this function).

    NEVER uses "Bet this" or "Recommend" language - always "Suggested
    Bet"/"Potential Opportunity".
    """
    strength_class = f"strength-{mismatch.strength_label.lower()}"
    emoji = {"Strong": "\U0001F680", "Solid": "\u2705", "Watch": "\U0001F440"}.get(mismatch.strength_label, "")
    h2h_tag = " (H2H)" if mismatch.is_h2h else ""

    if mismatch.h2h_streak is not None:
        alignment_icon = {"Aligned": "\U0001F680", "Mixed": "\U0001F440", "Conflict": "\u26A0\uFE0F"}.get(mismatch.alignment, "")
        body = f"""
      <div class="mismatch-subheading">Recent Form:</div>
      <div class="mismatch-line">{mismatch.for_streak.team}: {mismatch.for_streak.describe()}</div>
      <div class="mismatch-line">{mismatch.against_streak.team} allowed: {mismatch.against_streak.describe()}</div>
      <div class="mismatch-subheading">Head-to-Head:</div>
      <div class="mismatch-line">{mismatch.for_streak.team}: {mismatch.h2h_streak.describe()} &larr; {mismatch.alignment}</div>
      <div class="mismatch-alignment alignment-{mismatch.alignment.lower()}">{alignment_icon} {mismatch.alignment.upper()}</div>
        """
    else:
        body = f"""
      <div class="mismatch-line">{mismatch.for_streak.team}: {mismatch.for_streak.describe()}</div>
      <div class="mismatch-line">{mismatch.against_streak.team} allowed: {mismatch.against_streak.describe()}</div>
        """

    return f"""
    <div class="mismatch-card {strength_class}">
      <div class="mismatch-title">{emoji} {mismatch.strength_label.upper()} MISMATCH{h2h_tag}</div>
      {body}
      <div class="mismatch-combined">Combined confidence: {mismatch.combined_percentage:.1%} &rarr; {mismatch.strength_label}</div>
      <div class="mismatch-opportunity">Potential Opportunity: {mismatch.suggested_bet}</div>
    </div>
    """


def _mismatches_section_html(mismatches: list) -> str:
    """Render the "Mismatches Found" section for ONE match. mismatches
    is a flat list[Mismatch] for that specific fixture. Empty list ->
    no section rendered at all (not an empty placeholder).
    """
    if not mismatches:
        return ""

    strength_order = {"Strong": 0, "Solid": 1, "Watch": 2}
    ordered = sorted(mismatches, key=lambda m: strength_order.get(m.strength_label, 99))

    cards_html = "\n".join(_mismatch_card_html(m) for m in ordered)
    return f"""
    <h4 class="section-heading">\U0001F50D Mismatches Found ({len(ordered)})</h4>
    {cards_html}
    """


def _recent_form_section_html(recent_form: dict) -> str:
    """Render the "Recent Form Streaks" section for ONE match, per
    Problem 2's spec: home team's home-only streaks and away team's
    away-only streaks, each already filtered/sorted by
    engine.main._compute_recent_form_streaks(). Format matches the
    spec's exact example:

        Liverpool (Home Form)
          • 5+ SOT in 9 of last 10 home games (90.0%)

        Manchester United (Away Form)
          • 5+ SOT allowed in 8 of last 10 away games (80.0%)
    """
    home_streaks = recent_form.get("home", [])
    away_streaks = recent_form.get("away", [])
    if not home_streaks and not away_streaks:
        return ""

    blocks = []
    if home_streaks:
        lines = "\n".join(f'<div class="streak-line">&bull; {s.describe()}</div>' for s in home_streaks)
        blocks.append(f'<div class="streak-team-name">{recent_form["home_team"]} (Home Form)</div>{lines}')
    if away_streaks:
        lines = "\n".join(f'<div class="streak-line">&bull; {s.describe()}</div>' for s in away_streaks)
        blocks.append(f'<div class="streak-team-name">{recent_form["away_team"]} (Away Form)</div>{lines}')

    return f"""
    <h4 class="section-heading">\U0001F4CA Recent Form Streaks</h4>
    {"".join(blocks)}
    """


def _match_details_html(fixture_label: str, mismatches: list, recent_form: dict) -> str:
    """Render one match as a collapsed <details> block, per Problem 5's
    spec: native HTML disclosure widget, no JavaScript, collapsed by
    default (no `open` attribute), with a compact one-line summary
    showing team names and a mismatch/streak count badge.
    """
    n_mismatches = len(mismatches)
    n_streaks = len(recent_form.get("home", [])) + len(recent_form.get("away", []))
    has_strong = any(m.strength_label == "Strong" for m in mismatches)
    strong_icon = "\U0001F680 " if has_strong else ""

    badge_text = f"{n_mismatches} mismatch{'es' if n_mismatches != 1 else ''} &middot; {n_streaks} streak{'s' if n_streaks != 1 else ''}"

    mismatches_html = _mismatches_section_html(mismatches)
    recent_form_html = _recent_form_section_html(recent_form)
    body = mismatches_html + recent_form_html
    if not body:
        body = '<div class="no-matches">No qualifying trends found for this match.</div>'

    return f"""
    <details class="match-details">
      <summary>
        <span class="match-teams">{strong_icon}<strong>{fixture_label}</strong></span>
        <span class="badge">{badge_text}</span>
      </summary>
      <div class="match-body">
        {body}
      </div>
    </details>
    """


def _match_sort_key(fixture_label: str, mismatches: list) -> tuple:
    """Sort key for matches within a league, per Problem 5's spec:
    1. Matches with a Strong mismatch first
    2. Then matches with any mismatch
    3. Then matches with only streaks (no mismatches)
    4. Alphabetical by home team as tiebreaker

    Lower tuple values sort first, so tier 0 = has-Strong (best),
    tier 1 = has-any-mismatch, tier 2 = no mismatches at all.
    """
    has_strong = any(m.strength_label == "Strong" for m in mismatches)
    has_any = len(mismatches) > 0
    tier = 0 if has_strong else (1 if has_any else 2)
    home_team = fixture_label.split(" vs ")[0]
    return (tier, home_team)


def _league_section_html(league_name: str, fixtures_dict: dict, recent_form_dict: dict) -> str:
    """Render one league's fixtures for one day: a subheading followed
    by each match as a collapsed <details> block, sorted per
    _match_sort_key. fixtures_dict maps fixture_label -> list[Mismatch]
    (may be an empty list for matches with no mismatches, which still
    get a card if they have recent-form streaks).
    """
    all_labels = set(fixtures_dict.keys()) | set(recent_form_dict.keys())
    if not all_labels:
        return ""

    ordered_labels = sorted(all_labels, key=lambda label: _match_sort_key(label, fixtures_dict.get(label, [])))

    matches_html = "\n".join(
        _match_details_html(label, fixtures_dict.get(label, []), recent_form_dict.get(label, {}))
        for label in ordered_labels
    )

    return f"""
    <div class="league-section">
      <h3 class="league-heading">{league_name}</h3>
      {matches_html}
    </div>
    """


def _day_label(d, target_date) -> str:
    """Return a human label for a date relative to target_date: "Today",
    "Tomorrow", "Day After Tomorrow" for the first three days of the
    window, falling back to a plain weekday+date format beyond that.
    """
    offset = (d - target_date).days
    weekday = d.strftime("%A")
    if offset == 0:
        return f"Today \u2014 {d.isoformat()} ({weekday})"
    elif offset == 1:
        return f"Tomorrow \u2014 {d.isoformat()} ({weekday})"
    elif offset == 2:
        return f"Day After Tomorrow \u2014 {d.isoformat()} ({weekday})"
    else:
        return f"{d.isoformat()} ({weekday})"


def _day_section_html(
    d, target_date, mismatches_for_day: dict, recent_form_for_day: dict,
    league_display_names: dict, league_order: list,
) -> str:
    """Render one day's full section: heading, then one subsection per
    league that actually has fixtures that day (leagues with zero
    fixtures are skipped entirely, per Problem 4's spec), in
    league_order. Leagues not in league_order (shouldn't normally
    happen) are appended after the known ones, alphabetically, rather
    than silently dropped.
    """
    leagues_present = set(mismatches_for_day.keys()) | set(recent_form_for_day.keys())

    if not leagues_present:
        body = '<div class="no-matches">No qualifying trends for this day\'s fixtures.</div>'
    else:
        ordered_leagues = [lg for lg in league_order if lg in leagues_present]
        ordered_leagues += sorted(leagues_present - set(league_order))

        sections = []
        for league in ordered_leagues:
            league_name = league_display_names.get(league, league)
            sections.append(_league_section_html(
                league_name,
                mismatches_for_day.get(league, {}),
                recent_form_for_day.get(league, {}),
            ))
        body = "".join(sections)

    label = _day_label(d, target_date)
    return f"""
    <div class="day-section">
      <h2 class="day-heading">\U0001F4C5 {label}</h2>
      {body}
    </div>
    """


def generate_html(
    fixture_count: int = 0,
    generated_at: Optional[datetime] = None,
    all_mismatches: Optional[dict] = None,
    all_recent_form: Optional[dict] = None,
    target_date=None,
    league_display_names: Optional[dict] = None,
    league_order: Optional[list] = None,
    dates_with_fixtures: Optional[list] = None,
) -> str:
    """Render the full dashboard HTML, grouped by day then by league,
    with each match collapsed by default.

    all_mismatches: dict of date -> league -> {fixture_label: list[Mismatch]}.
    all_recent_form: dict of date -> league -> {fixture_label: {"home_team":.., "away_team":.., "home": [...], "away": [...]}}.
    target_date: the window's start date, used for "Today"/"Tomorrow"
        labeling. Inferred from the earliest date present if omitted.
    league_display_names: dict mapping internal league key -> display
        name (e.g. "epl" -> "Premier League"). Falls back to using the
        key itself if not provided or a key is missing.
    league_order: list of internal league keys defining display order
        within each day. Leagues not in this list are appended after,
        alphabetically.
    dates_with_fixtures: explicit list of dates that had at least one
        real fixture scheduled, even if that fixture produced zero
        qualifying mismatches/recent-form streaks. Without this, a day
        with real fixtures but no data clearing the 60% threshold would
        be indistinguishable from a day with literally no fixtures at
        all, and would incorrectly disappear from the page entirely
        instead of showing its own "Today"/"Tomorrow" heading with a
        day-level empty state. If omitted, falls back to only the
        dates present as keys in all_mismatches/all_recent_form (the
        old, narrower behavior).
    """
    generated_at = generated_at or datetime.now(timezone.utc)

    all_mismatches = all_mismatches or {}
    all_recent_form = all_recent_form or {}
    league_display_names = league_display_names or {}
    league_order = league_order or []

    if dates_with_fixtures is not None:
        all_dates = sorted(dates_with_fixtures)
    else:
        all_dates = sorted(set(all_mismatches.keys()) | set(all_recent_form.keys()))

    if target_date is None:
        target_date = all_dates[0] if all_dates else generated_at.date()

    if not all_dates:
        body_html = '<div class="no-matches">No fixtures today.</div>'
    else:
        sections = []
        for d in all_dates:
            sections.append(_day_section_html(
                d, target_date,
                all_mismatches.get(d, {}),
                all_recent_form.get(d, {}),
                league_display_names, league_order,
            ))
        body_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trends</title>
<style>
  :root {{
    --bg: #0f1115;
    --card-bg: #171a21;
    --border: #2a2e38;
    --text: #e6e8eb;
    --text-dim: #9aa0ab;
    --accent: #4fd1c5;
    --value-high: #1f6f5c;
    --value-med: #2c4a63;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    margin: 0;
    padding: 16px;
  }}
  header {{ margin-bottom: 20px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px 0; }}
  .no-matches {{
    padding: 24px 8px;
    color: var(--text-dim);
    text-align: center;
    font-size: 0.9rem;
  }}

  .day-section {{ margin-bottom: 32px; }}
  .day-heading {{
    font-size: 1.15rem;
    margin: 0 0 14px 0;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }}

  .league-section {{ margin-bottom: 20px; }}
  .league-heading {{
    font-size: 0.95rem;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    margin: 0 0 8px 4px;
  }}

  /* Collapsed-by-default match cards - native <details>/<summary>,
     no JavaScript. See details[open] rule below for the expand marker
     rotation. */
  .match-details {{
    border-bottom: 1px solid var(--border);
  }}
  .match-details summary {{
    list-style: none;
    cursor: pointer;
    padding: 10px 4px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    font-size: 0.88rem;
  }}
  .match-details summary::-webkit-details-marker {{ display: none; }}
  .match-details summary::before {{
    content: "\\25B6";
    display: inline-block;
    margin-right: 8px;
    font-size: 0.7rem;
    color: var(--text-dim);
    transition: transform 0.15s ease;
  }}
  .match-details[open] summary::before {{
    transform: rotate(90deg);
  }}
  .match-teams {{
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .badge {{
    flex-shrink: 0;
    font-size: 0.72rem;
    color: var(--text-dim);
    background: var(--card-bg);
    padding: 3px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }}
  .match-body {{
    padding: 4px 4px 16px 20px;
  }}

  .section-heading {{
    font-size: 0.95rem;
    margin: 18px 0 10px 0;
  }}
  .mismatch-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 12px;
  }}
  .mismatch-card.strength-strong {{ border-left: 4px solid var(--value-high); }}
  .mismatch-card.strength-solid {{ border-left: 4px solid var(--value-med); }}
  .mismatch-card.strength-watch {{ border-left: 4px solid #7a6a2c; }}
  .mismatch-title {{ font-weight: 600; margin-bottom: 6px; font-size: 0.85rem; }}
  .mismatch-subheading {{
    font-weight: 600;
    font-size: 0.8rem;
    color: var(--text-dim);
    margin-top: 10px;
    margin-bottom: 2px;
  }}
  .mismatch-line {{
    font-size: 0.85rem;
    color: var(--text);
    margin: 2px 0;
  }}
  .mismatch-alignment {{
    font-size: 0.8rem;
    font-weight: 600;
    margin-top: 6px;
  }}
  .alignment-aligned {{ color: var(--value-high); }}
  .alignment-mixed {{ color: #c9a227; }}
  .alignment-conflict {{ color: #c94d3f; }}
  .mismatch-combined {{
    font-size: 0.85rem;
    color: var(--text-dim);
    margin-top: 8px;
  }}
  .mismatch-opportunity {{
    font-size: 0.85rem;
    color: var(--accent);
    margin-top: 4px;
  }}
  .streak-team-name {{ font-weight: 600; margin: 10px 0 4px 0; font-size: 0.85rem; }}
  .streak-line {{
    font-size: 0.82rem;
    color: var(--text-dim);
    margin: 2px 0 2px 12px;
  }}
</style>
</head>
<body>
<header>
  <h1>Trends</h1>
</header>

{body_html}

</body>
</html>
"""


def write_html(
    fixture_count: int = 0,
    output_path: str = None,
    all_mismatches: Optional[dict] = None,
    all_recent_form: Optional[dict] = None,
    target_date=None,
    league_display_names: Optional[dict] = None,
    league_order: Optional[list] = None,
    dates_with_fixtures: Optional[list] = None,
):
    output_path = output_path or config.HTML_OUTPUT_PATH
    html = generate_html(
        fixture_count=fixture_count,
        all_mismatches=all_mismatches,
        all_recent_form=all_recent_form,
        target_date=target_date,
        league_display_names=league_display_names,
        league_order=league_order,
        dates_with_fixtures=dates_with_fixtures,
    )
    with open(output_path, "w") as f:
        f.write(html)
    return output_path
