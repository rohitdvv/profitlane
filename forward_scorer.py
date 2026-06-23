"""
forward_scorer.py

The smart forward-looking part:
- Resolves the deadhead-IN (empty miles from the driver's current city to the load
  origin) using the precomputed market->coords table, NOT the per-row inline lat/lon
  (2 of every 3 records omit coords, so the inline path was unreliable / zeroed out).
- Uses destination market_score to predict a realistic deadhead-OUT.
- Feeds both into the deterministic cost engine.
- Filters to loads the driver can actually pick up (pickup radius) and, by default,
  to profitable loads only.
- Ranks by true NET profit (after future cost).

This is what creates the "aha" moment: a lower-gross load that ends in a strong market
can beat a higher-gross load that strands the truck in a dead market.

NOTE: every number here is plain deterministic Python. No LLM involvement.
"""

from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

from config import (
    DEFAULTS,
    PICKUP_RADIUS_MILES,
    HIDE_UNPROFITABLE,
    MIN_NET_PROFIT,
    RETURN_TOP_N,
)
from cost_engine import calculate_costs, get_config, haversine_miles
from data_layer import (
    get_market_scores,
    get_city_coords,
    resolve_location,
)


# ------------------------------------------------------------------
# Distance helpers (market-coords first, reliable for all 100k loads)
# ------------------------------------------------------------------
def _origin_coords(load_row: pd.Series, coords: Dict[str, Tuple[float, float]]):
    """Best available coords for a load's origin: market table first, inline fallback."""
    o_mkt = load_row.get("origin_market")
    if o_mkt and o_mkt in coords:
        return coords[o_mkt]
    olat, olon = load_row.get("origin_lat"), load_row.get("origin_lon")
    if pd.notna(olat) and pd.notna(olon):
        return (float(olat), float(olon))
    return None


def _deadhead_in_miles(
    current_coords: Optional[Tuple[float, float]],
    load_row: pd.Series,
    coords: Dict[str, Tuple[float, float]],
) -> Optional[float]:
    """Empty miles from driver's current city to the load origin. None if unknown."""
    if not current_coords:
        return None
    oc = _origin_coords(load_row, coords)
    if not oc:
        return None
    return haversine_miles(current_coords[0], current_coords[1], oc[0], oc[1])


# ------------------------------------------------------------------
# Score a single load (pure)
# ------------------------------------------------------------------
def score_load(
    load_row: pd.Series,
    current_coords: Optional[Tuple[float, float]],
    market_scores: Dict[str, Dict],
    coords: Optional[Dict[str, Tuple[float, float]]] = None,
    cfg=None,
) -> Dict[str, Any]:
    """Score ONE load with full forward-looking cost adjustment. Pure."""
    if cfg is None:
        cfg = get_config(DEFAULTS)
    if coords is None:
        coords = get_city_coords()

    dest_mkt = load_row.get("dest_market")
    ms = market_scores.get(dest_mkt, {})
    destination_score = ms.get("market_score", 0.35)  # neutral-ish default

    base_dead = DEFAULTS["base_deadhead_miles"]
    expected_deadhead_out = base_dead * (1.0 - destination_score)

    din = _deadhead_in_miles(current_coords, load_row, coords)
    dead_in = 120.0 if din is None else din  # last-resort average when wholly unknown

    loaded = float(load_row.get("miles", 0) or 0)
    gross = float(load_row.get("revenue", 0) or 0)

    breakdown = calculate_costs(
        deadhead_in_miles=dead_in,
        loaded_miles=loaded,
        deadhead_out_miles=expected_deadhead_out,
        gross_revenue=gross,
        cfg=cfg,
    )

    return {
        "id": load_row.get("id"),
        "origin": load_row.get("origin_market"),
        "destination": dest_mkt,
        "equipment": load_row.get("equipment"),
        "pickup_date": load_row.get("pickup_date"),
        "miles": loaded,
        "gross_revenue": gross,
        "rate_per_mile": round(gross / loaded, 2) if loaded else 0.0,
        "destination_score": round(destination_score, 4),
        "expected_deadhead_out": round(expected_deadhead_out, 1),
        "deadhead_in_known": din is not None,
        **breakdown,
    }


