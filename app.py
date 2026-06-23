"""
app.py

Most Profitable Load Agent — FastAPI + single-page enterprise dashboard (no Streamlit).

Driver / dispatcher facing tool:
  1. Natural-language query OR Location + Equipment + pickup-radius controls.
  2. Deterministic engine ranks loads the driver can actually pick up (within radius)
     by REAL net profit, pricing the expected empty (deadhead) miles out of the
     destination market.
  3. The #1 pick is shown big with a full itemized cost waterfall, side-by-side
     against the highest-GROSS load to make "pays more, earns less" obvious.
  4. A Fleet / Owner-Operator toggle re-frames driver pay (cost vs take-home).
  5. A second tab shows the top-10 US markets to start a carrier.

CRITICAL DESIGN RULE: all money math is 100% pure Python (cost_engine + forward_scorer).
The LLM (agent.py) only parses NL queries and writes prose. It never computes a number.
Every figure on screen is reproducible.
"""

import math
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from config import DEFAULTS, MARKET_SCORE_WEIGHTS, MODEL, PICKUP_RADIUS_MILES
from data_layer import load_all_loads, get_market_scores, get_top_markets
from agent import get_recommendation

app = FastAPI(title="Most Profitable Load Agent")


@app.on_event("startup")
def _warm() -> None:
    df = load_all_loads()
    ms = get_market_scores()
    print(f"[startup] {len(df):,} loads parsed, {len(ms)} markets scored. Ready.")


