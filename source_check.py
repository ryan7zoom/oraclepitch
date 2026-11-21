"""
scripts/source_check.py

One-off feasibility test for OraclePitch's new data sources. Run from
GitHub Actions (see .github/workflows/source_check.yml) so the requests
come from the same servers the daily job will use.

Checks, per league (EPL, La Liga, Bundesliga, Serie A, Ligue 1):
  1. football-data.co.uk current-season CSV: reachable? latest match date?
     which stat columns exist, and how often they are filled in?
  2. ESPN unofficial API: most recent finished match -> does the match
     summary return corners, cards, shots, shots on target, offsides?
     Do the scoreboard line scores give half-by-half goals?
  3. Sofascore (information only): is it blocked from this runner?

Never fails the job: every problem is reported, not raised.
"""
import csv
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
TIMEOUT = 30

LEAGUES = {
    "epl":        {"fd": "E0",  "espn": "eng.1"},
    "la_liga":    {"fd": "SP1", "espn": "esp.1"},
    "bundesliga": {"fd": "D1",  "espn": "ger.1"},
    "serie_a":    {"fd": "I1",  "espn": "ita.1"},
    "ligue_1":    {"fd": "F1",  "espn": "fra.1"},
}

# football-data.co.uk columns -> what we need them for
FD_COLUMNS = {
    "FTHG": "full-time home goals", "FTAG": "full-time away goals",
    "HTHG": "half-time home goals", "HTAG": "half-time away goals",
    "HS": "home shots", "AS": "away shots",
    "HST": "home shots on target", "AST": "away shots on target",
    "HC": "home corners", "AC": "away corners",
    "HY": "home yellow cards", "AY": "away yellow cards",
    "HR": "home red cards", "AR": "away red cards",
    "HO": "home offsides", "AO": "away offsides",
}

# ESPN team statistic names we want (any of the aliases counts)
ESPN_STATS = {
    "corners": ["wonCorners", "cornerKicks"],
    "yellow cards": ["yellowCards"],
    "red cards": ["redCards"],
    "total shots": ["totalShots"],
    "shots on target": ["shotsOnTarget"],
    "offsides": ["offsides"],
}

today = datetime.now(timezone.utc).date()
season_start = today.year if today.month >= 7 else today.year - 1
SEASON_CODE = f"{str(season_start)[2:]}{str(season_start + 1)[2:]}"

report = {"run_at": datetime.now(timezone.utc).isoformat(), "season_code": SEASON_CODE,
          "football_data": {}, "espn": {}, "sofascore": {}}
lines = []


def out(s=""):
    print(s)
    lines.append(s)


def get(url, **kw):
    return requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)


# ---------------------------------------------------------------------------
# 1. football-data.co.uk
# ---------------------------------------------------------------------------
def parse_fd_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except Exception:
            pass
    return None


def check_football_data(league, code):
    url = f"https://www.football-data.co.uk/mmz4281/{SEASON_CODE}/{code}.csv"
    res = {"url": url}
    try:
        r = get(url)
        res["status"] = r.status_code
        if r.status_code != 200:
            return res
        text = r.content.decode("utf-8-sig", errors="replace")
        rows = [x for x in csv.DictReader(io.StringIO(text)) if x.get("HomeTeam")]
        res["rows"] = len(rows)
        dates = [d for d in (parse_fd_date(x.get("Date", "")) for x in rows) if d]
        if dates:
            latest = max(dates)
            res["latest_match"] = latest.isoformat()
            res["days_behind"] = (today - latest).days
        recent = rows[-30:]
        cols = {}
        for c in FD_COLUMNS:
            present = rows and c in rows[0]
            filled = sum(1 for x in recent if (x.get(c) or "").strip() != "") if present else 0
            cols[c] = {"in_file": bool(present), "filled_last30": filled, "of": len(recent)}
        res["columns"] = cols
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    return res


out(f"=== 1. football-data.co.uk (season {SEASON_CODE}) ===")
for league, c in LEAGUES.items():
    res = check_football_data(league, c["fd"])
    report["football_data"][league] = res
    if res.get("status") != 200:
        out(f"[{league}] FAILED: status={res.get('status')} {res.get('error', '')}")
        continue
    out(f"[{league}] OK  rows={res.get('rows')}  latest={res.get('latest_match')}  "
        f"days_behind={res.get('days_behind')}")
    for col, info in res["columns"].items():
        mark = "ok " if info["in_file"] and info["filled_last30"] > 0 else "MISSING"
        out(f"    {col:5} {mark:7} filled {info['filled_last30']}/{info['of']}  ({FD_COLUMNS[col]})")

# ---------------------------------------------------------------------------
# 2. ESPN
# ---------------------------------------------------------------------------
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def stat_lookup(team_block):
    return {s.get("name"): s.get("displayValue") for s in team_block.get("statistics", [])}


