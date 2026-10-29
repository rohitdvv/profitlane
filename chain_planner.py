"""
chain_planner.py

Profit Chain — plan two loads ahead (deterministic, no LLM).

A single-load board has to *guess* the empty drive after delivery
(forward_scorer's expected_deadhead_out). A chain removes the guess:
  leg 1 is re-costed with ZERO estimated empty-out, and
  leg 2 is the best profitable load out of leg 1's destination, whose
  deadhead-in is the real drive from that destination to its pickup.

chain_net = leg1 net (no guessed empty-out) + leg2 net (incl. its own empty-out)

Feasibility: leg 2's pickup date must be on/after leg 1's delivery day
(leg 1 pickup date + whole days of driving).
"""

from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional

from config import DEFAULTS, CHAIN_FIRST_LEGS, CHAIN_SHOW
from cost_engine import calculate_costs, get_config
from data_layer import load_all_loads, filter_loads
from forward_scorer import recommend_loads


def _delivery_date(pickup: Optional[str], drive_days: float) -> Optional[str]:
    if not pickup:
        return None
    try:
        d = date.fromisoformat(str(pickup)[:10])
    except ValueError:
        return None
    return (d + timedelta(days=int(drive_days))).isoformat()


@lru_cache(maxsize=4096)
def _best_next_load(equipment: str, market: str, radius: float, earliest: Optional[str]) -> Optional[Dict]:
    """Best profitable load picking up near `market` on/after `earliest`. Cached: data is static."""
    cand = filter_loads(load_all_loads(), equipment=equipment)
    if earliest:
        cand = cand[cand["pickup_date"].fillna("") >= earliest]
    if len(cand) == 0:
        return None
    rec = recommend_loads(cand, market, pickup_radius=radius, hide_unprofitable=True, top_n=1)
    return rec["best"]


def combine_legs(leg1: Dict[str, Any], leg2: Dict[str, Any], cfg=None) -> Dict[str, Any]:
    """Pure: cost a two-load chain. leg1/leg2 are scored-load dicts from forward_scorer."""
    if cfg is None:
        cfg = get_config(DEFAULTS)
    first = calculate_costs(
        deadhead_in_miles=leg1["deadhead_in_miles"],
        loaded_miles=leg1["loaded_miles"],
        deadhead_out_miles=0.0,
        gross_revenue=leg1["gross_revenue"],
        cfg=cfg,
    )
    days = first["estimated_days"] + leg2["estimated_days"]
    net = first["net_profit"] + leg2["net_profit"]
    owner = first["owner_net_profit"] + leg2["owner_net_profit"]
    return {
        "leg1": {**leg1, "chain_net_profit": first["net_profit"],
                 "chain_owner_net_profit": first["owner_net_profit"]},
        "leg2": leg2,
        "gross_revenue": round(leg1["gross_revenue"] + leg2["gross_revenue"], 2),
        "net_profit": round(net, 2),
        "owner_net_profit": round(owner, 2),
        "total_miles": round(first["total_miles"] + leg2["total_miles"], 1),
        "empty_miles": round(first["deadhead_in_miles"] + leg2["deadhead_in_miles"]
                             + leg2["deadhead_out_miles"], 1),
        "estimated_days": round(days, 2),
        "net_per_day": round(net / days, 2) if days else 0.0,
        "owner_net_per_day": round(owner / days, 2) if days else 0.0,
    }


def plan_chains(
    first_legs: List[Dict[str, Any]],
    equipment: str,
    pickup_radius: float,
    first_n: int = CHAIN_FIRST_LEGS,
    show: int = CHAIN_SHOW,
) -> Dict[str, Any]:
    """Best two-load chains starting from the top `first_n` single-load picks, ranked by chain net."""
    cfg = get_config(DEFAULTS)
    chains = []
    for leg1 in first_legs[:first_n]:
        if not leg1.get("destination"):
            continue
        delivery_days = calculate_costs(
            leg1["deadhead_in_miles"], leg1["loaded_miles"], 0.0, leg1["gross_revenue"], cfg
        )["estimated_days"]
        earliest = _delivery_date(leg1.get("pickup_date"), delivery_days)
        leg2 = _best_next_load(equipment, leg1["destination"], float(pickup_radius), earliest)
        if leg2 and leg2["id"] != leg1["id"]:
            chains.append(combine_legs(leg1, leg2, cfg))

    chains.sort(key=lambda c: c["net_profit"], reverse=True)
    best = chains[0] if chains else None
    single_best = first_legs[0] if first_legs else None
    return {
        "chains": chains[:show],
        "count": len(chains),
        # The aha: the best opening move for the week is often NOT the #1 single load.
        "first_leg_differs": bool(best and single_best and best["leg1"]["id"] != single_best["id"]),
    }
