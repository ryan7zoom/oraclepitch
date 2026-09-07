"""
engine/output/generate_html.py

Renders data/predictions/latest.json into docs/index.html - a static,
dark-themed, mobile-responsive dashboard. No JS framework, no build
step: plain HTML/CSS generated directly from Python so it's simple to
debug and has zero client-side dependencies (loads instantly on a
phone, works with JS disabled, etc).

Highlighting rule: for each Over market, the specific line with the
highest "value" (model probability minus implied probability, when
odds are available; otherwise just the model probability itself) is
visually highlighted, per the spec's "Best Over line" column design.
"""

from datetime import datetime, timezone
from typing import Optional

import config


def _lines_from_probs(over_probs: dict) -> list[float]:
    """Extract the actual Over lines present in a probability dict's
    keys (e.g. {'over_1.5': 0.9, 'over_2.5': 0.7} -> [1.5, 2.5]).

    This replaces the old approach of passing a fixed config.X_LINES
    list into _best_over_line() - corners.py and shots.py now generate
    DYNAMIC per-team/per-match line ranges (see their generate_lines()
    functions), so the set of lines varies prediction to prediction and
    must be read from the data itself, not assumed from a shared
    constant that no longer exists.
    """
    lines = []
    for key in over_probs:
        if key.startswith("over_"):
            try:
                lines.append(float(key.split("_", 1)[1]))
            except ValueError:
                continue
    return sorted(lines)


def _best_over_line(over_probs: dict, lines: list[float] = None) -> tuple[float, float]:
    """Return (best_line, its_probability) - the HIGHEST line that still
    clears a reasonable confidence threshold.

    lines: if not provided, derived directly from over_probs' own keys
        via _lines_from_probs() - this is now the normal path, since
        corners/shots lines are dynamic per prediction (see module
        docstring above). Still accepts an explicit lines list for
        markets that DO use a fixed, shared range (match_total_goals,
        team_total_goals via config.GOAL_LINES/TEAM_GOAL_LINES), and
        for tests that want to check specific lines directly.

    Note on why this isn't simply "highest probability": since Over
    probabilities are monotonically decreasing as the line increases
    (verified in tests/test_dixon_coles.py), naively picking the
    max-probability line always selects the lowest available line (e.g.
    Over 0.5 goals, Over 2.5 shots), which is true almost every match and
    isn't a useful or differentiated recommendation - it's the trivial
    answer, not a considered probability x reasonable-line balance.

    Without real bookmaker odds (see engine/backtest/simulator.py's
    documented gap), true "value" can't be computed as probability minus
    implied probability. As a stand-in, this picks the HIGHEST line
    whose probability still exceeds a confidence floor - a simple, at
    least directionally sensible proxy: it surfaces the most
    informative/aggressive line the model still has real confidence in,
    rather than always the safest/lowest one. This is a placeholder
    heuristic, not a real value calculation - flagged so it gets revisited
    once real odds are available.
    """
    if lines is None:
        lines = _lines_from_probs(over_probs)
    if not lines:
        return None, None

    CONFIDENCE_FLOOR = 0.5
    best_line = None
    best_prob = None
    for line in sorted(lines):
        prob = over_probs.get(f"over_{line}")
        if prob is not None and prob >= CONFIDENCE_FLOOR:
            best_line = line
            best_prob = prob
    if best_line is None:
        # Nothing cleared the floor - fall back to the lowest line's
        # probability so the cell isn't empty, but this signals a match
        # where none of the Over lines are confidently predicted.
        lowest = min(lines)
        return lowest, over_probs.get(f"over_{lowest}")
    return best_line, best_prob


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _match_row_html(prediction: dict) -> str:
    home = prediction["home_team"]
    away = prediction["away_team"]
    mw = prediction["match_winner"]
    dc = prediction["double_chance"]
    mtg = prediction["match_total_goals"]
    ttg = prediction["team_total_goals"]
    tsot = prediction["team_shots_on_target"]
    msot = prediction["match_shots_on_target"]

    dc_best_key = "1X" if dc["1X"] >= dc["X2"] else "X2"
    dc_best_val = dc[dc_best_key]

    mtg_line, mtg_prob = _best_over_line(mtg, config.GOAL_LINES)
    home_ttg_line, home_ttg_prob = _best_over_line(ttg["home"], config.TEAM_GOAL_LINES)
    away_ttg_line, away_ttg_prob = _best_over_line(ttg["away"], config.TEAM_GOAL_LINES)
    # Shots on target lines are now dynamic per side/match (see
    # engine/prediction/shots.py's generate_lines()/generate_match_lines()),
    # so no fixed config list exists anymore - _best_over_line derives
    # the actual lines directly from each dict's own keys.
    home_sot_line, home_sot_prob = _best_over_line(tsot["home"])
    away_sot_line, away_sot_prob = _best_over_line(tsot["away"])
    msot_line, msot_prob = _best_over_line(msot)

    def cell(line, prob):
        if line is None:
            return '<td class="na">—</td>'
        cls = "value-high" if prob >= 0.65 else ("value-med" if prob >= 0.5 else "")
        return f'<td class="{cls}">O{line} <span class="prob">{_fmt_pct(prob)}</span></td>'

    return f"""
    <tr>
      <td class="match-cell">
        <div class="home-team">{home}</div>
        <div class="vs">vs</div>
        <div class="away-team">{away}</div>
      </td>
      <td>{_fmt_pct(mw['home_win'])}</td>
      <td>{_fmt_pct(mw['draw'])}</td>
      <td>{_fmt_pct(mw['away_win'])}</td>
      <td>{dc_best_key} <span class="prob">{_fmt_pct(dc_best_val)}</span></td>
      {cell(mtg_line, mtg_prob)}
      {cell(home_ttg_line, home_ttg_prob)}
      {cell(away_ttg_line, away_ttg_prob)}
      {cell(home_sot_line, home_sot_prob)}
      {cell(away_sot_line, away_sot_prob)}
      {cell(msot_line, msot_prob)}
    </tr>
    """