def espn_day(slug, day):
    """One day's scoreboard. Returns (status, body_start, events)."""
    r = get(f"{ESPN_BASE}/{slug}/scoreboard?dates={day:%Y%m%d}")
    if r.status_code != 200:
        return r.status_code, r.text[:200], []
    return 200, "", r.json().get("events", [])


def check_espn(league, slug):
    """v2: one request per day (the range query returned 400 in the first
    test), walking back up to 21 days to collect finished matches."""
    res = {}
    try:
        finished = []
        first_error = None
        for back in range(0, 22):
            day = today - timedelta(days=back)
            status, body, events = espn_day(slug, day)
            if status != 200:
                first_error = first_error or (status, body, day.isoformat())
                if back == 0:
                    break  # same failure would repeat for every day
                continue
            for e in events:
                if e.get("status", {}).get("type", {}).get("completed"):
                    finished.append(e)
        res["scoreboard_status"] = 200 if not first_error else first_error[0]
        if first_error:
            res["error_body"] = first_error[1]
            res["error_day"] = first_error[2]
            return res
        res["finished_21d"] = len(finished)
        if not finished:
            return res
        finished.sort(key=lambda e: e.get("date", ""))
        res["latest_finished_date"] = finished[-1].get("date")
        ev = finished[-1]
        res["sample_match"] = ev.get("name")
        comp = ev["competitions"][0]
        res["linescores"] = {c["homeAway"]: [ls.get("value") for ls in c.get("linescores", [])]
                             for c in comp.get("competitors", [])}
        r2 = get(f"{ESPN_BASE}/{slug}/summary?event={ev['id']}")
        res["summary_status"] = r2.status_code
        if r2.status_code != 200:
            res["error_body"] = r2.text[:200]
            return res
        teams = r2.json().get("boxscore", {}).get("teams", [])
        res["teams_in_boxscore"] = len(teams)
        found, names_seen = {}, set()
        for t in teams:
            stats = stat_lookup(t)
            names_seen.update(k for k in stats if k)
            for label, aliases in ESPN_STATS.items():
                for a in aliases:
                    if a in stats:
                        found.setdefault(label, {})[t.get("homeAway", "?")] = stats[a]
                        break
        res["found"] = found
        res["missing"] = [k for k in ESPN_STATS if k not in found]
        res["all_stat_names"] = sorted(names_seen)
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    return res


out()
out("=== 2. ESPN (unofficial API) ===")
for league, c in LEAGUES.items():
    res = check_espn(league, c["espn"])
    report["espn"][league] = res
    if res.get("error") or res.get("scoreboard_status") != 200:
        out(f"[{league}] FAILED: status={res.get('scoreboard_status')} {res.get('error', '')} "
            f"body={res.get('error_body', '')!r}")
        continue
    fd_latest = report["football_data"].get(league, {}).get("latest_match")
    out(f"[{league}] finished(21d)={res.get('finished_21d')}  "
        f"latest_finished={res.get('latest_finished_date')}  "
        f"(football-data latest: {fd_latest})  sample='{res.get('sample_match')}'")
    if "found" in res:
        for label in ESPN_STATS:
            v = res["found"].get(label)
            out(f"    {label:16} {'ok   ' + str(v) if v else 'MISSING'}")
        out(f"    half-by-half linescores: {res.get('linescores')}")
    else:
        out(f"    summary status: {res.get('summary_status')} {res.get('error_body', '')!r}")

# ---------------------------------------------------------------------------
# 3. Sofascore (information only)
# ---------------------------------------------------------------------------
out()
out("=== 3. Sofascore (information only) ===")
try:
    r = get(f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{today.isoformat()}")
    report["sofascore"] = {"status": r.status_code, "body_start": r.text[:120]}
    out(f"status={r.status_code}  (200 = reachable, 403 = blocked from this runner)")
except Exception as e:
    report["sofascore"] = {"error": f"{type(e).__name__}: {e}"}
    out(f"error: {e}")

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
out()
out("=== VERDICT ===")
fd_ok = [l for l, r in report["football_data"].items() if r.get("status") == 200]
espn_ok = [l for l, r in report["espn"].items() if r.get("found")]
out(f"football-data.co.uk reachable: {len(fd_ok)}/5 leagues")
out(f"ESPN match stats reachable:    {len(espn_ok)}/5 leagues")
fresh = [l for l in fd_ok if (report["football_data"][l].get("days_behind") or 99) <= 7]
out(f"football-data.co.uk fresh (<=7 days): {len(fresh)}/{len(fd_ok)}")
out("FRESHNESS: compare 'latest_finished' (ESPN) with 'football-data latest' above.")
out("If ESPN's date is later than football-data's, football-data is lagging.")
out("If they match, the gap is just a break in the calendar.")
off = [l for l, r in report["espn"].items() if r.get("found", {}).get("offsides")]
out(f"Offsides available (ESPN): {len(off)}/5 leagues")

with open("source_check_report.json", "w") as f:
    json.dump(report, f, indent=2)
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a") as f:
        f.write("```\n" + "\n".join(lines) + "\n```\n")
sys.exit(0)
