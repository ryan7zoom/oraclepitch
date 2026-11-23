"""
engine/model/markets.py - every market, as a key, from one set of parameters.

Key format
    res:H|D|A   dc:1X|X2|12   btts:Y|N   h1:H|D|A   h2:H|D|A
    <scope>:<who>:<O|U><line>     scope  g=goals  g1=1st-half goals  g2=2nd-half goals
                                          c=corners  s=shots  t=shots on target
                                  who    tot=match total  h=home team  a=away team
e.g. "c:h:U4.5" = home team under 4.5 corners.

probs()    -> exact model probability of every key (from the distributions)
eval_key() -> whether a key hit, given actual values (the same logic is
              mirrored in the page's JavaScript for the combo builder)
"""

import numpy as np

from engine.model import dist

LINES = {
    "g":  {"tot": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5], "team": [0.5, 1.5, 2.5, 3.5, 4.5]},
    "g1": {"tot": [0.5, 1.5, 2.5], "team": [0.5, 1.5]},
    "g2": {"tot": [0.5, 1.5, 2.5], "team": [0.5, 1.5]},
    "c":  {"tot": [x + 0.5 for x in range(6, 14)], "team": [x + 0.5 for x in range(2, 10)]},
    "s":  {"tot": [x + 0.5 for x in range(14, 31)], "team": [x + 0.5 for x in range(5, 20)]},
    "t":  {"tot": [x + 0.5 for x in range(4, 14)], "team": [x + 0.5 for x in range(1, 9)]},
}
SCOPE_NAME = {"g": "Goals", "g1": "1st-half goals", "g2": "2nd-half goals",
              "c": "Corners", "s": "Shots", "t": "Shots on target"}
BASE_VARS = ["g_h", "g_a", "c_h", "c_a", "s_h", "s_a", "t_h", "t_a"]
SIM_COLS = BASE_VARS + ["g1_h", "g1_a"]


def group_of(key: str) -> str:
    p = key.split(":")
    if p[0] in ("res", "dc"):
        return "Result"
    if p[0] == "btts":
        return "Both teams to score"
    if p[0] == "h1":
        return "1st-half result"
    if p[0] == "h2":
        return "2nd-half result"
    return {"g": "Goals", "g1": "1st-half goals", "g2": "2nd-half goals",
            "c": "Corners", "s": "Shots", "t": "Shots on target"}[p[0]]


def all_keys() -> list:
    keys = ["res:H", "res:D", "res:A", "dc:1X", "dc:X2", "dc:12", "btts:Y", "btts:N"]
    for h in ("h1", "h2"):
        keys += [f"{h}:H", f"{h}:D", f"{h}:A"]
    for scope, d in LINES.items():
        for who, lines in (("tot", d["tot"]), ("h", d["team"]), ("a", d["team"])):
            for ln in lines:
                keys += [f"{scope}:{who}:O{ln}", f"{scope}:{who}:U{ln}"]
    return keys


# ---------------------------------------------------------------------------
def pmfs(p) -> dict:
    phi = p.phi
    out = {}
    for side in ("h", "a"):
        mu_g = p.mu["g_" + side]
        sh = p.share[side]
        out["g_" + side] = dist.pmf(mu_g, phi["g"], dist.SUPPORT["g"])
        out["g1_" + side] = dist.pmf(mu_g * sh, 1.0, dist.SUPPORT["g"])
        out["g2_" + side] = dist.pmf(mu_g * (1 - sh), 1.0, dist.SUPPORT["g"])
        for s in ("c", "s", "t"):
            if f"{s}_{side}" in p.mu:
                out[f"{s}_{side}"] = dist.pmf(p.mu[f"{s}_{side}"], phi[s], dist.SUPPORT[s])
    return out


def _result_probs(ph, pa, prefix) -> dict:
    grid = np.outer(ph, pa)
    home = float(np.tril(grid, -1).sum())     # rows=home goals > cols=away goals
    draw = float(np.trace(grid))
    away = float(np.triu(grid, 1).sum())
    out = {f"{prefix}:H": home, f"{prefix}:D": draw, f"{prefix}:A": away}
    if prefix == "res":
        out.update({"dc:1X": home + draw, "dc:X2": away + draw, "dc:12": home + away})
    return out


def probs(p) -> dict:
    d = pmfs(p)
    out = {}
    out.update(_result_probs(d["g_h"], d["g_a"], "res"))
    out.update(_result_probs(d["g1_h"], d["g1_a"], "h1"))
    out.update(_result_probs(d["g2_h"], d["g2_a"], "h2"))
    yes = (1 - d["g_h"][0]) * (1 - d["g_a"][0])
    out["btts:Y"], out["btts:N"] = float(yes), float(1 - yes)
    for scope, lines in LINES.items():
        if f"{scope}_h" not in d:
            continue
        dh, da = d[f"{scope}_h"], d[f"{scope}_a"]
        tot = dist.conv(dh, da)
        for who, pm, ls in (("tot", tot, lines["tot"]), ("h", dh, lines["team"]), ("a", da, lines["team"])):
            for ln in ls:
                o = dist.p_over(pm, ln)
                out[f"{scope}:{who}:O{ln}"] = o
                out[f"{scope}:{who}:U{ln}"] = 1.0 - o
    return out


# ---------------------------------------------------------------------------
def _val(scope, who, v):
    h, a = v.get(f"{scope}_h"), v.get(f"{scope}_a")
    if h is None or a is None:
        return None
    return h + a if who == "tot" else (h if who == "h" else a)


def eval_key(key: str, v: dict):
    """True/False, or None if a needed value is missing. v has g_h,g_a,g1_h,g1_a,
    g2_h,g2_a,c_h,c_a,s_h,s_a,t_h,t_a (None where unknown)."""
    p = key.split(":")
    if p[0] in ("res", "h1", "h2"):
        sc = {"res": "g", "h1": "g1", "h2": "g2"}[p[0]]
        h, a = v.get(sc + "_h"), v.get(sc + "_a")
        if h is None or a is None:
            return None
        return {"H": h > a, "D": h == a, "A": h < a}[p[1]]
    if p[0] == "dc":
        h, a = v.get("g_h"), v.get("g_a")
        if h is None or a is None:
            return None
        return {"1X": h >= a, "X2": a >= h, "12": h != a}[p[1]]
    if p[0] == "btts":
        h, a = v.get("g_h"), v.get("g_a")
        if h is None or a is None:
            return None
        return (h > 0 and a > 0) == (p[1] == "Y")
    x = _val(p[0], p[1], v)
    if x is None:
        return None
    line = float(p[2][1:])
    return x > line if p[2][0] == "O" else x < line


def values_of(m) -> dict:
    """Actual values for a finished Match."""
    v = {"g_h": m.ft_h, "g_a": m.ft_a, "c_h": m.co_h, "c_a": m.co_a,
         "s_h": m.sh_h, "s_a": m.sh_a, "t_h": m.sot_h, "t_a": m.sot_a,
         "g1_h": m.ht_h, "g1_a": m.ht_a, "g2_h": None, "g2_a": None}
    if m.ht_h is not None and m.ht_a is not None and m.ft_h is not None and m.ft_a is not None:
        v["g2_h"], v["g2_a"] = m.ft_h - m.ht_h, m.ft_a - m.ht_a
    return v


def actuals(m, keys=None) -> dict:
    v = values_of(m)
    out = {}
    for k in (keys or all_keys()):
        r = eval_key(k, v)
        if r is not None:
            out[k] = 1 if r else 0
    return out
