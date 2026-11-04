/*
 * check-core.js: the load-check math and offer reader for the static demo, which has no
 * server. load_check.py is the source of truth; tests/test_load_check.py runs both on the
 * same inputs and fails if they disagree.
 */
(function (root) {
  const r2 = n => Math.round(n * 100) / 100;
  const CITY = String.raw`((?:[A-Z][A-Za-z.'-]*\s+){0,3}[A-Z][A-Za-z.'-]*),?\s+([A-Z]{2})\b`;
  const LANE = new RegExp(CITY + String.raw`\s*(?:to|->|→|–|—|-|>)\s*` + CITY);
  const LABELLED = /(pick\s*-?\s*up|origin|from|delivery|deliver|drop(?:\s*-?\s*off)?|destination|dest)\s*[:\-]\s*/gi;
  const RATE = /\$\s*(\d{1,3}(?:\.\d{1,3})?)\s*(?:\/|per\s*)(?:mi\b|mile|loaded)/i;
  const RATE_ALL = new RegExp(RATE.source, 'gi');
  const MONEY = /\$\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)/g;
  const MILES = /(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:loaded\s*)?(?:mi\b|mi\.|miles?\b)/i;
  const MC = /\bMC\s*[#:\-]?\s*(\d{4,8})\b/i;
  const EQUIPMENT = [['reefer', 'Reefer'], ['refrigerated', 'Reefer'], ['flatbed', 'Flatbed'],
    ['step deck', 'Step Deck'], ['stepdeck', 'Step Deck'], ['dry van', 'Dry Van'], ['van', 'Dry Van']];
  const num = s => parseFloat(s.replace(/,/g, ''));

  function parseBasic(text) {
    const f = { origin: null, destination: null, loaded_miles: null, pay: null, rate_per_mile: null,
      equipment: null, pickup_date: null, broker: null, broker_mc: null };
    text = text || '';
    const lane = text.match(LANE);
    if (lane) {
      f.origin = `${lane[1].trim()}, ${lane[2]}`;
      f.destination = `${lane[3].trim()}, ${lane[4]}`;
    } else {
      for (const m of text.matchAll(LABELLED)) {
        const city = text.slice(m.index + m[0].length).match(new RegExp('^' + CITY));
        if (!city) continue;
        const key = 'pof'.includes(m[1][0].toLowerCase()) ? 'origin' : 'destination';
        f[key] = f[key] || `${city[1].trim()}, ${city[2]}`;
      }
    }
    const rate = text.match(RATE);
    if (rate) f.rate_per_mile = parseFloat(rate[1]);
    const rest = text.replace(RATE_ALL, ' ');
    const amounts = [...rest.matchAll(MONEY)].map(m => num(m[1]));
    if (amounts.length) f.pay = Math.max(...amounts);
    const miles = rest.match(MILES);
    if (miles) f.loaded_miles = num(miles[1]);
    const mc = text.match(MC);
    if (mc) f.broker_mc = mc[1];
    const lower = text.toLowerCase();
    for (const [word, name] of EQUIPMENT) {
      if (new RegExp(`\\b${word}\\b`).test(lower)) { f.equipment = name; break; }
    }
    return f;
  }

  function haversine(a, b) {
    const R = 3958.8, rad = d => d * Math.PI / 180;
    const dlat = rad(b[0] - a[0]), dlon = rad(b[1] - a[1]);
    const h = Math.sin(dlat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dlon / 2) ** 2;
    return 2 * R * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
  }

  function priceLoad(gross, loaded, emptyIn, emptyOut, c, target) {
    const M = emptyIn + loaded + emptyOut;
    const days = Math.max(0.25, M / (c.avg_speed_mph * c.hours_per_day));
    const fuel = M / c.mpg * c.fuel_price_per_gallon, driver = M * c.driver_pay_per_mile;
    const maint = M * c.maintenance_per_mile, ins = days * c.insurance_per_day, tolls = M * c.toll_per_mile;
    const cost = fuel + driver + maint + ins + tolls, opCost = cost - driver;
    const net = gross - cost, own = gross - opCost;
    const totalCost = r2(cost), miles = Math.round(M * 10) / 10;
    const targetGross = totalCost + target * miles;
    const verdict = gross >= targetGross ? 'take' : (r2(net) >= 0 ? 'counter' : 'pass');
    const counter = verdict === 'take' ? null : Math.ceil(targetGross / 25) * 25;
    return {
      deadhead_in_miles: Math.round(emptyIn * 10) / 10, loaded_miles: Math.round(loaded * 10) / 10,
      deadhead_out_miles: Math.round(emptyOut * 10) / 10, total_miles: miles,
      estimated_days: r2(days), fuel_cost: r2(fuel), driver_pay: r2(driver), maintenance: r2(maint),
      insurance: r2(ins), tolls: r2(tolls), total_cost: totalCost, operating_cost: r2(opCost),
      gross_revenue: r2(gross), net_profit: r2(net), net_per_mile: Math.round(net / M * 1000) / 1000,
      fleet_margin_pct: Math.round(net / gross * 1000) / 10, owner_net_profit: r2(own),
      owner_net_per_mile: Math.round(own / M * 1000) / 1000, owner_margin_pct: Math.round(own / gross * 1000) / 10,
      verdict, target_gross: r2(targetGross), counter_price: counter,
      counter_per_mile: counter ? r2(counter / loaded) : null, rate_per_mile: r2(gross / loaded),
      break_even_per_loaded_mile: r2(totalCost / loaded),
    };
  }

  root.PLCheck = { parseBasic, priceLoad, haversine };
})(typeof globalThis !== 'undefined' ? globalThis : this);
