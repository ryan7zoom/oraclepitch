"""
engine/trends/page.py

Renders the dashboard (docs/index.html): one static, self-contained page.
Light by default with a dark-mode button (choice is remembered in the browser).
Kickoff times are shown in GMT+8.
"""

from datetime import datetime, timedelta, timezone
from html import escape

from engine.trends import settings as S
from engine.trends.names import display

TZ = timezone(timedelta(hours=S.TZ_OFFSET_HOURS))

CSS = """
:root{--bg:#eef1f6;--card:#fff;--ink:#101828;--mute:#5b6b7f;--line:#dfe5ee;--accent:#0f766e;
--chip:#e3f3f0;--banner:#d9f1ee;--banner-ink:#16403b;--shadow:0 1px 2px rgba(16,24,40,.04)}
:root[data-theme=dark]{--bg:#0d1319;--card:#161d26;--ink:#e8edf3;--mute:#93a1b3;--line:#263140;--accent:#2dd4bf;
--chip:#133a37;--banner:#123432;--banner-ink:#cdeee9;--shadow:none}
*{box-sizing:border-box}
html{color-scheme:light}html[data-theme=dark]{color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:28px 16px 48px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px}
h1{margin:0;font-size:34px;line-height:1.15;font-weight:800;letter-spacing:-.01em}
#theme{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:10px 18px;border-radius:999px;
cursor:pointer;font:inherit;font-size:16px;white-space:nowrap;box-shadow:var(--shadow)}
.banner{margin:14px 0 10px;padding:16px 20px;border-radius:14px;background:var(--banner);color:var(--banner-ink);font-size:17px}
.stale{display:none;margin-left:8px;font-weight:800;color:#b42318}
.stale.on{display:inline}
:root[data-theme=dark] .stale{color:#fda29b}
.note{color:var(--mute);margin:0 0 16px;font-size:16px}
details.card{background:var(--card);border:1px solid var(--line);border-radius:14px;margin:12px 0;box-shadow:var(--shadow);overflow:hidden}
details.fx{border-left:5px solid var(--accent)}
details.card>summary{list-style:none;cursor:pointer;padding:18px 22px;display:flex;align-items:center;gap:14px}
details.card>summary::-webkit-details-marker{display:none}
details.card>summary::before{content:"";flex:none;border-left:9px solid var(--accent);border-top:6px solid transparent;
border-bottom:6px solid transparent;transition:transform .15s}
details.card[open]>summary::before{transform:rotate(90deg)}
.ttl{font-size:22px;font-weight:800;line-height:1.2}
.sub{display:block;font-size:13px;font-weight:500;color:var(--mute);margin-top:3px}
.lg{font-weight:700;color:var(--accent)}
.body{padding:2px 22px 18px;border-top:1px solid var(--line)}
.sec{margin:18px -22px 2px;padding:10px 22px;font-weight:800;font-size:14px;background:var(--chip);color:var(--accent)}
.sec.gen{background:var(--bg);color:var(--mute)}
.sec small{display:block;font-weight:500;font-size:12px;opacity:.85;margin-top:1px}
.pill{display:inline-block;font-weight:700;color:var(--accent);background:var(--chip);border-radius:999px;padding:0 8px;margin-right:4px}
.grp{margin:16px 0 6px;font-size:12px;font-weight:800;color:var(--mute);text-transform:uppercase;letter-spacing:.06em}
.bet{margin:8px 0 14px}.bl{font-weight:700;font-size:16px}
.avg{font-weight:400;color:var(--mute);font-size:12px;margin-left:8px}
.tr{display:flex;gap:10px;align-items:baseline;font-size:15px;margin:3px 0 0 2px}
.hit{flex:none;min-width:48px;text-align:center;font-size:12px;font-weight:800;color:var(--accent);
background:var(--chip);border-radius:7px;padding:1px 7px}
.day{margin:22px 2px 4px;font-weight:800;color:var(--mute);font-size:13px;text-transform:uppercase;letter-spacing:.06em}
.chips{display:flex;flex-wrap:wrap;gap:8px;padding:2px 22px 18px;border-top:1px solid var(--line)}
.chips button{border:1px solid var(--line);background:var(--bg);color:var(--ink);padding:7px 14px;border-radius:999px;
cursor:pointer;font:inherit;font-size:14px}
.chips button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.chips input{flex:1 1 200px;border:1px solid var(--line);background:var(--bg);color:var(--ink);padding:8px 14px;
border-radius:999px;font:inherit;font-size:14px}
.empty{color:var(--mute);padding:14px 4px}
.foot{color:var(--mute);font-size:13px;margin:26px 2px 8px}
@media (max-width:520px){h1{font-size:28px}.ttl{font-size:19px}#theme{padding:8px 14px;font-size:14px}}
"""