def _mismatch_card_html(mismatch) -> str:
    """Render a single Mismatch (engine.streaks.analyzer.Mismatch) as an
    HTML card, following the spec's sample output format exactly:
    emoji + strength label, both streak sentences, combined confidence,
    and a "Suggested Bet" / "Potential Opportunity" line - NEVER phrased
    as "Bet this" or "Recommend", per the spec's explicit requirement
    that this tool surface patterns without making betting decisions.
    """
    strength_class = f"strength-{mismatch.strength_label.lower()}"
    emoji = {"Strong": "\U0001F680", "Solid": "\u2705", "Watch": "\U0001F440"}.get(mismatch.strength_label, "")
    match_label = f"{mismatch.home_team} vs {mismatch.away_team}"
    h2h_tag = " (H2H)" if mismatch.is_h2h else ""

    return f"""
    <div class="mismatch-card {strength_class}">
      <div class="mismatch-title">{emoji} {mismatch.strength_label.upper()} MISMATCH: {match_label}{h2h_tag}</div>
      <div class="mismatch-line">{mismatch.for_streak.team}: {mismatch.for_streak.describe()}</div>
      <div class="mismatch-line">{mismatch.against_streak.team} allowed: {mismatch.against_streak.describe()}</div>
      <div class="mismatch-combined">Combined confidence: {mismatch.combined_percentage:.1%} &rarr; {mismatch.strength_label}</div>
      <div class="mismatch-opportunity">Potential Opportunity: {mismatch.suggested_bet}</div>
    </div>
    """


def _mismatches_section_html(all_mismatches: dict) -> str:
    """Render the top "Mismatches Found" section. all_mismatches maps
    "{home_team} vs {away_team}" -> list[Mismatch]. Per spec 4.1, this
    section is only shown at all if there's at least one mismatch
    across any fixture.
    """
    flat = []
    for fixture_label, mismatches in all_mismatches.items():
        flat.extend(mismatches)

    if not flat:
        return ""

    # Sort strongest first (Strong > Solid > Watch), so the most
    # actionable patterns are immediately visible without scrolling.
    strength_order = {"Strong": 0, "Solid": 1, "Watch": 2}
    flat.sort(key=lambda m: strength_order.get(m.strength_label, 99))

    cards_html = "\n".join(_mismatch_card_html(m) for m in flat)
    return f"""
    <h2 class="section-heading">\U0001F50D Mismatches Found ({len(flat)})</h2>
    {cards_html}
    """