def _clean(obj: Any) -> Any:
    """pandas/numpy scalars -> plain python; NaN/Inf -> None (JSON-safe)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if hasattr(obj, "item"):
        try:
            return _clean(obj.item())
        except Exception:
            return obj
    return obj


class RecommendRequest(BaseModel):
    query: str = ""
    location: Optional[str] = None
    equipment: Optional[str] = None
    pickup_radius: Optional[float] = None
    hide_unprofitable: Optional[bool] = None


@app.post("/api/recommend")
def api_recommend(req: RecommendRequest) -> JSONResponse:
    result = get_recommendation(
        query=req.query or "",
        force_location=req.location or None,
        force_equipment=req.equipment or None,
        pickup_radius=req.pickup_radius,
        hide_unprofitable=req.hide_unprofitable,
    )
    return JSONResponse(_clean(result))


@app.get("/api/markets")
def api_markets() -> JSONResponse:
    rows = get_top_markets(10).to_dict(orient="records")
    return JSONResponse(_clean({"markets": rows, "weights": MARKET_SCORE_WEIGHTS}))


@app.get("/api/config")
def api_config() -> JSONResponse:
    return JSONResponse(_clean({
        "defaults": DEFAULTS, "model": MODEL, "pickup_radius": PICKUP_RADIUS_MILES,
    }))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>LoadIQ — Most Profitable Load Agent</title>
<style>
  :root{
    --bg:#0a0e17; --bg2:#0e1320; --panel:#131a2b; --panel2:#1a2238; --line:#263048;
    --line2:#323d5a; --text:#eef2fb; --muted:#8b99b8; --faint:#5d6a87;
    --green:#34d399; --greend:#0f3d2e; --red:#fb7185; --redd:#3d1620;
    --accent:#6c8cff; --accent2:#4d6bff; --gold:#fbbf24; --cyan:#38bdf8;
    --chip:#1c2540;
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
    font-size:14px;-webkit-font-smoothing:antialiased}
  a{color:var(--accent)}
  .app{display:grid;grid-template-columns:300px 1fr;min-height:100vh}

  /* ---- Sidebar ---- */
  .side{background:linear-gradient(180deg,var(--bg2),var(--bg));border-right:1px solid var(--line);
    padding:22px 20px;display:flex;flex-direction:column;gap:18px;position:sticky;top:0;height:100vh;overflow:auto}
  .brand{display:flex;align-items:center;gap:10px}
  .brand .logo{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--cyan));
    display:grid;place-items:center;font-size:18px}
  .brand h1{font-size:17px;margin:0;letter-spacing:-.3px}
  .brand small{color:var(--muted);font-size:11px}
  .grp{display:flex;flex-direction:column;gap:7px}
  .grp>label{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--faint);font-weight:600}
  input,select{width:100%;padding:10px 11px;border-radius:9px;border:1px solid var(--line);
    background:var(--panel);color:var(--text);font-size:13px;outline:none}
  input:focus,select:focus{border-color:var(--accent2)}
  .range{display:flex;align-items:center;gap:9px}
  .range input[type=range]{padding:0;accent-color:var(--accent)}
  .range b{min-width:64px;text-align:right;font-variant-numeric:tabular-nums;color:var(--cyan)}
  .seg{display:flex;background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:3px}
  .seg button{flex:1;border:none;background:transparent;color:var(--muted);padding:8px;border-radius:7px;
    cursor:pointer;font-size:12px;font-weight:600}
  .seg button.on{background:var(--accent2);color:#fff}
  .toggle{display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--muted)}
  .sw{position:relative;width:38px;height:21px;background:var(--line2);border-radius:99px;cursor:pointer;transition:.15s}
  .sw.on{background:var(--green)}
  .sw i{position:absolute;top:2px;left:2px;width:17px;height:17px;background:#fff;border-radius:50%;transition:.15s}
  .sw.on i{left:19px}
  .cta{padding:12px;border-radius:10px;border:none;background:linear-gradient(135deg,var(--accent),var(--accent2));
    color:#fff;font-weight:700;font-size:14px;cursor:pointer}
  .cta:disabled{opacity:.55;cursor:default}
  .side .foot{margin-top:auto;font-size:11px;color:var(--faint);line-height:1.5;border-top:1px solid var(--line);padding-top:14px}
  .side .foot b{color:var(--green)}

  /* ---- Main ---- */
  .main{padding:24px 30px 70px;max-width:1180px}
  .topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
  .tabs{display:flex;gap:6px}
  .tab{padding:9px 15px;border-radius:9px;background:var(--panel);color:var(--muted);cursor:pointer;
    font-size:13px;border:1px solid var(--line);font-weight:600}
  .tab.active{background:var(--accent2);color:#fff;border-color:var(--accent2)}
  .modepill{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:99px;padding:5px 11px}
  .modepill b{color:var(--cyan)}

  /* KPI strip */
  .kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}
  .kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px 14px}
  .kpi .v{font-size:21px;font-weight:800;letter-spacing:-.5px}
  .kpi .l{font-size:11px;color:var(--muted);margin-top:2px}
  .kpi.good .v{color:var(--green)} .kpi.warn .v{color:var(--gold)} .kpi.dim .v{color:var(--muted)}

  .explain{margin:0 0 18px;padding:14px 16px;background:linear-gradient(90deg,var(--panel2),var(--panel));
    border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;font-size:13.5px;line-height:1.55}
  .explain .who{font-size:11px;color:var(--cyan);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;font-weight:700}

  .sectitle{font-size:13px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);
    font-weight:700;margin:6px 0 10px}

  .compare{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .compare.single{grid-template-columns:1fr;max-width:560px}
  .pick{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;position:relative}
  .pick.win{border-color:var(--green);box-shadow:0 0 0 1px var(--green) inset,0 8px 30px rgba(52,211,153,.07)}
  .tag{position:absolute;top:-10px;left:16px;font-size:10.5px;font-weight:800;letter-spacing:.4px;
    padding:4px 10px;border-radius:99px}
  .tag.win{background:var(--green);color:#04210f}
  .tag.alt{background:var(--chip);color:var(--muted);border:1px solid var(--line2)}
  .lane{font-size:18px;font-weight:700;margin:6px 0 3px;letter-spacing:-.3px}
  .meta{color:var(--muted);font-size:12px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:6px}
  .chip{background:var(--chip);border:1px solid var(--line);border-radius:99px;padding:2px 9px;font-size:11px}
  .netwrap{display:flex;align-items:baseline;gap:10px;margin-bottom:2px}
  .net{font-size:32px;font-weight:900;letter-spacing:-1.2px}
  .net.g{color:var(--green)} .net.r{color:var(--red)}
  .nlbl{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.5px}
  .marginbadge{font-size:12px;font-weight:700;padding:3px 9px;border-radius:7px}
  .marginbadge.g{background:var(--greend);color:var(--green)} .marginbadge.r{background:var(--redd);color:var(--red)}

  /* cost waterfall */
  .wf{margin-top:15px;display:flex;flex-direction:column;gap:6px}
  .wf .row{display:grid;grid-template-columns:108px 1fr 78px;align-items:center;gap:9px;font-size:12.5px}
  .wf .row .n{color:var(--muted)} .wf .row.tot .n{color:var(--text);font-weight:700}
  .wf .bar{height:9px;border-radius:4px;background:var(--line);overflow:hidden}
  .wf .bar>i{display:block;height:100%}
  .wf .bar.fuel>i{background:#f59e0b} .wf .bar.driver>i{background:#a78bfa}
  .wf .bar.maint>i{background:#38bdf8} .wf .bar.ins>i{background:#f472b6} .wf .bar.toll>i{background:#94a3b8}
  .wf .bar.gross>i{background:var(--green)} .wf .bar.cost>i{background:var(--red)}
  .wf .v{text-align:right;font-variant-numeric:tabular-nums}
  .wf .row.tot{border-top:1px solid var(--line2);padding-top:7px;margin-top:3px}

  .milestrip{margin-top:13px;display:flex;height:24px;border-radius:7px;overflow:hidden;border:1px solid var(--line)}
  .milestrip>div{display:grid;place-items:center;font-size:10.5px;font-weight:700;color:#04210f;white-space:nowrap}
  .ms-din{background:#94a3b8} .ms-load{background:var(--green)} .ms-dout{background:#fbbf24}
  .mslabel{font-size:11px;color:var(--muted);margin-top:6px}

  .gauge{margin-top:13px}
  .gauge .top{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:5px}
  .gauge .top b{color:var(--gold)}
  .gbar{height:8px;border-radius:5px;background:var(--line);overflow:hidden}
  .gbar>i{display:block;height:100%;background:linear-gradient(90deg,#fb7185,#fbbf24,#34d399)}

  .delta{margin:16px 0 4px;text-align:center;font-size:13px;color:var(--muted);
    background:var(--panel);border:1px dashed var(--line2);border-radius:10px;padding:12px}
  .delta b{color:var(--green)}

  /* table */
  .card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:16px;margin-top:22px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
  th{color:var(--faint);font-weight:700;font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;cursor:default}
  td:first-child,td:nth-child(2),th:first-child,th:nth-child(2){text-align:left}
  tbody tr:hover{background:var(--panel2)}
  .rk{color:var(--gold);font-weight:800}
  .pos{color:var(--green)} .neg{color:var(--red)}
  .sbar{display:inline-block;width:46px;height:6px;border-radius:4px;background:var(--line);overflow:hidden;vertical-align:middle;margin-left:6px}
  .sbar>i{display:block;height:100%;background:var(--gold)}
  .loading{padding:50px;text-align:center;color:var(--muted)}
  .spin{width:22px;height:22px;border:3px solid var(--line);border-top-color:var(--accent);border-radius:50%;
    display:inline-block;animation:s .7s linear infinite;vertical-align:middle;margin-right:9px}
  @keyframes s{to{transform:rotate(360deg)}}
  .err{padding:16px;color:var(--red);background:var(--redd);border-radius:10px}
  .hide{display:none}
  .mkformula{font-size:12px;color:var(--muted);margin:0 0 14px;background:var(--panel2);
    border:1px solid var(--line);border-radius:8px;padding:9px 12px;font-family:ui-monospace,monospace}
  @media(max-width:900px){.app{grid-template-columns:1fr}.side{position:static;height:auto}
    .kpis{grid-template-columns:repeat(2,1fr)}.compare{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="app">
  <!-- ================= SIDEBAR ================= -->
  <aside class="side">
    <div class="brand">
      <div class="logo">🚛</div>
      <div><h1>LoadIQ</h1><small>Most Profitable Load Agent</small></div>
    </div>

    <div class="grp">
      <label>Ask in plain English</label>
      <input id="q" placeholder="I'm in Dallas, TX with a dry van…"/>
    </div>
    <div class="grp">
      <label>Current location</label>
      <input id="loc" value="Dallas, TX"/>
    </div>
    <div class="grp">
      <label>Equipment</label>
      <select id="eq"><option>Dry Van</option><option>Reefer</option><option>Flatbed</option><option>Step Deck</option></select>
    </div>
    <div class="grp">
      <label>Max pickup distance</label>
      <div class="range">
        <input id="rad" type="range" min="50" max="600" step="25" value="250" oninput="radlbl()"/>
        <b id="radv">250 mi</b>
      </div>
    </div>
    <div class="grp">
      <label>Accounting model</label>
      <div class="seg">
        <button id="m-fleet" class="on" onclick="setMode('fleet')">Fleet</button>
        <button id="m-owner" onclick="setMode('owner')">Owner-Op</button>
      </div>
    </div>
    <div class="grp">
      <div class="toggle">
        <span>Hide unprofitable loads</span>
        <div id="hsw" class="sw on" onclick="toggleHide()"><i></i></div>
      </div>
    </div>

    <button id="go" class="cta" onclick="run()">Find Best Load</button>

    <div class="foot">
      <b>● Deterministic</b> — every number is plain Python from the cost engine.
      The model never touches the math; it only parses your request and writes the summary.
    </div>
  </aside>

  <!-- ================= MAIN ================= -->
  <main class="main">
    <div class="topbar">
      <div class="tabs">
        <div class="tab active" data-tab="rec" onclick="switchTab('rec')">Load Board</div>
        <div class="tab" data-tab="mkt" onclick="switchTab('mkt')">Top 10 Markets</div>
      </div>
      <div class="modepill">Model: <b id="modepill">Fleet (driver pay = cost)</b></div>
    </div>

    <section id="rec">
      <div id="kpis" class="kpis"></div>
      <div id="rec-out"></div>
    </section>

    <section id="mkt" class="hide">
      <div class="card">
        <div class="sectitle">Top 10 US markets to start a carrier</div>
        <p class="mkformula" id="mkformula"></p>
        <div id="mkt-out" class="loading"><span class="spin"></span>Loading…</div>
      </div>
    </section>
  </main>
</div>

<script>
const $=id=>document.getElementById(id);
const money=n=>n==null?'—':'$'+Math.round(n).toLocaleString();
const money2=n=>n==null?'—':'$'+Number(n).toFixed(2);
const num=n=>n==null?'—':Math.round(n).toLocaleString();

const state={mode:'fleet', hide:true, data:null};

function radlbl(){ $('radv').textContent=$('rad').value+' mi'; }
function toggleHide(){ state.hide=!state.hide; $('hsw').classList.toggle('on',state.hide); }
function setMode(m){
  state.mode=m;
  $('m-fleet').classList.toggle('on',m==='fleet');
  $('m-owner').classList.toggle('on',m==='owner');
  $('modepill').textContent = m==='fleet'?'Fleet (driver pay = cost)':'Owner-Op (driver pay = take-home)';
  if(state.data) render(state.data);   // re-render same data, no refetch
}
// field accessors for the active accounting model
const F=()=>state.mode==='fleet'
  ? {net:'net_profit',npm:'net_per_mile',mar:'fleet_margin_pct',cost:'total_cost',driverIsCost:true}
  : {net:'owner_net_profit',npm:'owner_net_per_mile',mar:'owner_margin_pct',cost:'operating_cost',driverIsCost:false};

function switchTab(t){
  document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.dataset.tab===t));
  $('rec').classList.toggle('hide',t!=='rec');
  $('mkt').classList.toggle('hide',t!=='mkt');
  if(t==='mkt'&&!window._mkt) loadMarkets();
}

function kpis(st,loads){
  const f=F();
  const avgMar = loads.length? loads.reduce((a,l)=>a+l[f.mar],0)/loads.length : 0;
  const cards=[
    {v:num(st.total_candidates),l:'Loads scanned',c:'dim'},
    {v:num(st.in_radius),l:'Within '+st.radius+' mi',c:''},
    {v:num(st.profitable),l:'Profitable',c:'good'},
    {v:num(st.hidden_unprofitable),l:'Hidden (loss-making)',c:'warn'},
    {v:avgMar.toFixed(0)+'%',l:'Avg margin (top '+loads.length+')',c:avgMar>=0?'good':'warn'},
  ];
  $('kpis').innerHTML=cards.map(k=>`<div class="kpi ${k.c}"><div class="v">${k.v}</div><div class="l">${k.l}</div></div>`).join('');
}

function waterfall(L){
  const f=F();
  const g=L.gross_revenue;
  const lines=[['Fuel','fuel',L.fuel_cost],['Maintenance','maint',L.maintenance],
    ['Insurance','ins',L.insurance],['Tolls','toll',L.tolls]];
  if(f.driverIsCost) lines.splice(1,0,['Driver pay','driver',L.driver_pay]);
  const maxc=Math.max(...lines.map(x=>x[2]),1);
  let rows=`<div class="row gross"><div class="n">Gross revenue</div>
    <div class="bar gross"><i style="width:100%"></i></div><div class="v">${money(g)}</div></div>`;
  rows+=lines.map(([n,c,v])=>`<div class="row"><div class="n">${n}</div>
    <div class="bar ${c}"><i style="width:${Math.round(v/maxc*100)}%"></i></div>
    <div class="v">−${money(v)}</div></div>`).join('');
  const cost=L[f.cost];
  rows+=`<div class="row tot"><div class="n">${f.driverIsCost?'Total cost':'Operating cost'}</div>
    <div class="bar cost"><i style="width:${Math.round(cost/g*100)}%"></i></div><div class="v">−${money(cost)}</div></div>`;
  return `<div class="wf">${rows}</div>`;
}

function milestrip(L){
  const t=L.total_miles||1;
  const seg=(c,v,lbl)=>v>0?`<div class="${c}" style="width:${v/t*100}%">${v/t>0.12?lbl:''}</div>`:'';
  return `<div class="milestrip">
    ${seg('ms-din',L.deadhead_in_miles,Math.round(L.deadhead_in_miles)+' dh-in')}
    ${seg('ms-load',L.loaded_miles,Math.round(L.loaded_miles)+' loaded')}
    ${seg('ms-dout',L.deadhead_out_miles,Math.round(L.deadhead_out_miles)+' dh-out')}
  </div>
  <div class="mslabel">Total ${num(L.total_miles)} mi · ${L.estimated_days} days · empty-out estimated from destination strength</div>`;
}

function pickCard(L,win){
  const f=F();
  const net=L[f.net], mar=L[f.mar];
  return `<div class="pick ${win?'win':''}">
    <span class="tag ${win?'win':'alt'}">${win?'★ #1 SMART PICK · best net':'HIGHEST GROSS · pays more'}</span>
    <div class="lane">${L.origin} → ${L.destination}</div>
    <div class="meta">
      <span class="chip">${L.id}</span><span class="chip">${L.equipment}</span>
      <span class="chip">${num(L.miles)} loaded mi</span>
      <span class="chip">rate ${money2(L.rate_per_mile)}/mi</span>
      <span class="chip">net ${money2(L[f.npm])}/mi</span>
      ${L.pickup_date?`<span class="chip">PU ${L.pickup_date}</span>`:''}
    </div>
    <div class="netwrap">
      <div class="net ${net>=0?'g':'r'}">${money(net)}</div>
      <div class="marginbadge ${mar>=0?'g':'r'}">${mar.toFixed(0)}% margin</div>
    </div>
    <div class="nlbl">${f.driverIsCost?'Net profit (after driver pay)':'Owner take-home (incl. your $/mi)'}</div>
    ${waterfall(L)}
    ${milestrip(L)}
    <div class="gauge">
      <div class="top"><span>Destination market strength</span><b>${L.destination_score.toFixed(2)} / 1.00</b></div>
      <div class="gbar"><i style="width:${Math.round(L.destination_score*100)}%"></i></div>
    </div>
  </div>`;
}

function loadsTable(rows){
  const f=F();
  const sorted=[...rows].sort((a,b)=>b[f.net]-a[f.net]);
  const body=sorted.map((L,i)=>`<tr>
    <td><span class="rk">#${i+1}</span></td><td>${L.id}</td>
    <td>${L.origin} → ${L.destination}</td>
    <td>${money(L.gross_revenue)}</td>
    <td class="${L[f.net]>=0?'pos':'neg'}">${money(L[f.net])}</td>
    <td>${money2(L[f.npm])}</td>
    <td>${L[f.mar].toFixed(0)}%</td>
    <td>${L.destination_score.toFixed(2)}<span class="sbar"><i style="width:${Math.round(L.destination_score*100)}%"></i></span></td>
  </tr>`).join('');
  return `<div class="card"><div class="sectitle">Ranked loads · sorted by ${state.mode==='fleet'?'net profit':'owner take-home'}</div>
    <table><thead><tr><th>#</th><th>Load</th><th>Lane</th><th>Gross</th>
    <th>${state.mode==='fleet'?'Net':'Take-home'}</th><th>$/mi</th><th>Margin</th><th>Dest score</th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

function render(d){
  const f=F();
  kpis(d.stats,d.top_loads);
  const best=d.best, alt=d.highest_gross;
  const two = alt && best && alt.id!==best.id;
  let html='';
  if(d.explanation) html+=`<div class="explain"><div class="who">⚡ AI summary (prose only — numbers are computed)</div>${d.explanation}</div>`;

  html+=`<div class="sectitle">Recommendation</div>`;
  if(two){
    const dNet=best[f.net]-alt[f.net], dGross=alt.gross_revenue-best.gross_revenue;
    html+=`<div class="compare">${pickCard(best,true)}${pickCard(alt,false)}</div>
      <div class="delta">The alternative pays <b style="color:var(--gold)">${money(dGross)} more</b> in gross,
      but our pick <b>earns ${money(dNet)} more</b> ${f.driverIsCost?'net':'take-home'} —
      it ends in a stronger market (${best.destination_score.toFixed(2)} vs ${alt.destination_score.toFixed(2)}),
      so the empty drive out is ~${Math.round(alt.expected_deadhead_out-best.expected_deadhead_out)} mi shorter.</div>`;
  } else {
    html+=`<div class="compare single">${pickCard(best,true)}</div>
      <div class="delta">This load tops the board on both gross and net for your filters.</div>`;
  }
  html+=loadsTable(d.top_loads);
  $('rec-out').innerHTML=html;
}

async function run(){
  $('go').disabled=true;
  $('kpis').innerHTML='';
  $('rec-out').innerHTML='<div class="loading"><span class="spin"></span>Scoring loads deterministically…</div>';
  try{
    const r=await fetch('/api/recommend',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:$('q').value,location:$('loc').value,equipment:$('eq').value,
        pickup_radius:Number($('rad').value),hide_unprofitable:state.hide})});
    const d=await r.json();
    state.data=d;
    if(!d.top_loads||!d.top_loads.length){
      $('rec-out').innerHTML='<div class="err">No profitable loads within '+$('rad').value+' mi of '+$('loc').value+
        '. Try widening the radius or turning off "hide unprofitable".</div>'; return;
    }
    render(d);
  }catch(e){ $('rec-out').innerHTML='<div class="err">Error: '+e+'</div>'; }
  finally{ $('go').disabled=false; }
}

async function loadMarkets(){
  try{
    const d=await(await fetch('/api/markets')).json();
    window._mkt=d; const w=d.weights;
    $('mkformula').textContent=
      `market_score = ${w.outbound_demand}·outbound_demand + ${w.lane_balance}·lane_balance + ${w.rate_level}·rate_level   (each min-max normalized 0–1)`;
    const mx=Math.max(...d.markets.map(m=>m.market_score));
    const body=d.markets.map((m,i)=>`<tr>
      <td><span class="rk">#${i+1}</span> ${m.market}</td>
      <td>${m.outbound_demand.toLocaleString()}</td>
      <td>${m.lane_balance.toFixed(2)}</td>
      <td>${money2(m.rate_level)}</td>
      <td>${m.market_score.toFixed(3)}<span class="sbar"><i style="width:${Math.round(m.market_score/mx*100)}%"></i></span></td>
    </tr>`).join('');
    $('mkt-out').innerHTML=`<table><thead><tr><th>Market</th><th>Outbound demand</th>
      <th>Lane balance</th><th>Avg $/mi</th><th>Score</th></tr></thead><tbody>${body}</tbody></table>`;
  }catch(e){ $('mkt-out').innerHTML='<div class="err">Error: '+e+'</div>'; }
}

window.addEventListener('load',run);
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
