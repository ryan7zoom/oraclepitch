"""
engine/data/update.py

Daily match-data update:  python -m engine.data.update

For each of the five leagues:
  1. football-data.co.uk: refetch the current season (older seasons only the
     first time, or with --full) and merge into data/matches/<league>.csv.
  2. ESPN: pass 1 (every league first): add matches finished in the last few
     days that football-data has not posted yet. Pass 2: fill OFFSIDES for older
     matches, newest first, with what is left of the budget split EQUALLY
     across leagues. Later runs continue where it stopped.
  3. Write data/matches/_report.json and print a summary (coverage per stat,
     latest date, ESPN team names that could not be matched).

A failure in one source/league is logged and reported, never fatal to the rest.
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import config
from engine.data import football_data
from engine.data.espn import BudgetExhausted, EspnClient, SLUGS
from engine.data.http import make_session
from engine.data.model import STAT_FIELDS, Match, season_of
from engine.data.store import Store
from engine.data.teams import TeamResolver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("engine.data.update")

REPORT_FIELDS = ["ft_h", "ht_h", "sh_h", "sot_h", "co_h", "yc_h", "rc_h", "of_h"]


def update_football_data(store: Store, league: str, today, session, full: bool, report: dict) -> None:
    current = season_of(today)
    have = {m.season for m in store.rows}
    for season in range(current - config.DATA_SEASONS_BACK, current + 1):
        if not (season == current or full or season not in have):
            continue
        try:
            matches = football_data.fetch_season(league, season, session)
        except Exception as e:
            logger.warning(f"[{league}] football-data {season} failed: {e}")
            report["errors"].append(f"football-data {season}: {e}")
            continue
        for m in matches:
            store.upsert_fd(m)
        logger.info(f"[{league}] football-data {season}: {len(matches)} matches")


def update_espn(store: Store, league: str, today, espn, report: dict, phase: str = "recent") -> None:
    """phase "recent": the last ESPN_RECENT_DAYS days (new matches + their stats).
    phase "backfill": older matches that still lack offsides. Run "recent" for ALL
    leagues first, so no league is starved by another one's backfill."""
    resolver = TeamResolver(league, store.names())
    first_season = season_of(today) - (config.OFFSIDES_SEASONS - 1)
    recent = {today - timedelta(days=k) for k in range(config.ESPN_RECENT_DAYS)}

    need_by_date = defaultdict(list)
    for m in store.rows:
        if (m.season >= first_season and m.needs_offsides
                and m.espn_tried < config.ESPN_MAX_TRIES
                and ((m.date in recent) == (phase == "recent"))):
            need_by_date[m.date].append(m)

    dates = sorted(recent if phase == "recent" else set(need_by_date), reverse=True)
    created = filled = 0

    try:
        for d in dates:
            events = espn.scoreboard(league, d)
            touched = set()
            for ev in events:
                if not ev.completed or ev.home_score is None or ev.away_score is None:
                    continue
                home, away = resolver.resolve(ev.home_name), resolver.resolve(ev.away_name)
                if not (home and away):
                    continue
                row = store.find(home, away, ev.date)
                if row is not None:
                    touched.add(id(row))
                    if not row.needs_offsides:
                        continue
                stats = espn.summary(league, ev.id)
                if stats is None:
                    if row is not None:
                        row.espn_tried += 1
                    continue
                if row is not None:
                    store.fill_from_espn(row, stats, ev.ht)
                    row.espn_id = ev.id
                    filled += 1
                else:
                    m = Match(league=league, season=season_of(ev.date), date=ev.date,
                              home=home, away=away, ft_h=ev.home_score, ft_a=ev.away_score,
                              espn_id=ev.id)
                    if ev.ht:
                        m.ht_h, m.ht_a = ev.ht
                    for f, v in stats.items():
                        setattr(m, f, v)
                    store.add_from_espn(m)
                    created += 1
            for m in need_by_date.get(d, []):
                if id(m) not in touched:
                    m.espn_tried += 1
    except BudgetExhausted:
        logger.info(f"[{league}] ESPN request budget used up - will continue next run")
        report["budget_exhausted"] = True

    report["espn_filled"] = report.get("espn_filled", 0) + filled
    report["espn_created"] = report.get("espn_created", 0) + created
    report["unmatched_espn_names"] = sorted(set(report.get("unmatched_espn_names", [])) | resolver.unmatched)
    if resolver.unmatched:
        logger.warning(f"[{league}] ESPN names not matched: {sorted(resolver.unmatched)}")


