"""
engine/output/expected_html.py

Compact HTML for the expected-value block shown at the top of each match.
"""

from html import escape

SHOW_MATCH_TOTALS = True   # False = only the two team rows per stat

_STAT_LABELS = {"shots_on_target": "Shots on target", "corners": "Corners", "goals": "Goals"}

EXPECTED_CSS = """
  .ev-stat { margin: 0 0 10px 0; }
  .ev-stat-name { font-size: 0.72rem; text-transform: uppercase; letter-spacing: .06em; color: var(--text-dim); margin-bottom: 2px; }
  .ev-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; font-size: 0.82rem; padding: 2px 0; }
  .ev-team { min-width: 92px; color: var(--text); }
  .ev-num { min-width: 34px; font-weight: 700; color: var(--accent); }
  .ev-line { color: var(--text-dim); white-space: nowrap; }
  .ev-line b { color: var(--text); font-weight: 600; }
  .ev-h2h { font-size: 0.74rem; color: var(--text-dim); padding: 1px 0 0 0; }
  .streak-details { margin-top: 10px; }
  .streak-details > summary { cursor: pointer; font-size: 0.8rem; color: var(--text-dim); }
"""


def _lines_html(lines: list) -> str:
    return "".join(
        f'<span class="ev-line">{side} {line:g} <b>{pct * 100:.0f}%</b></span>'
        for side, line, pct in lines
    )


def _row(label: str, expected: float, lines: list) -> str:
    return (
        f'<div class="ev-row"><span class="ev-team">{escape(label)}</span>'
        f'<span class="ev-num">{expected:.1f}</span>{_lines_html(lines)}</div>'
    )


def expected_block_html(expected) -> str:
    if not expected:
        return ""
    home, away = escape(expected["home_team"]), escape(expected["away_team"])
    blocks = []
    for s in expected["stats"]:
        rows = [
            _row(expected["home_team"], s.home_expected, s.home_lines),
            _row(expected["away_team"], s.away_expected, s.away_lines),
        ]
        if SHOW_MATCH_TOTALS:
            rows.append(_row("Total", s.total_expected, s.total_lines))
        if s.h2h:
            rows.append(
                f'<div class="ev-h2h">H2H at this venue, last {s.h2h["n"]}: '
                f'{home} {s.h2h["home_avg"]:.1f} &ndash; {away} {s.h2h["away_avg"]:.1f}</div>'
            )
        blocks.append(
            f'<div class="ev-stat"><div class="ev-stat-name">{_STAT_LABELS.get(s.stat, s.stat)}</div>{"".join(rows)}</div>'
        )
    return "".join(blocks)
