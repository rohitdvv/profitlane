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
    const pill = document.querySelector('.hero .pill');
    if (pill) pill.innerHTML = `<span class="dot">Live demo</span> ${m.cities.length} cities, precomputed by the real engine`;
  });
})();
