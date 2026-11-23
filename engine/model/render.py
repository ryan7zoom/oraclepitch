"""
engine/model/render.py - the dashboard page (one static HTML file).

Every market is a tappable button showing the model probability and the league
average for the same line. Tapped legs go into a combo bar that computes the
JOINT probability from the match simulation embedded in the page.
"""

import html
from datetime import datetime

from engine.model import markets, sim

# Mirrors markets.eval_key (parity-tested against Python with node in tests/).
JS_EVAL = r"""
function evalKey(key, v) {
  const p = key.split(':');
  const val = (sc, who) => { const h = v[sc + '_h'], a = v[sc + '_a'];
    return who === 'tot' ? h + a : (who === 'h' ? h : a); };
  if (p[0] === 'res' || p[0] === 'h1' || p[0] === 'h2') {
    const sc = {res: 'g', h1: 'g1', h2: 'g2'}[p[0]];
    const h = v[sc + '_h'], a = v[sc + '_a'];
    return p[1] === 'H' ? h > a : (p[1] === 'D' ? h === a : h < a);
  }
  if (p[0] === 'dc') {
    const h = v.g_h, a = v.g_a;
    return p[1] === '1X' ? h >= a : (p[1] === 'X2' ? a >= h : h !== a);
  }
  if (p[0] === 'btts') { return ((v.g_h > 0 && v.g_a > 0) === (p[1] === 'Y')); }
  const x = val(p[0], p[1]); const line = parseFloat(p[2].slice(1));
  return p[2][0] === 'O' ? x > line : x < line;
}
"""

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1a1d21;--dim:#6b7280;--line:#e5e7eb;--accent:#2563eb;--fill:#bfdbfe;--good:#15803d;--warn:#b45309}
:root:not([data-theme=light]){color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--text:#e8eaed;--dim:#9aa3af;--line:#262c35;--accent:#60a5fa;--fill:#1e3a5f;--good:#4ade80;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:12px 12px 120px}
h1{font-size:20px;margin:4px 0}h2.day{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:20px 4px 8px}
.sub{color:var(--dim);font-size:12px;margin:0 0 10px}
.card,.match{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:0 0 10px;overflow:hidden}
summary{cursor:pointer;padding:12px;list-style:none}summary::-webkit-details-marker{display:none}
.top{display:flex;gap:8px;font-size:12px;color:var(--dim)}.ko{font-weight:700;color:var(--accent)}
.teams{font-size:16px;margin:3px 0}.exp{font-size:12px;color:var(--dim)}
.bar{display:flex;height:6px;border-radius:3px;overflow:hidden;margin:6px 0 2px}.bar i{display:block}
.chip{display:inline-block;font-size:11px;padding:1px 6px;border-radius:8px;background:var(--line);margin:4px 4px 0 0}.chip.warn{color:var(--warn)}
.body{padding:0 12px 12px;border-top:1px solid var(--line)}
.sec{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);margin:14px 0 4px}
.lh{font-size:12px;color:var(--dim);margin:8px 0 3px}
.r{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:0 0 5px}.r3{grid-template-columns:repeat(3,1fr)}
.pb{font:inherit;font-size:14px;color:var(--text);text-align:left;border:1px solid var(--line);border-radius:8px;padding:6px 8px;background:linear-gradient(90deg,var(--fill) calc(var(--p)*1%),transparent 0);cursor:pointer;display:grid;grid-template-columns:1fr auto;column-gap:6px;align-items:baseline;min-width:0}
.pb span{overflow-wrap:anywhere}.pb b{font-variant-numeric:tabular-nums;white-space:nowrap}.pb i{grid-column:1/-1;font-style:normal;font-size:11px;color:var(--dim)}
.pb.on{outline:2px solid var(--accent)}.pb.lift b::after{content:" \\25B2";color:var(--good);font-size:10px;white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:4px 6px;text-align:right;border-bottom:1px solid var(--line)}td:first-child,th:first-child{text-align:left}
.note{font-size:12px;color:var(--dim);margin:8px 12px}.scbody{padding:0 12px 12px}
#combo{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 12px;display:none;box-shadow:0 -4px 16px rgba(0,0,0,.15)}
#combo .res{font-size:18px;font-weight:700}#combo .legs{font-size:12px;color:var(--dim);max-height:64px;overflow:auto}
#combo button{font:inherit;border:1px solid var(--line);background:none;color:var(--text);border-radius:8px;padding:4px 10px;float:right}
"""

JS = JS_EVAL + r"""
const COLS = ['g_h','g_a','c_h','c_a','s_h','s_a','t_h','t_a','g1_h','g1_a'];
const sims = {}; const legs = {};
function simOf(i){
  if(!sims[i]){const el=document.querySelector('.match[data-i="'+i+'"]');const b=atob(el.dataset.sim);
    const u=new Uint8Array(b.length);for(let k=0;k<b.length;k++)u[k]=b.charCodeAt(k);sims[i]=u;}
  return sims[i];
}
function rowVals(u,r){const v={};for(let c=0;c<10;c++)v[COLS[c]]=u[r*10+c];v.g2_h=v.g_h-v.g1_h;v.g2_a=v.g_a-v.g1_a;return v;}
function joint(i,keys){const u=simOf(i);const n=u.length/10;let ok=0;
  for(let r=0;r<n;r++){const v=rowVals(u,r);if(keys.every(k=>evalKey(k,v)))ok++;}return ok/n;}
