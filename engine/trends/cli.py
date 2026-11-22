"""
engine/trends/cli.py

Preview / batch runner.

One fixture (prints trends grouped like the reference page):
    python -m engine.trends.cli --league la_liga --home Barcelona --away Villarreal --date 2026-11-22

Many fixtures from a JSON file  [{"league":"epl","date":"2026-11-28","home":"Arsenal","away":"Chelsea"}, ...]:
    python -m engine.trends.cli --fixtures fixtures.json --out data/trends/latest.json

Team names must be the football-data.co.uk names used in data/matches/*.csv
(e.g. "Man United", "Ath Madrid", "Paris SG"); unknown names list the closest ones.
"""

import argparse
import difflib
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

import config
from engine.trends import settings as S
from engine.trends.engine import Fixture, TrendEngine, build_report


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def print_fixture(fx: Fixture, trends, group=None, raw=False, per_bet=6) -> None:
    from engine.trends.select import select_bets
    print(f"\n{fx.home} v {fx.away}  ({S.LEAGUE_LABELS.get(fx.league, fx.league)}, {fx.date})")
    if raw:                                   # every trend that passed the hit-rate rule (chalk included)
        by_group = defaultdict(lambda: defaultdict(list))
        for t in trends:
            by_group[t.group][t.bet].append(t)
        for g in S.GROUP_ORDER:
            if g not in by_group or (group and g != group):
                continue
            print(f"\n== {S.GROUP_LABELS[g].upper()} (raw) ==")
            for bet, ts in by_group[g].items():
                print(bet)
                for t in sorted(ts, key=lambda t: (-t.hits / t.window, -t.window))[:per_bet]:
                    mp = f"{t.model_p:.0%}" if t.model_p is not None else "n/a"
                    print(f"    {t.hits}/{t.window}  {t.text}   [model {mp}]")
        return
    bets = select_bets(trends)
    if not bets:
        print("  no strong trends")
    for matching, title in ((True, "HIGHEST MATCHING TRENDS (both sides line up)"), (False, "GENERAL TRENDS")):
        section = [b for b in bets if b.matching == matching and (not group or b.group == group)]
        if not section:
            continue
        print(f"\n######## {title} ########")
        for g in S.GROUP_ORDER:
            gb = [b for b in section if b.group == g]
            if not gb:
                continue
            print(f"\n== {S.GROUP_LABELS[g].upper()} ==")
            for b in gb:
                tail = f"   [fair line ~ {b.fair:.1f}]" if b.fair is not None else ""
                print(f"{b.label}{tail}")
                for t in b.trends:
                    print(f"    {t.hits}/{t.window}  {t.text}")


def main() -> None:
    p = argparse.ArgumentParser(description="Show match trends")
    p.add_argument("--league"), p.add_argument("--home"), p.add_argument("--away")
    p.add_argument("--date", default=None, help="fixture date YYYY-MM-DD (default: today UTC)")
    p.add_argument("--fixtures", default=None, help="JSON file with many fixtures")
    p.add_argument("--out", default=None, help="write the JSON report here")
    p.add_argument("--group", default=None, choices=S.GROUP_ORDER)
    p.add_argument("--raw", action="store_true", help="show every trend, not just the selected bets")
    p.add_argument("--per-bet", type=int, default=6)
    p.add_argument("--data-dir", default=config.MATCH_DATA_DIR)
    a = p.parse_args()

    engine = TrendEngine.from_dir(a.data_dir)
    if a.fixtures:
        with open(a.fixtures) as f:
            fixtures = [Fixture(x["league"], _d(x["date"]), x["home"], x["away"]) for x in json.load(f)]
    elif a.league and a.home and a.away:
        today = datetime.now(timezone.utc).date()
        fixtures = [Fixture(a.league, _d(a.date) if a.date else today, a.home, a.away)]
    else:
        p.error("give --league/--home/--away, or --fixtures FILE")

    for fx in fixtures:
        known = engine.teams(fx.league)
        bad = [t for t in (fx.home, fx.away) if t not in known]
        if bad:
            for t in bad:
                print(f"Unknown team {t!r} in {fx.league}. Closest: "
                      f"{difflib.get_close_matches(t, known, n=5, cutoff=0.3)}", file=sys.stderr)
            continue
        if not a.fixtures or len(fixtures) <= 3:
            print_fixture(fx, engine.trends_for_fixture(fx), a.group, a.raw, a.per_bet)

    report = build_report(engine, fixtures)
    print(f"\n{len(report['fixtures'])} fixture(s) analysed, {len(report['skipped'])} skipped, "
          f"{sum(len(f['bets']) for f in report['fixtures'])} bets total "
          f"(from {sum(f['raw_trend_count'] for f in report['fixtures'])} raw trends)")
    for s in report["skipped"]:
        print(f"  SKIPPED {s['home']} v {s['away']}: {s['reason']}")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), **report}, f)
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