HEAD_JS = """(function(){try{var t=localStorage.getItem('op-theme');if(t)document.documentElement.setAttribute('data-theme',t)}catch(e){}})();"""

JS = """
(function(){
var r=document.documentElement,b=document.getElementById('theme');
function lab(){b.textContent=r.getAttribute('data-theme')==='dark'?'\\u2600 Light mode':'\\u263E Dark mode'}
b.onclick=function(){var n=r.getAttribute('data-theme')==='dark'?'light':'dark';r.setAttribute('data-theme',n);
try{localStorage.setItem('op-theme',n)}catch(e){}lab()};lab();
var ago=document.getElementById('ago'),stale=document.getElementById('stale');
function agoText(){var m=Math.max(0,Math.floor((Date.now()-Date.parse(ago.dataset.ts))/6e4));
if(m<1)return 'just now';if(m<60)return m+' min ago';
var h=Math.floor(m/60),r=m%60;if(h<48)return h+(h===1?' hour ':' hours ')+(r?r+' min ':'')+'ago';
return Math.floor(h/24)+' days ago'}
function tick(){if(!ago)return;ago.textContent=agoText();
if(stale)stale.classList.toggle('on',(Date.now()-Date.parse(ago.dataset.ts))/36e5>=parseFloat(stale.dataset.hours))}
tick();setInterval(tick,30000);
var league='all',q='';
function apply(){
document.querySelectorAll('details.fx').forEach(function(e){
var okL=league==='all'||e.dataset.league===league,okQ=!q||e.dataset.teams.indexOf(q)>-1;
e.style.display=okL&&okQ?'':'none'});
document.querySelectorAll('.day').forEach(function(d){var n=d.nextElementSibling,s=false;
while(n&&!n.classList.contains('day')){if(n.style.display!=='none')s=true;n=n.nextElementSibling}
d.style.display=s?'':'none'})}
var btns=document.querySelectorAll('.chips button');
btns.forEach(function(x){x.addEventListener('click',function(){btns.forEach(function(y){y.classList.remove('on')});
x.classList.add('on');league=x.dataset.l;apply()})});
var s=document.getElementById('search');
if(s)s.addEventListener('input',function(){q=s.value.trim().toLowerCase();apply()});
})();
"""


def _kick(f):
    if f.get("kickoff"):
        return datetime.fromisoformat(f["kickoff"]).astimezone(TZ)
    return None


def _bet_html(b) -> str:
    avg = (f'<span class="avg" title="Expected value for this match from both teams\' strength and the '
           f'opponent\'s level. Bookmaker lines sit near this number.">fair line &asymp; {b["fair"]:g}</span>'
           ) if b.get("fair") is not None else ""
    rows = "".join(
        f'<div class="tr"><span class="hit" title="happened in {t["hits"]} of the last {t["window"]} games">'
        f'{t["hits"]}/{t["window"]}</span><span>{escape(t["text"])}</span></div>' for t in b["trends"])
    return f'<div class="bet"><div class="bl">{escape(b["label"])}{avg}</div>{rows}</div>'


def _groups_html(bets) -> str:
    by_group = {}
    for b in bets:
        by_group.setdefault(b["group"], []).append(b)
    out = ""
    for g in S.GROUP_ORDER:
        if g in by_group:
            out += f'<div class="grp">{escape(S.GROUP_LABELS[g])}</div>' + "".join(_bet_html(b) for b in by_group[g])
    return out


