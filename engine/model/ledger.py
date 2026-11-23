"""
engine/model/ledger.py - the live record.

Every upcoming match is saved BEFORE kickoff as its model parameters (one
compact row; any market can be re-derived from them). Once the match is in
the data, every market is graded and added to the live scorecard, so the
page shows how the model is really doing - not just in a backtest.
Rows for matches on or after today may be refreshed; earlier days are frozen.
"""

import csv
import json
import os
from datetime import datetime

from engine.model import markets
from engine.model.backtest import BUCKETS
from engine.model.ratings import Params

COLUMNS = ["date", "league", "home", "away", "made_on", "params_json", "graded"]


def _dump(p: Params) -> str:
    return json.dumps({"mu": {k: round(v, 4) for k, v in p.mu.items()}, "share": p.share,
                       "phi": p.phi, "n_hist": p.n_hist}, separators=(",", ":"))


def _load(s: str) -> Params:
    d = json.loads(s)
    return Params(mu=d["mu"], share=d["share"], phi=d["phi"], n_hist=d.get("n_hist", {}))


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["date"], r["league"], r["home"])):
            w.writerow(r)


def upsert(rows, preds, today, now_iso):
    """preds: list of (date, league, home, away, Params). Frozen if date < today."""
    idx = {(r["date"], r["league"], r["home"], r["away"]): r for r in rows}
    for d, lg, h, a, p in preds:
        key = (d.isoformat(), lg, h, a)
        if key in idx:
            if idx[key]["graded"] == "1" or d < today:
                continue
            idx[key].update(made_on=now_iso, params_json=_dump(p))
        else:
            r = {"date": d.isoformat(), "league": lg, "home": h, "away": a,
                 "made_on": now_iso, "params_json": _dump(p), "graded": "0"}
            rows.append(r); idx[key] = r
    return rows


def grade(rows, find_match, live):
    """find_match(league, home, away, date) -> finished Match or None.
    live: {'graded_matches': n, 'buckets': [[n,sum_p,sum_hit]*5], 'since': iso}."""
    keys = markets.all_keys()
    n_new = 0
    for r in rows:
        if r["graded"] == "1":
            continue
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        m = find_match(r["league"], r["home"], r["away"], d)
        if m is None or not m.finished:
            continue
        pr = markets.probs(_load(r["params_json"]))
        for k, a in markets.actuals(m, keys).items():
            pk = pr[k]
            if pk >= 0.5:
                for bi, (lo, hi) in enumerate(BUCKETS):
                    if lo <= pk < hi:
                        live["buckets"][bi][0] += 1; live["buckets"][bi][1] += pk; live["buckets"][bi][2] += a
                        break
        r["graded"] = "1"
        live["graded_matches"] += 1
        live["since"] = live.get("since") or r["date"]
        n_new += 1
    return n_new


def new_live():
    return {"graded_matches": 0, "buckets": [[0, 0.0, 0.0] for _ in BUCKETS], "since": None}