def build_report(store: Store, today, report: dict) -> None:
    rows = [m for m in store.rows if m.season >= season_of(today) - 1]
    report["rows_total"] = len(store.rows)
    report["latest_match"] = max((m.date.isoformat() for m in store.rows), default=None)
    report["coverage_last_2_seasons_pct"] = {
        f: round(100 * sum(getattr(m, f) is not None for m in rows) / len(rows), 1) if rows else None
        for f in REPORT_FIELDS
    }
    report["offsides_missing_last_2_seasons"] = sum(m.needs_offsides for m in rows)


def run(today, leagues, budget: int, full: bool, use_espn: bool, espn=None) -> dict:
    session = make_session()
    if use_espn and espn is None:
        espn = EspnClient(budget=budget, session=session)
    stores, reports = {}, {}
    for league in leagues:
        reports[league] = {"errors": []}
        stores[league] = Store(league, os.path.join(config.MATCH_DATA_DIR, f"{league}.csv"))
        stores[league].load()
        update_football_data(stores[league], league, today, session, full, reports[league])

    if use_espn:
        total = espn.budget
        for league in leagues:                       # pass 1: recent days, every league
            update_espn(stores[league], league, today, espn, reports[league], "recent")
        for i, league in enumerate(leagues):         # pass 2: offsides backfill, equal shares
            share = max(0, (total - espn.used) // (len(leagues) - i))
            espn.budget = espn.used + share
            update_espn(stores[league], league, today, espn, reports[league], "backfill")
        espn.budget = total

    full_report = {}
    for league in leagues:
        build_report(stores[league], today, reports[league])
        stores[league].save()
        full_report[league] = reports[league]
    if use_espn:
        full_report["_espn_requests_used"] = espn.used
    return full_report


def print_summary(full_report: dict) -> None:
    print("\n=== MATCH DATA UPDATE ===")
    for league, r in full_report.items():
        if league.startswith("_"):
            continue
        print(f"[{league}] rows={r.get('rows_total')} latest={r.get('latest_match')} "
              f"offsides_missing(last 2 seasons)={r.get('offsides_missing_last_2_seasons')} "
              f"espn_filled={r.get('espn_filled')} espn_new={r.get('espn_created')}")
        print(f"    coverage %: {r.get('coverage_last_2_seasons_pct')}")
        if r.get("unmatched_espn_names"):
            print(f"    UNMATCHED ESPN NAMES: {r['unmatched_espn_names']}")
        for e in r["errors"]:
            print(f"    ERROR: {e}")
    print(f"ESPN requests used: {full_report.get('_espn_requests_used')}")


def main():
    p = argparse.ArgumentParser(description="Update the match-data CSVs")
    p.add_argument("--leagues", default=None, help=f"comma list, default all: {list(SLUGS)}")
    p.add_argument("--espn-budget", type=int, default=config.ESPN_REQUEST_BUDGET)
    p.add_argument("--full", action="store_true", help="refetch every football-data season")
    p.add_argument("--no-espn", action="store_true")
    p.add_argument("--today", default=None, help="YYYY-MM-DD (testing); default: today (UTC)")
    args = p.parse_args()

    today = (datetime.strptime(args.today, "%Y-%m-%d").date() if args.today
             else datetime.now(timezone.utc).date())
    leagues = args.leagues.split(",") if args.leagues else list(SLUGS)

    full_report = run(today, leagues, args.espn_budget, args.full, not args.no_espn)
    os.makedirs(config.MATCH_DATA_DIR, exist_ok=True)
    with open(os.path.join(config.MATCH_DATA_DIR, "_report.json"), "w") as f:
        json.dump({"run_at": datetime.now(timezone.utc).isoformat(), **full_report}, f, indent=2)
    print_summary(full_report)

    if all(r["errors"] and not r.get("rows_total") for k, r in full_report.items() if not k.startswith("_")):
        sys.exit(1)


if __name__ == "__main__":
    main()
