"""
engine/model/backtest.py - does the model actually work?

Walks forward through every league match since 2022-23. Each match is
predicted using ONLY earlier games, then compared with what happened.
Writes data/model/scorecard.json:
  * Brier score of the model vs just using the league's historical rate
  * calibration: when the model says 85%, how often does it hit?
  * the same split by established teams vs newly promoted / thin-history teams
  * measured dispersion (phi) and stat correlations used by the simulator
  * a bet-builder test: joint probability of 3 legs, copula vs naive product

Usage: python -m engine.model.backtest [--data-dir data/matches] [--out data/model/scorecard.json]
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

import config
from engine.data.store import Store
from engine.model import markets, sim
from engine.model.ratings import STATS, LeagueModel, promoted_priors

LEAGUES = ["epl", "la_liga", "bundesliga", "serie_a", "ligue_1"]
MIN_SEASON = 2022          # first season predicted (2021 is warm-up)
MIN_HIST = 5               # a team needs this many league games to be predicted
BUCKETS = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001)]


def load_matches(data_dir, league):
    st = Store(league, os.path.join(data_dir, f"{league}.csv"))
    st.load()
    return sorted([m for m in st.rows if m.finished], key=lambda m: (m.date, m.home))


def collect(data_dir, leagues=LEAGUES):
    """Pass 1: (match, mu-params, established?) for every eligible match."""
    recs = []
    for lg in leagues:
        ms = load_matches(data_dir, lg)
        priors_by_season = {}
        model = None
        cur = None
        for m in ms:
            if m.season != cur:                      # new season: refresh promoted-team priors
                cur = m.season
                priors_by_season[cur] = promoted_priors(ms, cur)
                if model is None:
                    model = LeagueModel(priors=priors_by_season[cur])
                else:
                    model.priors = priors_by_season[cur]
            if m.season >= MIN_SEASON and model.ready() and model.count[m.home] >= MIN_HIST and model.count[m.away] >= MIN_HIST:
                p = model.params_for(m.home, m.away)
                recs.append((lg, m, p))
            model.add(m)
    return recs


def estimate_phi_corr(recs):
    num, cnt = defaultdict(float), defaultdict(int)
    for _, m, p in recs:
        v = markets.values_of(m)
        for s in STATS:
            for side in ("h", "a"):
                x, mu = v.get(f"{s}_{side}"), p.mu.get(f"{s}_{side}")
                if x is not None and mu:
                    num[s] += (x - mu) ** 2 / mu; cnt[s] += 1
    phi = {s: float(np.clip(num[s] / cnt[s], 1.0, 6.0)) for s in STATS if cnt[s]}
    zs = []
    for _, m, p in recs:
        v = markets.values_of(m)
        row = []
        for name in markets.BASE_VARS:
            x, mu = v.get(name), p.mu.get(name)
            if x is None or not mu:
                row = None; break
            row.append((x - mu) / np.sqrt(phi[name[0]] * mu))
        if row:
            zs.append(row)
    corr = np.corrcoef(np.array(zs), rowvar=False) if len(zs) > 500 else np.eye(len(markets.BASE_VARS))
    return phi, corr


def evaluate(recs, phi, corr, builder_every=8, sim_n=1500):
    keys = markets.all_keys()
    base_n, base_h = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    agg = defaultdict(lambda: {"n": 0, "bm": 0.0, "bb": 0.0})
    cal = defaultdict(lambda: [[0, 0.0, 0.0] for _ in BUCKETS])           # group -> bucket -> n,sum_p,sum_hit
    cal_split = {"established": [[0, 0.0, 0.0] for _ in BUCKETS], "thin history": [[0, 0.0, 0.0] for _ in BUCKETS]}
    builder = {"n": 0, "joint": 0.0, "product": 0.0, "hit": 0.0}
    n_pred = 0
    for lg, m, p in recs:
        p.phi = {**p.phi, **phi}
        pr = markets.probs(p)
        act = markets.actuals(m, keys)
        thin = min(p.n_hist["h"], p.n_hist["a"]) < 20
        for k, a in act.items():
            pk = pr[k]
            g = markets.group_of(k)
            bn = base_n[lg][k]
            if bn >= 100:
                bp = base_h[lg][k] / bn
                agg[g]["n"] += 1
                agg[g]["bm"] += (pk - a) ** 2
                agg[g]["bb"] += (bp - a) ** 2
            if pk >= 0.5:
                for bi, (lo, hi) in enumerate(BUCKETS):
                    if lo <= pk < hi:
                        for tgt in (cal[g][bi], cal_split["thin history" if thin else "established"][bi]):
                            tgt[0] += 1; tgt[1] += pk; tgt[2] += a
                        break
        for k, a in act.items():
            base_n[lg][k] += 1; base_h[lg][k] += a
        n_pred += 1
        if n_pred % builder_every == 0 and not thin:
            # three most likely legs from different market groups
            cand = sorted(((pr[k], k) for k in act if 0.6 <= pr[k] <= 0.97 and not k.startswith(("dc", "res", "h1", "h2"))
                           or k in ("dc:1X", "dc:X2")), reverse=True)
            legs, used = [], set()
            for pk, k in cand:
                g = markets.group_of(k)
                if g not in used:
                    legs.append(k); used.add(g)
                if len(legs) == 3:
                    break
            if len(legs) == 3 and all(l in act for l in legs):
                s_ = sim.simulate(p, corr, n=sim_n, seed=n_pred)
                builder["n"] += 1
                builder["joint"] += sim.joint_prob(s_, legs)
                builder["product"] += float(np.prod([pr[l] for l in legs]))
                builder["hit"] += float(all(act[l] for l in legs))
    return agg, cal, cal_split, builder


def summarize_cal(rows):
    out, gap, tot = [], 0.0, 0
    for (lo, hi), (n, sp, sh) in zip(BUCKETS, rows):
        if n:
            out.append({"lo": lo, "hi": min(hi, 1.0), "n": n, "said": sp / n, "actual": sh / n})
            if n >= 50:
                gap += abs(sp / n - sh / n) * n; tot += n
    return out, (gap / tot if tot else None)


def run(data_dir, out_path):
    recs = collect(data_dir)
    phi, corr = estimate_phi_corr(recs)
    agg, cal, cal_split, builder = evaluate(recs, phi, corr)
    markets_out = {}
    for g, a in agg.items():
        buckets, gap = summarize_cal(cal[g])
        brier_m, brier_b = a["bm"] / a["n"], a["bb"] / a["n"]
        skill = 1 - brier_m / brier_b
        markets_out[g] = {"n": a["n"], "brier_model": brier_m, "brier_league_rate": brier_b,
                          "skill": skill, "calibration_gap": gap, "buckets": buckets,
                          "validated": bool(skill > 0 and gap is not None and gap <= 0.03)}
    pooled = [[0, 0.0, 0.0] for _ in BUCKETS]
    for g in cal:
        for bi, r in enumerate(cal[g]):
            for j in range(3):
                pooled[bi][j] += r[j]
    split_out = {k: dict(zip(("buckets", "gap"), summarize_cal(v))) for k, v in cal_split.items()}
    pooled_b, pooled_gap = summarize_cal(pooled)
    card = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": 1,
        "matches_tested": len(recs),
        "period": f"{MIN_SEASON}-{str(MIN_SEASON + 1)[2:]} onward, walk-forward (each match predicted from earlier games only)",
        "phi": phi, "corr": corr.tolist(),
        "markets": markets_out,
        "pooled": {"buckets": pooled_b, "gap": pooled_gap},
        "by_history": split_out,
        "builder": {"n": builder["n"],
                    "avg_joint_model": builder["joint"] / max(builder["n"], 1),
                    "avg_product_if_independent": builder["product"] / max(builder["n"], 1),
                    "actual_all_three_hit": builder["hit"] / max(builder["n"], 1)},
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(card, f, indent=1)
    return card


def print_card(card):
    print(f"\nBACKTEST: {card['matches_tested']} matches  ({card['period']})")
    print(f"phi (dispersion): { {k: round(v, 2) for k, v in card['phi'].items()} }")
    print(f"{'market group':24}{'n':>9}{'Brier model':>13}{'league rate':>12}{'skill':>8}{'calib gap':>10}  ok?")
    for g, r in sorted(card["markets"].items()):
        print(f"{g:24}{r['n']:9d}{r['brier_model']:13.4f}{r['brier_league_rate']:12.4f}{r['skill']*100:7.1f}%{(r['calibration_gap'] or 0)*100:9.1f}%  {'YES' if r['validated'] else 'no'}")
    print("\nCalibration (all markets, side the model favours >=50%):")
    for b in card["pooled"]["buckets"]:
        print(f"  said {b['said']*100:5.1f}%  ->  hit {b['actual']*100:5.1f}%   (n={b['n']})")
    for k, v in card["by_history"].items():
        print(f"  [{k}] mean calibration gap: {(v['gap'] or 0)*100:.1f} pts")
    b = card["builder"]
    print(f"\nBuilder test ({b['n']} three-leg slips of the model's likeliest legs): "
          f"copula says {b['avg_joint_model']*100:.1f}%, naive product {b['avg_product_if_independent']*100:.1f}%, actually hit {b['actual_all_three_hit']*100:.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=config.MATCH_DATA_DIR)
    ap.add_argument("--out", default="data/model/scorecard.json")
    a = ap.parse_args()
    print_card(run(a.data_dir, a.out))