function refresh(){
  const ms=Object.keys(legs).filter(i=>legs[i].size>0);const bar=document.getElementById('combo');
  if(!ms.length){bar.style.display='none';return;}
  let p=1,naive=1,n=0,txt=[];
  ms.forEach(i=>{const keys=[...legs[i]];p*=joint(i,keys);n+=keys.length;
    keys.forEach(k=>{const b=document.querySelector('.match[data-i="'+i+'"] .pb[data-k="'+k+'"]');
      naive*=parseFloat(b.dataset.p);txt.push(b.dataset.label);});});
  bar.style.display='block';
  bar.querySelector('.res').textContent=n+' leg'+(n>1?'s':'')+': '+(p*100).toFixed(1)+'% together';
  bar.querySelector('.sub2').textContent=n>1?('(multiplying them as if independent: '+(naive*100).toFixed(1)+'%)'):'';
  bar.querySelector('.legs').textContent=txt.join('  |  ');
}
document.addEventListener('click',e=>{
  const b=e.target.closest('.pb');if(b){const i=b.closest('.match').dataset.i;legs[i]=legs[i]||new Set();
    const k=b.dataset.k;if(legs[i].has(k)){legs[i].delete(k);b.classList.remove('on');}else{legs[i].add(k);b.classList.add('on');}refresh();}
  if(e.target.id==='clear'){Object.keys(legs).forEach(i=>legs[i].clear());document.querySelectorAll('.pb.on').forEach(x=>x.classList.remove('on'));refresh();}
});
"""


def esc(s):
    return html.escape(str(s), quote=True)


def pct(x):
    return f"{x * 100:.0f}"


def btn(i, key, label, p, base, full_label):
    lift = base is not None and p - base >= 0.08
    lg = f"<i>league avg {pct(base)}%</i>" if base is not None else ""
    return (f'<button class="pb{" lift" if lift else ""}" data-k="{esc(key)}" data-p="{p:.4f}" '
            f'data-label="{esc(full_label)}" style="--p:{p * 100:.0f}">'
            f"<span>{esc(label)}</span><b>{pct(p)}%</b>{lg}</button>")


def ladder(i, scope, who, title, probs, base, home, away):
    lines = markets.LINES[scope]["tot" if who == "tot" else "team"]
    scored = [(abs(probs[f"{scope}:{who}:O{ln}"] - 0.5), ln) for ln in lines]
    keep = sorted(ln for po, ln in scored if 0.06 <= probs[f"{scope}:{who}:O{ln}"] <= 0.94)
    if len(keep) < 3:
        keep = sorted(ln for _, ln in sorted(scored)[:3])
    who_name = {"tot": "", "h": home + " ", "a": away + " "}[who]
    rows = [f'<div class="lh">{esc(title)}</div>']
    for ln in keep:
        ko, ku = f"{scope}:{who}:O{ln}", f"{scope}:{who}:U{ln}"
        nm = markets.SCOPE_NAME[scope].lower()
        rows.append('<div class="r">'
                    + btn(i, ko, f"Over {ln}", probs[ko], base.get(ko), f"{who_name}{nm} over {ln}")
                    + btn(i, ku, f"Under {ln}", probs[ku], base.get(ku), f"{who_name}{nm} under {ln}")
                    + "</div>")
    return "".join(rows)


def match_body(i, pr, base, home, away):
    h = []
    def trio(prefix, items):
        cells = "".join(btn(i, f"{prefix}:{k}", lab, pr[f"{prefix}:{k}"], base.get(f"{prefix}:{k}"), full)
                        for k, lab, full in items)
        return f'<div class="r r3">{cells}</div>'
    h.append('<div class="sec">Result</div>')
    h.append(trio("res", [("H", home, f"{home} to win"), ("D", "Draw", "draw"), ("A", away, f"{away} to win")]))
    h.append(trio("dc", [("1X", f"{home} or draw", f"{home} or draw"), ("X2", f"{away} or draw", f"{away} or draw"), ("12", "No draw", "no draw")]))
    h.append('<div class="sec">Goals</div>')
    h.append(ladder(i, "g", "tot", "Total goals", pr, base, home, away))
    h.append(ladder(i, "g", "h", f"{home} goals", pr, base, home, away))
    h.append(ladder(i, "g", "a", f"{away} goals", pr, base, home, away))
    h.append('<div class="r">' + btn(i, "btts:Y", "Both teams score", pr["btts:Y"], base.get("btts:Y"), "both teams to score")
             + btn(i, "btts:N", "Not both", pr["btts:N"], base.get("btts:N"), "not both teams to score") + "</div>")
    for hk, name in (("h1", "1st half"), ("h2", "2nd half")):
        sc = "g1" if hk == "h1" else "g2"
        h.append(f'<div class="sec">{name}</div>')
        h.append(trio(hk, [("H", home, f"{home} to win the {name}"), ("D", "Draw", f"{name} draw"), ("A", away, f"{away} to win the {name}")]))
        h.append(ladder(i, sc, "tot", f"{name} total goals", pr, base, home, away))
        h.append(ladder(i, sc, "h", f"{home} {name} goals", pr, base, home, away))
        h.append(ladder(i, sc, "a", f"{away} {name} goals", pr, base, home, away))
    for sc in ("c", "s", "t"):
        if f"{sc}:tot:O{markets.LINES[sc]['tot'][0]}" not in pr:
            continue
        name = markets.SCOPE_NAME[sc]
        h.append(f'<div class="sec">{name}</div>')
        h.append(ladder(i, sc, "tot", f"Total {name.lower()}", pr, base, home, away))
        h.append(ladder(i, sc, "h", f"{home} {name.lower()}", pr, base, home, away))
        h.append(ladder(i, sc, "a", f"{away} {name.lower()}", pr, base, home, away))
    return "".join(h)


def scorecard_html(card, live, data_through):
    rows = []
    for g, r in sorted(card["markets"].items()):
        rows.append(f"<tr><td>{esc(g)}</td><td>{r['skill'] * 100:+.1f}%</td>"
                    f"<td>{(r['calibration_gap'] or 0) * 100:.1f} pts</td><td>{'&#10003;' if r['validated'] else 'weak'}</td></tr>")
    cal = "".join(f"<tr><td>said {b['said'] * 100:.0f}%</td><td>hit {b['actual'] * 100:.1f}%</td><td>{b['n']:,}</td></tr>"
                  for b in card["pooled"]["buckets"])
    b = card["builder"]
    if live["graded_matches"]:
        lv = "".join(f"<tr><td>said {r[1] / r[0] * 100:.0f}%</td><td>hit {r[2] / r[0] * 100:.1f}%</td><td>{int(r[0]):,}</td></tr>"
                     for r in live["buckets"] if r[0])
        live_html = (f"<p class='note'>Live record since {esc(live['since'])}: {live['graded_matches']} matches graded.</p>"
                     f"<table><tr><th>Model</th><th>Reality</th><th>Picks</th></tr>{lv}</table>")
    else:
        live_html = "<p class='note'>Live record: predictions are saved before kickoff and graded automatically. Results appear here after the first matches finish.</p>"
    return (f"<details class='card'><summary><b>Does it work?</b> &nbsp;<span class='sub'>backtest on {card['matches_tested']:,} real matches &middot; tap</span></summary>"
            "<div class='scbody'>"
            f"<p class='note' style='margin-left:0'>Each match was predicted using only earlier games, then checked. {esc(card['period'])}.</p>"
            "<table><tr><th>Market</th><th>Better than league average by</th><th>Off by</th><th>OK</th></tr>" + "".join(rows) + "</table>"
            "<p class='note' style='margin-left:0'>Calibration (picks the model favours):</p>"
            "<table><tr><th>Model</th><th>Reality</th><th>Picks</th></tr>" + cal + "</table>"
            f"<p class='note' style='margin-left:0'>Three-leg builders of the likeliest legs: model joint {b['avg_joint_model'] * 100:.0f}%, "
            f"multiplied as independent {b['avg_product_if_independent'] * 100:.0f}%, actually hit {b['actual_all_three_hit'] * 100:.0f}% (n={b['n']}). "
            "A high chance is never a guarantee: a 95% leg still misses 1 time in 20, and every extra leg lowers the combined chance.</p>"
            + live_html + f"<p class='note' style='margin-left:0'>Data through {esc(data_through)}.</p></div></details>")


def render(preds, card, live, updated, data_through, warnings) -> str:
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
           f"<title>OraclePitch</title><style>{CSS}</style></head><body>",
           "<h1>OraclePitch</h1>",
           f"<p class='sub'>Updated {esc(updated)} (GMT+8) &middot; match data through {esc(data_through)}</p>",
           scorecard_html(card, live, data_through)]
    day = None
    for i, p in enumerate(preds):
        if p["day"] != day:
            day = p["day"]
            out.append(f"<h2 class='day'>{esc(day)}</h2>")
        pr, home, away = p["probs"], p["home_name"], p["away_name"]
        eh, ea = p["params"].mu["g_h"], p["params"].mu["g_a"]
        hw, dr, aw = pr["res:H"], pr["res:D"], pr["res:A"]
        chips = "".join(f"<span class='chip warn'>&#9888; {esc(n)}: only {c} league games on record</span>" for n, c in p["thin"])
        out.append(
            f"<section class='match' data-i='{i}' data-sim='{p['sim']}'><details><summary>"
            f"<div class='top'><span class='lg'>{esc(p['league_name'])}</span><span class='ko'>{esc(p['kickoff'])}</span></div>"
            f"<div class='teams'><b>{esc(home)}</b> vs <b>{esc(away)}</b></div>"
            f"<div class='exp'>Expected goals {eh:.1f} &ndash; {ea:.1f} &middot; Home {pct(hw)}% &middot; Draw {pct(dr)}% &middot; Away {pct(aw)}%</div>"
            f"<div class='bar'><i style='width:{hw * 100:.0f}%;background:var(--accent)'></i><i style='width:{dr * 100:.0f}%;background:var(--dim)'></i><i style='width:{aw * 100:.0f}%;background:var(--warn)'></i></div>"
            f"{chips}</summary><div class='body'>{match_body(i, pr, p['base'], home, away)}</div></details></section>")
    if not preds:
        out.append("<p class='note'>No fixtures found in the next 3 days.</p>")
    if warnings:
        out.append("<p class='note'>" + esc(" | ".join(warnings)) + "</p>")
    out.append("<p class='note'>Percentages are the model's chance. &ldquo;league avg&rdquo; is how often that line hit across recent league matches; &#9650; marks lines this matchup makes clearly likelier. Tap any lines to see their combined chance.</p>")
    out.append("<div id='combo'><button id='clear'>Clear</button><div class='res'></div><div class='sub2 sub'></div><div class='legs'></div></div>")
    out.append(f"<script>{JS}</script></body></html>")
    return "".join(out)