def _streaks_by_category_html(all_streaks: dict, flagged_streak_keys: set) -> str:
    """Render the "All Streaks by Category" section (spec 4.1 bottom
    section). all_streaks maps team_name -> list[StreakResult].
    flagged_streak_keys is a set of (team, stat, threshold, window,
    direction, filter_type) tuples identifying which specific streaks
    were part of a mismatch, so they can be visually flagged per the
    spec's "<- Mismatch flagged" indicator.

    Streaks are grouped by stat category (matching the spec's sample
    output layout: "--- Shots on Target ---", "--- Corners ---", etc.),
    then by team within each category.
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
    predictions: list[dict],
    generated_at: Optional[datetime] = None,
    all_mismatches: Optional[dict] = None,
    all_streaks: Optional[dict] = None,
) -> str:
    """Render the full dashboard HTML from a list of MatchPrediction
    dicts (see engine.prediction.ensemble.MatchPrediction.to_dict()).

    all_mismatches: optional dict of "{home} vs {away}" -> list[Mismatch]
        (engine.streaks.analyzer.Mismatch) for the "Mismatches Found"
        section. Omitted or empty -> that section is skipped entirely,
        so this function remains fully backward-compatible with the
        original predictions-only dashboard (see
        tests/test_generate_html.py's pre-existing tests, which call
        this without these new arguments and must keep passing).
    all_streaks: optional dict of team_name -> list[StreakResult] for
        the "All Streaks by Category" section. Same backward-compat
        note applies.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    timestamp_str = generated_at.strftime("%Y-%m-%d %H:%M UTC")

    if predictions:
        rows_html = "\n".join(_match_row_html(p) for p in predictions)
    else:
        rows_html = '<tr><td colspan="10" class="no-matches">No upcoming fixtures found.</td></tr>'

    mismatches_html = _mismatches_section_html(all_mismatches or {})

    flagged_keys = set()
    if all_mismatches:
        for mismatches in all_mismatches.values():
            for m in mismatches:
                for s in (m.for_streak, m.against_streak):
                    flagged_keys.add((s.team, s.stat, s.threshold, s.window, s.direction, s.filter_type))
    streaks_html = _streaks_by_category_html(all_streaks or {}, flagged_keys)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EPL Prediction Dashboard</title>
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
  header {{
    margin-bottom: 20px;
  }}
  h1 {{
    font-size: 1.4rem;
    margin: 0 0 4px 0;
  }}
  .meta {{
    color: var(--text-dim);
    font-size: 0.85rem;
  }}
  .table-wrap {{
    overflow-x: auto;
    border-radius: 10px;
    border: 1px solid var(--border);
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    min-width: 900px;
    background: var(--card-bg);
  }}
  th, td {{
    padding: 10px 12px;
    text-align: center;
    border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
    white-space: nowrap;
  }}
  th {{
    background: #1c2028;
    color: var(--text-dim);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.7rem;
    letter-spacing: 0.04em;
  }}
  .match-cell {{
    text-align: left;
    white-space: normal;
  }}
  .home-team {{ font-weight: 600; }}
  .away-team {{ font-weight: 600; }}
  .vs {{ color: var(--text-dim); font-size: 0.7rem; }}
  .prob {{
    display: block;
    color: var(--text-dim);
    font-size: 0.75rem;
  }}
  .value-high {{ background: var(--value-high); }}
  .value-med {{ background: var(--value-med); }}
  .na {{ color: var(--text-dim); }}
  .no-matches {{
    padding: 40px;
    color: var(--text-dim);
  }}
  footer {{
    margin-top: 20px;
    color: var(--text-dim);
    font-size: 0.75rem;
  }}

  /* Streak / mismatch dashboard sections */
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
  .mismatch-title {{
    font-weight: 600;
    margin-bottom: 6px;
  }}
  .mismatch-line {{
    font-size: 0.85rem;
    color: var(--text);
    margin: 2px 0;
  }}
  .mismatch-combined {{
    font-size: 0.85rem;
    color: var(--text-dim);
    margin-top: 6px;
  }}
  .mismatch-opportunity {{
    font-size: 0.85rem;
    color: var(--accent);
    margin-top: 4px;
  }}
  .no-mismatches {{
    color: var(--text-dim);
    font-size: 0.9rem;
    padding: 8px 0;
  }}
  .streak-category {{
    margin-bottom: 20px;
  }}
  .streak-team-name {{
    font-weight: 600;
    margin: 10px 0 4px 0;
  }}
  .streak-line {{
    font-size: 0.82rem;
    color: var(--text-dim);
    margin: 2px 0 2px 12px;
  }}
  .streak-line.flagged {{
    color: var(--text);
  }}
  .mismatch-flag {{
    color: var(--accent);
    font-size: 0.78rem;
  }}
</style>
</head>
<body>
<header>
  <h1>EPL Match Preview Dashboard</h1>
  <div class="meta">Generated: {timestamp_str} &middot; Data: API-Football (live) + xgabora/Club-Football-Match-Data (historical)</div>
</header>

{mismatches_html}

<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th>Match</th>
      <th>Home Win</th>
      <th>Draw</th>
      <th>Away Win</th>
      <th>Double Chance</th>
      <th>Match Total O</th>
      <th>Home Total O</th>
      <th>Away Total O</th>
      <th>Home SOT O</th>
      <th>Away SOT O</th>
      <th>Match SOT O</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</div>

{streaks_html}

<footer>
  Corners predictions are currently disabled pending a reliable live data source.
  Shots on Target shown as Over-only lines.
  Streak and mismatch sections are decision-support only - they do not
  represent bet recommendations or automated staking advice.
</footer>
</body>
</html>
"""


def write_html(
    predictions: list[dict],
    output_path: str = None,
    all_mismatches: Optional[dict] = None,
    all_streaks: Optional[dict] = None,
):
    output_path = output_path or config.HTML_OUTPUT_PATH
    html = generate_html(predictions, all_mismatches=all_mismatches, all_streaks=all_streaks)
    with open(output_path, "w") as f:
        f.write(html)
    return output_path
