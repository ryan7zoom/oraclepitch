"""
engine/trends/page.py

Renders the dashboard (docs/index.html): one static, self-contained page.
Kickoff times are shown in GMT+8.
"""

from datetime import datetime, timedelta, timezone
from html import escape

from engine.trends import settings as S

TZ = timezone(timedelta(hours=S.TZ_OFFSET_HOURS))

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mute:#667085;--line:#e4e7ec;--accent:#0b6b3a;--chip:#e8f3ec}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--card:#171b22;--ink:#e8ebf0;--mute:#9aa4b2;--line:#262c36;--accent:#4cc38a;--chip:#17301f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:16px}
h1{margin:8px 0 2px;font-size:22px}.sub{color:var(--mute);font-size:13px;margin-bottom:12px}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 14px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 12px;border-radius:999px;cursor:pointer;font:inherit;font-size:13px}
.filters button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.day{margin:18px 0 6px;font-weight:600;color:var(--mute);font-size:13px;text-transform:uppercase;letter-spacing:.04em}
details.fx{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:8px 0;overflow:hidden}
details.fx>summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
details.fx>summary::-webkit-details-marker{display:none}
.lg{font-size:11px;font-weight:700;color:var(--accent);background:var(--chip);padding:2px 8px;border-radius:999px}
.ko{color:var(--mute);font-size:13px;min-width:44px}.teams{font-weight:600}.cnt{margin-left:auto;color:var(--mute);font-size:12px}
.body{padding:0 14px 12px;border-top:1px solid var(--line)}
.grp{margin:12px 0 4px;font-size:12px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.bet{margin:6px 0 10px}.bl{font-weight:600}.avg{font-weight:400;color:var(--mute);font-size:12px;margin-left:6px}
.tr{display:flex;gap:8px;align-items:baseline;color:var(--ink);font-size:14px;margin:2px 0 0 2px}
.hit{flex:none;min-width:46px;text-align:center;font-size:12px;font-weight:700;color:var(--accent);background:var(--chip);border-radius:6px;padding:1px 6px}
.none{color:var(--mute);padding:10px 0}.foot{color:var(--mute);font-size:12px;margin:22px 0 8px}
"""

JS = """
const btns=document.querySelectorAll('.filters button');
btns.forEach(b=>b.addEventListener('click',()=>{
  btns.forEach(x=>x.classList.remove('on'));b.classList.add('on');
  const l=b.dataset.l;
  document.querySelectorAll('[data-league]').forEach(e=>{e.style.display=(l==='all'||e.dataset.league===l)?'':'none'});
  document.querySelectorAll('.day').forEach(d=>{
    let n=d.nextElementSibling,show=false;
    while(n&&!n.classList.contains('day')){if(n.style.display!=='none')show=true;n=n.nextElementSibling}
    d.style.display=show?'':'none'});
}));
"""


def _kick(f):
    if f.get("kickoff"):
        return datetime.fromisoformat(f["kickoff"]).astimezone(TZ)
    return None


def _bet_html(b) -> str:
    avg = (f'<span class="avg" title="How often this happens in the league anyway">'
           f'league avg {b["base_rate"]:.0%}</span>') if b.get("base_rate") is not None else ""
    rows = "".join(
        f'<div class="tr"><span class="hit">{t["hits"]}/{t["window"]}</span>'
        f'<span>{escape(t["text"])}</span></div>' for t in b["trends"])
    return f'<div class="bet"><div class="bl">{escape(b["label"])}{avg}</div>{rows}</div>'


def _fixture_html(f) -> str:
    k = _kick(f)
    by_group = {}
    for b in f["bets"]:
        by_group.setdefault(b["group"], []).append(b)
    body = ""
    for g in S.GROUP_ORDER:
        if g in by_group:
            body += f'<div class="grp">{escape(S.GROUP_LABELS[g])}</div>' + "".join(_bet_html(b) for b in by_group[g])
    if not body:
        body = '<div class="none">No strong trends for this match.</div>'
    label = S.LEAGUE_LABELS.get(f["league"], f["league"])
    n = len(f["bets"])
    ko = f"{k:%H:%M}" if k else "--:--"
    return (f'<details class="fx" data-league="{escape(f["league"])}"><summary>'
            f'<span class="lg">{escape(label)}</span><span class="ko">{ko}</span>'
            f'<span class="teams">{escape(f["home"])} v {escape(f["away"])}</span>'
            f'<span class="cnt">{n} bet{"s" if n != 1 else ""}</span></summary>'
            f'<div class="body">{body}</div></details>')


def render_page(report: dict, generated_at: datetime, data_through: dict = None) -> str:
    fixtures = report["fixtures"]
    leagues = [l for l in S.LEAGUE_LABELS if any(f["league"] == l for f in fixtures)]
    filters = '<button class="on" data-l="all">All</button>' + "".join(
        f'<button data-l="{l}">{escape(S.LEAGUE_LABELS[l])}</button>' for l in leagues)

    parts, last_day = [], None
    for f in sorted(fixtures, key=lambda f: (f.get("kickoff") or f["date"], f["league"], f["home"])):
        k = _kick(f)
        day = k.date() if k else datetime.fromisoformat(f["date"]).date()
        if day != last_day:
            parts.append(f'<div class="day">{day:%A %d %B}</div>')
            last_day = day
        parts.append(_fixture_html(f))
    if not parts:
        parts.append('<div class="none">No fixtures in the next few days (international break?).</div>')

    through = ""
    if data_through:
        through = " &middot; data up to " + ", ".join(
            f"{escape(S.LEAGUE_LABELS.get(l, l))} {d}" for l, d in data_through.items() if d)
    gen = generated_at.astimezone(TZ)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OraclePitch - football trends</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>OraclePitch</h1>
<div class="sub">Statistical streaks for upcoming top-5-league matches. Not predictions or betting advice.
Updated {gen:%d %b %Y %H:%M} GMT+8{through}.</div>
<div class="filters">{filters}</div>
{''.join(parts)}
<div class="foot">Each line reads "hits/games": e.g. 15/16 = happened in 15 of the last 16 qualifying games
(league games only). "League avg" is how often it happens in that league anyway - a streak
means more when that number is low. Streaks can occur by chance; use your own judgment.</div>
</div><script>{JS}</script></body></html>"""