# ------------------------------------------------------------------
# Main recommendation flow with radius + profitability filtering + stats
# ------------------------------------------------------------------
def recommend_loads(
    candidates: pd.DataFrame,
    current_location: str,
    pickup_radius: float = PICKUP_RADIUS_MILES,
    hide_unprofitable: bool = HIDE_UNPROFITABLE,
    min_net_profit: float = MIN_NET_PROFIT,
    top_n: int = RETURN_TOP_N,
    sort_key: str = "net_profit",
) -> Dict[str, Any]:
    """
    Full board flow. Returns:
      {
        "loads": [scored dicts ...]  (top_n, ranked by sort_key desc),
        "stats": { total, in_radius, profitable, hidden_unprofitable, out_of_range, radius },
        "current_coords": (lat, lon) or None,
      }
    """
    market_scores = get_market_scores()
    coords = get_city_coords()
    current_coords = resolve_location(current_location)
    cfg = get_config(DEFAULTS)

    total = 0 if candidates is None else len(candidates)

    in_radius_df = candidates.iloc[0:0] if candidates is not None else None
    if candidates is not None and current_coords is not None and total:
        # Vectorized radius filter: deadhead is a function of the ORIGIN MARKET only
        # (the driver's current position is fixed), so compute it once per market.
        clat, clon = current_coords
        market_dh = {
            m: haversine_miles(clat, clon, c[0], c[1]) for m, c in coords.items()
        }
        din_series = candidates["origin_market"].map(market_dh)
        in_radius_df = candidates[din_series.notna() & (din_series <= pickup_radius)]
    in_radius = 0 if in_radius_df is None else len(in_radius_df)

    scored = []
    if in_radius:
        for _, row in in_radius_df.iterrows():
            try:
                scored.append(score_load(row, current_coords, market_scores, coords, cfg))
            except Exception:
                continue

    profitable = [s for s in scored if s.get("net_profit", -1) > 0]

    pool = scored
    if hide_unprofitable:
        pool = [s for s in scored if s.get("net_profit", -999999) >= min_net_profit]

    pool.sort(key=lambda x: x.get(sort_key, -999999), reverse=True)

    # For the side-by-side demo we contrast the best-NET pick against the flashiest
    # load by GROSS pay that still nets LESS than the pick -> the "pays more, earns
    # less" aha. Falls back to the plain top-gross load, then to None.
    top_by_net = pool[0] if pool else None
    top_by_gross = None
    if top_by_net:
        # Ideal contrast: pays MORE gross yet nets LESS than the pick.
        worse = [
            s for s in pool
            if s.get("net_profit", 0) < top_by_net["net_profit"]
            and s.get("gross_revenue", 0) > top_by_net["gross_revenue"]
        ]
        if worse:
            top_by_gross = max(worse, key=lambda x: x.get("gross_revenue", 0))

    stats = {
        "total_candidates": total,
        "in_radius": in_radius,
        "scored": len(scored),
        "profitable": len(profitable),
        "hidden_unprofitable": len(scored) - len(profitable) if hide_unprofitable else 0,
        "out_of_range": total - in_radius,
        "radius": pickup_radius,
        "showing": min(top_n, len(pool)),
    }

    return {
        "loads": pool[:top_n],
        "best": top_by_net,
        "highest_gross": top_by_gross,
        "stats": stats,
        "current_coords": list(current_coords) if current_coords else None,
    }


# ------------------------------------------------------------------
# Back-compat: simple ranked list (used by older callers / quick checks)
# ------------------------------------------------------------------
def rank_loads(
    candidates: pd.DataFrame,
    current_location: str,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    res = recommend_loads(candidates, current_location, top_n=top_n)
    return res["loads"]


def find_inversion_example(candidates: pd.DataFrame, current_location: str = "Atlanta, GA") -> Dict:
    """
    Finds a real pair demonstrating the inversion:
    lower-gross load ending in a strong market beats a higher-gross load ending in a weak one.
    """
    res = recommend_loads(candidates, current_location, hide_unprofitable=False, top_n=10_000)
    scored = res["loads"]
    if not scored:
        return {}

    by_gross = max(scored, key=lambda x: x["gross_revenue"])
    by_net = max(scored, key=lambda x: x["net_profit"])

    best_pair = None
    for a in scored:
        for b in scored:
            if a["gross_revenue"] > b["gross_revenue"] and b["net_profit"] > a["net_profit"]:
                if b.get("destination_score", 0) > a.get("destination_score", 0) + 0.15:
                    best_pair = {"high_gross_bad": a, "lower_gross_smart": b}
                    break
        if best_pair:
            break

    return {
        "top_by_net": by_net,
        "top_by_gross": by_gross,
        "inversion_pair": best_pair,
        "count_scored": len(scored),
    }
