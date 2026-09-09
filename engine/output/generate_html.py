"""
engine/output/generate_html.py

Renders the trend-surfacing dashboard: docs/index.html - a static,
dark-themed, mobile-responsive page. No JS framework, no build step:
plain HTML/CSS generated directly from Python.

REWRITTEN FOR THE PROJECT PIVOT: this module previously rendered a
predictions table (match winner %, double chance, goals/shots Over
lines) driven by the Dixon-Coles/Monte Carlo/shots/corners models.
That entire table and its supporting code (_match_row_html,
_best_over_line, _lines_from_probs) were deleted along with the models
themselves - see config.py's module docstring for the pivot rationale.
This module now renders ONLY the two streak/mismatch sections:
"Mismatches Found" and "All Streaks by Category". It never recommends
bets or stake sizes - phrasing is always "Potential Opportunity" or a
plain factual description, per the project's explicit requirement that
this tool surface patterns for the user's own judgment, not make
betting decisions.
"""

from datetime import datetime, timezone
from typing import Optional

import config


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "\u2014"
    return f"{value * 100:.1f}%"


def _mismatch_card_html(mismatch, match_label: str = None) -> str:
    """Render a single Mismatch (engine.streaks.analyzer.Mismatch) as an
    HTML card. If the mismatch has been enriched with a corresponding
    head-to-head streak (see StreakAnalyzer._enrich_with_h2h()), show
    "Recent Form" and "Head-to-Head" as separate labeled sections with
    an alignment verdict, per the pivot spec's sample output. Otherwise
    (H2H-only mismatches, or recent-form mismatches with no H2H data
    available) falls back to the simpler for/against-only layout.

    match_label: if provided, used as the card's fixture label instead
        of reconstructing "{home} vs {away}" from the Mismatch object's
        own fields - needed for multi-league support, since Mismatch
        itself has no league field, but the caller's all_mismatches
        dict key (e.g. "[La Liga] Real Madrid vs Barcelona") already
        has the league prefix. Without this, the league name was
        silently dropped from the rendered card even though it was
        present in the data passed in - a real bug found and fixed
        during multi-league development.

    NEVER uses "Bet this" or "Recommend" language - always "Suggested
    Bet"/"Potential Opportunity", per the project's explicit
    requirement that this tool never makes betting decisions.
    """
    strength_class = f"strength-{mismatch.strength_label.lower()}"
    emoji = {"Strong": "\U0001F680", "Solid": "\u2705", "Watch": "\U0001F440"}.get(mismatch.strength_label, "")
    match_label = match_label or f"{mismatch.home_team} vs {mismatch.away_team}"
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
      <div class="mismatch-title">{emoji} {mismatch.strength_label.upper()} MISMATCH: {match_label}{h2h_tag}</div>
      {body}
      <div class="mismatch-combined">Combined confidence: {mismatch.combined_percentage:.1%} &rarr; {mismatch.strength_label}</div>
      <div class="mismatch-opportunity">Potential Opportunity: {mismatch.suggested_bet}</div>
    </div>
    """


def _mismatches_section_html(all_mismatches: dict) -> str:
    """Render the top "Mismatches Found" section. all_mismatches maps
    a fixture label (e.g. "[La Liga] Real Madrid vs Barcelona") ->
    list[Mismatch]. Only shown at all if there's at least one mismatch
    across any fixture.
    """
    flat = []  # list of (fixture_label, Mismatch) tuples
    for fixture_label, mismatches in all_mismatches.items():
        for m in mismatches:
            flat.append((fixture_label, m))

    if not flat:
        return ""

    strength_order = {"Strong": 0, "Solid": 1, "Watch": 2}
    flat.sort(key=lambda pair: strength_order.get(pair[1].strength_label, 99))

    cards_html = "\n".join(_mismatch_card_html(m, match_label=label) for label, m in flat)
    return f"""
    <h2 class="section-heading">\U0001F50D Mismatches Found ({len(flat)})</h2>
    {cards_html}
    """


def _streaks_by_category_html(all_streaks: dict, flagged_streak_keys: set) -> str:
    """Render the "All Streaks by Category" section: streaks grouped by
    stat, then by team within each stat, with mismatch-contributing
    streaks visually flagged.
    """
    if not all_streaks:
        return ""

    by_category = {}
    for team, streaks in all_streaks.items():
        for s in streaks:
            by_category.setdefault(s.stat, {}).setdefault(team, []).append(s)

    category_display_names = {
        "shots_on_target": "Shots on Target",
        "corners": "Corners",
        "goals": "Goals",
        "goals_conceded": "Goals Conceded",
    }

    sections = []
    for stat, teams_dict in by_category.items():
        category_name = category_display_names.get(stat, stat)
        team_blocks = []
        for team, streaks in teams_dict.items():
            lines = []
            for s in streaks:
                key = (s.team, s.stat, s.threshold, s.window, s.direction, s.filter_type)
                flag = ' <span class="mismatch-flag">&larr; Mismatch flagged</span>' if key in flagged_streak_keys else ""
                line_class = "streak-line flagged" if key in flagged_streak_keys else "streak-line"
                lines.append(f'<div class="{line_class}">&bull; {s.describe()}{flag}</div>')
            team_blocks.append(
                f'<div class="streak-team-name">{team}</div>' + "\n".join(lines)
            )
        sections.append(
            f'<div class="streak-category"><h3>--- {category_name} ---</h3>' + "\n".join(team_blocks) + "</div>"
        )

    return f"""
    <h2 class="section-heading">\U0001F4C8 All Streaks by Category</h2>
    {"".join(sections)}
    """


def generate_html(
    fixture_count: int = 0,
    generated_at: Optional[datetime] = None,
    all_mismatches: Optional[dict] = None,
    all_streaks: Optional[dict] = None,
) -> str:
    """Render the full dashboard HTML.

    fixture_count: number of fixtures analyzed today, shown in the
        header, purely informational.
    all_mismatches: dict of "{home} vs {away}" -> list[Mismatch]
        (engine.streaks.analyzer.Mismatch) for the "Mismatches Found"
        section. Omitted or empty -> that section is skipped entirely.
    all_streaks: dict of team_name -> list[StreakResult] for the
        "All Streaks by Category" section.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    timestamp_str = generated_at.strftime("%Y-%m-%d %H:%M UTC")

    mismatches_html = _mismatches_section_html(all_mismatches or {})

    flagged_keys = set()
    if all_mismatches:
        for mismatches in all_mismatches.values():
            for m in mismatches:
                for s in (m.for_streak, m.against_streak, m.h2h_streak):
                    if s is not None:
                        flagged_keys.add((s.team, s.stat, s.threshold, s.window, s.direction, s.filter_type))
    streaks_html = _streaks_by_category_html(all_streaks or {}, flagged_keys)

    if not mismatches_html and not streaks_html:
        body_html = '<div class="no-matches">No fixtures today.</div>'
    else:
        body_html = mismatches_html + streaks_html

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EPL Match Preview Dashboard</title>
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
  .meta {{ color: var(--text-dim); font-size: 0.85rem; }}
  footer {{
    margin-top: 20px;
    color: var(--text-dim);
    font-size: 0.75rem;
  }}
  .no-matches {{
    padding: 40px;
    color: var(--text-dim);
    text-align: center;
  }}

  .section-heading {{
    font-size: 1.1rem;
    margin: 32px 0 12px 0;
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
  .mismatch-title {{ font-weight: 600; margin-bottom: 6px; }}
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
  .streak-category {{ margin-bottom: 20px; }}
  .streak-team-name {{ font-weight: 600; margin: 10px 0 4px 0; }}
  .streak-line {{
    font-size: 0.82rem;
    color: var(--text-dim);
    margin: 2px 0 2px 12px;
  }}
  .streak-line.flagged {{ color: var(--text); }}
  .mismatch-flag {{ color: var(--accent); font-size: 0.78rem; }}
</style>
</head>
<body>
<header>
  <h1>EPL Match Preview Dashboard</h1>
</header>

{body_html}

</body>
</html>
"""


def write_html(
    fixture_count: int = 0,
    output_path: str = None,
    all_mismatches: Optional[dict] = None,
    all_streaks: Optional[dict] = None,
):
    output_path = output_path or config.HTML_OUTPUT_PATH
    html = generate_html(fixture_count=fixture_count, all_mismatches=all_mismatches, all_streaks=all_streaks)
    with open(output_path, "w") as f:
        f.write(html)
    return output_path
