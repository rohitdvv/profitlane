/*
 * static-shim.js — lets web/index.html run on static hosting (GitHub Pages).
 *
 * The full app talks to the FastAPI server. On a static host there is no server,
 * so this shim answers the page's /api/* calls from JSON files that
 * scripts/build_static.py precomputed with the real (deterministic) engine.
 * Only loaded by the static build; the live server never serves it.
 */
(function () {
  const realFetch = window.fetch.bind(window);
  const base = new URL('.', document.currentScript.src).href;
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  const slug = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const EQUIP = ['Dry Van', 'Reefer', 'Flatbed', 'Step Deck'];
  let manifest = null;
  const getManifest = async () => manifest || (manifest = await (await realFetch(base + 'data/manifest.json')).json());

  const ME = { signed_in: false, email: null, plan: 'demo', searches_left: null, money_found: 0,
               api_key: null, checkout: { pro: false, fleet: false } };
  const SERVER_ONLY = { error: 'demo', message: 'Accounts and payments run on the full ProfitLane server. This GitHub Pages demo shows the engine with precomputed searches.' };

  // Deterministic stand-in for the LLM parser: match a covered city + an equipment keyword.
  function parseQuery(q, cities) {
    const t = q.toLowerCase();
    const city = cities.find(c => t.includes(c.split(',')[0].toLowerCase())) || null;
    const eq = t.includes('reefer') ? 'Reefer' : t.includes('flatbed') ? 'Flatbed'
      : t.includes('step deck') || t.includes('stepdeck') ? 'Step Deck' : 'Dry Van';
    return { location: city, equipment: eq };
  }

  // Same steps as load_check.evaluate_load, on the data the build script exported.
  let checkData = null;
  const getCheck = async () => checkData || (checkData = await (await realFetch(base + 'data/check.json')).json());
  const locate = (d, name) => {
    if (!name) return null;
    const q = name.trim().toLowerCase().replace(/\s*,\s*/, ', ');
    const key = Object.keys(d.coords).find(k => k.toLowerCase() === q || k.split(',')[0].toLowerCase() === q);
    return key ? { key, at: d.coords[key] } : null;
  };

  async function checkEvaluate(b) {
    const d = await getCheck();
    const cfg = { ...d.defaults, ...(b.costs || {}) };
    const o = locate(d, b.origin), dest = locate(d, b.destination), t = locate(d, b.truck_location);
    const sources = {};
    let loaded = b.loaded_miles;
    if (loaded) sources.loaded = 'entered';
    else if (o && dest) { loaded = Math.round(PLCheck.haversine(o.at, dest.at) * d.road_factor * 10) / 10; sources.loaded = 'estimated'; }
    else return json({ error: 'need_miles', message: 'Enter the loaded miles. We could not estimate them from these cities.' }, 422);
    let emptyIn = b.empty_in_miles;
    if (emptyIn != null) sources.empty_in = 'entered';
    else if (t && o) { emptyIn = PLCheck.haversine(t.at, o.at); sources.empty_in = 'estimated'; }
    else { emptyIn = 0; sources.empty_in = 'unknown'; }
    const score = dest && d.scores[dest.key] != null ? d.scores[dest.key] : null;
    let emptyOut = b.empty_out_miles;
    if (emptyOut != null) sources.empty_out = 'entered';
    else { emptyOut = cfg.base_deadhead_miles * (1 - (score != null ? score : d.unknown_score)); sources.empty_out = 'estimated'; }
    const target = b.target_profit_per_mile == null ? d.target : b.target_profit_per_mile;
    const p = PLCheck.priceLoad(b.gross, loaded, emptyIn, emptyOut, cfg, target);
    let message = null;
    if (p.counter_price) {
      const lane = b.origin && b.destination ? `${b.origin} to ${b.destination}` : 'this';
      const fmt = n => Math.round(n).toLocaleString('en-US');
      message = `Hi, on your ${lane} load (${fmt(loaded)} mi at $${fmt(b.gross)}), I can run it for $${fmt(p.counter_price)} ($${(p.counter_price / loaded).toFixed(2)}/mi). Let me know if that works.`;
    }
    return json({ ...p, origin: b.origin, destination: b.destination, destination_score: score,
      target_profit_per_mile: target, sources, counter_message: message });
  }

  function checkRead(b) {
    if (b.upload_base64) return json({ error: 'ai_unavailable', message: 'Reading screenshots and PDFs runs on the full app. In this demo, paste the text or type the numbers.' }, 422);
    if (!(b.text || '').trim()) return json({ error: 'empty', message: 'Paste a load offer or attach a file.' }, 400);
    const f = PLCheck.parseBasic(b.text);
    const fromRate = f.pay == null && f.rate_per_mile && f.loaded_miles;
    if (fromRate) f.pay = Math.round(f.rate_per_mile * f.loaded_miles * 100) / 100;
    return json({ fields: f, check: [], pay_from_rate: !!fromRate, reader: 'basic' });
  }

  async function recommend(body) {
    const m = await getManifest();
    let loc = body.location, eq = body.equipment;
    if (body.query && !loc) { const p = parseQuery(body.query, m.cities); loc = p.location; eq = p.equipment; }
    eq = EQUIP.includes(eq) ? eq : 'Dry Van';
    const city = loc && m.cities.find(c => c.toLowerCase() === String(loc).trim().toLowerCase()
      || c.split(',')[0].toLowerCase() === String(loc).trim().toLowerCase());
    if (!city) {
      return json({ top_loads: [], location_resolved: false, stats: { radius: m.radius },
                    parsed: { location: loc || body.query || '', equipment: eq }, me: ME });
    }
    const r = await realFetch(`${base}data/${slug(city)}__${slug(eq)}.json`);
    const d = await r.json();
    d.me = ME;
    return json(d);
  }

  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    const path = new URL(url, location.href).pathname.replace(/^.*\/api\//, '/api/');
    if (!path.startsWith('/api/')) return realFetch(input, init);
    const body = init.body ? JSON.parse(init.body) : {};
    switch (path) {
      case '/api/me': return json(ME);
      case '/api/check': return checkEvaluate(body);
      case '/api/check/read': return checkRead(body);
      case '/api/recommend': return recommend(body);
      case '/api/plans': return realFetch(base + 'data/plans.json');
      case '/api/markets': return realFetch(base + 'data/markets.json');
      case '/api/locations': return json({ locations: (await getManifest()).cities });
      default: return json(SERVER_ONLY, 503);
    }
  };

  // Results are precomputed at one radius with money-losers hidden, so pin those controls.
  window.addEventListener('DOMContentLoaded', async () => {
    const m = await getManifest();
    const rad = document.getElementById('rad');
    if (rad) { rad.value = m.radius; rad.disabled = true; document.getElementById('radv').textContent = m.radius + ' mi'; }
    const hsw = document.getElementById('hsw');
    if (hsw) { hsw.disabled = true; hsw.title = 'Fixed in the demo'; }
    const pill = document.getElementById('check-pill');
    if (pill) pill.innerHTML = '<span class="dot">Demo</span> Reads pasted text by pattern. Screenshots and AI reading run on the full app';
  });
})();
