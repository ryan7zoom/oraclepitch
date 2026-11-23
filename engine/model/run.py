"""
engine/model/run.py - the daily job:  python -m engine.model.run

 1. load the match CSVs (kept fresh by engine/data/update.py)
 2. load (or rebuild, weekly) the backtest scorecard: dispersion + correlations
 3. fit each league's model on all matches
 4. predict every fixture in the next 3 days: all markets + a correlated simulation
 5. save predictions to the ledger, grade finished ones, write docs/index.html
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import config
from engine.data.store import Store
from engine.data.espn import EspnClient
from engine.data.teams import TeamResolver
from engine.model import backtest, ledger, markets, render, sim
from engine.model.model_io import base_rates
from engine.model.ratings import LeagueModel, promoted_priors

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("engine.model.run")

LEAGUE_NAMES = {"epl": "Premier League", "la_liga": "La Liga", "bundesliga": "Bundesliga",
                "serie_a": "Serie A", "ligue_1": "Ligue 1"}
GMT8 = timezone(timedelta(hours=8))
SCORECARD_MAX_AGE_DAYS = 7
SIM_N = 2000
WINDOW_DAYS = 2


def scorecard(data_dir, path, now):
    if os.path.exists(path):
        card = json.load(open(path))
        age = now - datetime.fromisoformat(card["generated"])
        if card.get("version") == 1 and age < timedelta(days=SCORECARD_MAX_AGE_DAYS):
            return card
    log.info("Rebuilding backtest scorecard (about a minute)...")
    return backtest.run(data_dir, path)


def collect_fixtures(models, stores, now, today_local, espn=None):
    """Upcoming matches (today..+2 in GMT+8) with exact kickoff times, from ESPN's scoreboard."""
    fixtures, warnings = [], []
    espn = espn or EspnClient(budget=60)
    days = [today_local + timedelta(days=k) for k in range(-1, WINDOW_DAYS + 2)]
    for lg, model in models.items():
        resolver = TeamResolver(lg, stores[lg].names())
        got, failed = [], False
        try:
            for d in days:
                got += espn.scoreboard(lg, d)
        except Exception as e:                      # budget / network: fall back below
            log.warning(f"[{lg}] ESPN fixtures failed: {e}")
            got, failed = [], True
        seen = set()
        for ev in got:
            if ev.completed or ev.kickoff is None or ev.id in seen:
                continue
            seen.add(ev.id)
            ko = ev.kickoff.astimezone(GMT8)
            if ko <= now.astimezone(GMT8) or not (today_local <= ko.date() <= today_local + timedelta(days=WINDOW_DAYS)):
                continue
            home, away = resolver.resolve(ev.home_name), resolver.resolve(ev.away_name)
            if not (home and away) or home not in model.count or away not in model.count:
                warnings.append(f"Could not match {ev.home_name} / {ev.away_name} ({LEAGUE_NAMES[lg]}) - skipped")
                continue
            fixtures.append({"league": lg, "date": ko.date(), "home": home, "away": away,
                             "home_name": ev.home_name, "away_name": ev.away_name, "ko_dt": ko})
        if failed:
            warnings.append(f"{LEAGUE_NAMES[lg]}: fixtures unavailable right now (ESPN) - will retry next run")
    return fixtures, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=config.MATCH_DATA_DIR)
    ap.add_argument("--out", default="docs/index.html")
    ap.add_argument("--model-dir", default="data/model")
    ap.add_argument("--ledger-dir", default="data/ledger")
    ap.add_argument("--now", default=None, help="ISO time (testing)")
    a = ap.parse_args()

    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    today_local = now.astimezone(GMT8).date()
    today_utc = now.date()

    card = scorecard(a.data_dir, os.path.join(a.model_dir, "scorecard.json"), now)
    phi = card["phi"]
    import numpy as np
    corr = np.array(card["corr"])

    stores, models, bases = {}, {}, {}
    latest = None
    for lg in backtest.LEAGUES:
        st = Store(lg, os.path.join(a.data_dir, f"{lg}.csv")); st.load()
        ms = sorted([m for m in st.rows if m.finished], key=lambda m: (m.date, m.home))
        if not ms:
            log.warning(f"[{lg}] no match data"); continue
        cur = max(m.season for m in ms)
        model = LeagueModel(phi=phi, priors=promoted_priors(ms, cur))
        for m in ms:
            model.add(m)
        stores[lg], models[lg] = st, model
        bases[lg] = base_rates([m for m in ms if m.season >= cur - 2])
        latest = max(latest or ms[-1].date, ms[-1].date)

    fixtures, warnings = collect_fixtures(models, stores, now, today_local)
    preds = []
    for fx in fixtures:
        lg, model = fx["league"], models[fx["league"]]
        p = model.params_for(fx["home"], fx["away"])
        thin = [(fx["home_name"], model.count[fx["home"]]), (fx["away_name"], model.count[fx["away"]])]
        thin = [(n, c) for n, c in thin if c < 20]
        preds.append({**fx, "params": p, "probs": markets.probs(p), "base": bases[lg], "thin": thin,
                      "league_name": LEAGUE_NAMES[lg],
                      "sim": sim.encode(sim.simulate(p, corr, n=SIM_N, seed=len(preds) + 1))})
    preds.sort(key=lambda x: (x["ko_dt"] or datetime.max.replace(tzinfo=GMT8), x["league"]))
    for p in preds:
        p["day"] = p["date"].strftime("%a %d %b")
        p["kickoff"] = p["ko_dt"].strftime("%H:%M") if p["ko_dt"] else "TBC"

    lpath = os.path.join(a.ledger_dir, "predictions.csv")
    spath = os.path.join(a.ledger_dir, "live_scorecard.json")
    rows = ledger.read(lpath)
    ledger.upsert(rows, [(p["date"], p["league"], p["home"], p["away"], p["params"]) for p in preds],
                  today_utc, now.isoformat(timespec="seconds"))
    live = json.load(open(spath)) if os.path.exists(spath) else ledger.new_live()
    graded = ledger.grade(rows, lambda lg, h, aw, d: stores[lg].find(h, aw, d) if lg in stores else None, live)
    ledger.write(lpath, rows)
    os.makedirs(a.ledger_dir, exist_ok=True)
    json.dump(live, open(spath, "w"), indent=1)

    page = render.render(preds, card, live, now.astimezone(GMT8).strftime("%d %b %H:%M"),
                         latest.isoformat() if latest else "n/a", warnings)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(page)
    log.info(f"Wrote {a.out}: {len(preds)} matches, {len(page) / 1e6:.2f} MB; graded {graded} finished predictions; "
             f"{len(warnings)} warnings")


if __name__ == "__main__":
    main()
