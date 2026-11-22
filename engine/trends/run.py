"""
engine/trends/run.py

THE daily pipeline:   python -m engine.trends.run

  data/matches/*.csv  ->  upcoming fixtures (ESPN)  ->  trends  ->  bets
                      ->  data/trends/latest.json + docs/index.html

Options: --days N (default 3), --today YYYY-MM-DD, --espn-budget N
"""

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import config
from engine.data.espn import EspnClient, SLUGS
from engine.data.http import make_session
from engine.trends import settings as S
from engine.trends.engine import TrendEngine, build_report
from engine.trends.fixtures import upcoming_fixtures
from engine.trends.page import render_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("engine.trends.run")


def run(today, days: int, espn, data_dir: str, json_path: str, html_path: str, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    engine = TrendEngine.from_dir(data_dir)
    names = {lg: set(engine.teams(lg)) for lg in SLUGS}
    fixtures, skipped_names = upcoming_fixtures(espn, list(SLUGS), names, today, days, now)
    report = build_report(engine, fixtures)
    report["skipped"] = skipped_names + report["skipped"]
    through = {lg: (engine.league_matches[lg][0].date.isoformat() if engine.league_matches.get(lg) else None)
               for lg in SLUGS}
    payload = {"generated_at": now.isoformat(), "data_through": through, **report}

    for path in (json_path, html_path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_page(report, now, through))
    return payload


def print_summary(payload: dict) -> None:
    print("\n=== TRENDS RUN ===")
    print(f"data through: {payload['data_through']}")
    per = {}
    for f in payload["fixtures"]:
        per.setdefault(f["league"], []).append(f)
    for lg, fs in per.items():
        print(f"[{lg}] {len(fs)} fixtures, bets per fixture: {[len(f['bets']) for f in fs]}")
    if not per:
        print("no upcoming fixtures found")
    for s in payload["skipped"]:
        print(f"SKIPPED {s['league']}: {s['home']} v {s['away']} - {s['reason']}")


def main() -> None:
    p = argparse.ArgumentParser(description="Build the trends dashboard")
    p.add_argument("--days", type=int, default=S.DAYS_AHEAD)
    p.add_argument("--today", default=None, help="YYYY-MM-DD (testing); default today UTC")
    p.add_argument("--espn-budget", type=int, default=S.ESPN_FIXTURE_BUDGET)
    p.add_argument("--data-dir", default=config.MATCH_DATA_DIR)
    p.add_argument("--json", default=S.TRENDS_JSON_PATH)
    p.add_argument("--html", default=S.HTML_PATH)
    a = p.parse_args()
    today = (datetime.strptime(a.today, "%Y-%m-%d").date() if a.today
             else datetime.now(timezone.utc).date())
    espn = EspnClient(budget=a.espn_budget, session=make_session())
    payload = run(today, a.days, espn, a.data_dir, a.json, a.html)
    print_summary(payload)
    print(f"ESPN requests used: {espn.used}")


if __name__ == "__main__":
    main()