def _fixture_html(f) -> str:
    k = _kick(f)
    home, away = display(f["home"]), display(f["away"])
    match = [b for b in f["bets"] if b.get("matching")]
    other = [b for b in f["bets"] if not b.get("matching")]
    body = ""
    if match:
        body += ('<div class="sec">&#9733; Highest matching trends'
                 '<small>One team does it and the opponent allows it (or both teams\' form agrees)</small></div>'
                 + _groups_html(match))
    if other:
        body += ('<div class="sec gen">General trends'
                 '<small>One side has a strong streak; the other side is unknown or does not agree</small></div>'
                 + _groups_html(other))
    if not body:
        body = '<div class="empty">No strong trends for this match.</div>'
    label = S.LEAGUE_LABELS.get(f["league"], f["league"])
    ko = f"{k:%H:%M} &middot; " if k else ""
    bits = []
    if match:
        bits.append(f'<span class="pill">&#9733; {len(match)} matching</span>')
    if other:
        bits.append(f"{len(other)} other")
    counts = " ".join(bits) if bits else "no strong trends"
    teams = escape(f"{home} {away} {f['home']} {f['away']}".lower())
    return (f'<details class="card fx" data-league="{escape(f["league"])}" data-teams="{teams}"><summary>'
            f'<div><span class="ttl">{escape(home)} v {escape(away)}</span>'
            f'<span class="sub"><span class="lg">{escape(label)}</span> &middot; {ko}{counts}</span></div></summary>'
            f'<div class="body">{body}</div></details>')


def render_page(report: dict, generated_at: datetime, data_through: dict = None) -> str:
    fixtures = report["fixtures"]
    leagues = [l for l in S.LEAGUE_LABELS if any(f["league"] == l for f in fixtures)]
    chips = '<button class="on" data-l="all">All</button>' + "".join(
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
        parts.append('<div class="empty">No fixtures in the next few days (international break?).</div>')

    gen = generated_at.astimezone(TZ)
    played = ""
    if data_through:
        latest = max((d for d in data_through.values() if d), default=None)
        if latest:
            played = f" &middot; matches played up to {datetime.fromisoformat(latest):%d/%m/%Y}"
    skipped = report.get("skipped") or []
    skipped_html = ""
    if skipped:
        names = ", ".join(escape(f"{s['home']} v {s['away']}") for s in skipped)
        skipped_html = f'<div class="foot">Not shown (team name not matched): {names}.</div>'
    n_fx = len(fixtures)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OraclePitch - football trends</title>
<script>{HEAD_JS}</script><style>{CSS}</style></head>
<body><div class="wrap">
<div class="top"><h1>OraclePitch</h1><button id="theme" type="button">&#9790; Dark mode</button></div>
<div class="banner">Trends built {gen:%d/%m/%Y %H:%M} (GMT+8){played}. Updated <span id="ago" data-ts="{generated_at.astimezone(timezone.utc).isoformat()}"></span>.<span id="stale" class="stale" data-hours="{S.STALE_AFTER_HOURS}">&#9888; Data is more than {S.STALE_AFTER_HOURS} hours old - the update may have failed.</span></div>
<p class="note">Streaks from league games only, for the next few days of EPL, La Liga, Bundesliga, Serie A and Ligue 1.
Lines the match is almost certain to clear are left out. Not predictions or betting advice.</p>
<details class="card"><summary><span class="ttl">Filters &middot; {n_fx} match{"es" if n_fx != 1 else ""}</span></summary>
<div class="chips">{chips}<input id="search" type="search" placeholder="Search team"></div></details>
{''.join(parts)}
{skipped_html}
<div class="foot">Each line reads "hits/games": 9/12 means it happened in 9 of the last 12 qualifying games.
"Fair line" is the number the model expects for this match, from each team's recent form and the opponent's level
(a team's attack is raised against a defence that allows a lot, and lowered against one that allows little).
Lines too easy to clear are hidden; each ladder lists up to three lines, best hit rate first.
Streaks can happen by chance; use your own judgment.</div>
</div><script>{JS}</script></body></html>"""
