"""
cost_engine.py

ALL profit/cost math is here. Pure deterministic Python.
No LLM ever touches numbers. Every return value is reproducible.

Returns rich breakdown dicts so the UI can show the full itemized stack.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
import math


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Pure Python haversine distance in statute miles."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class CostConfig:
    mpg: float = 6.5
    fuel_price_per_gallon: float = 4.00
    driver_pay_per_mile: float = 0.65
    maintenance_per_mile: float = 0.18
    insurance_per_day: float = 50.0
    avg_speed_mph: float = 50.0
    hours_per_day: float = 11.0
    toll_per_mile: float = 0.03
    base_deadhead_miles: float = 250.0


def get_config(overrides: Optional[Dict[str, float]] = None) -> CostConfig:
    """Builds a CostConfig. Uses defaults unless overridden."""
    cfg = CostConfig()
    if overrides:
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, float(v))
    return cfg


def estimate_days(total_miles: float, cfg: CostConfig) -> float:
    """Estimate calendar days for the trip."""
    if total_miles <= 0:
        return 0.0
    miles_per_day = cfg.avg_speed_mph * cfg.hours_per_day
    days = total_miles / miles_per_day
    return max(0.25, days)  # small minimum for very short hauls


def calculate_costs(
    deadhead_in_miles: float,
    loaded_miles: float,
    deadhead_out_miles: float,
    gross_revenue: float,
    cfg: CostConfig,
) -> Dict[str, Any]:
    """
    Core deterministic cost engine.

    Returns a full breakdown dict. All numbers are calculated here.
    """
    deadhead_in = max(0.0, float(deadhead_in_miles or 0))
    loaded = max(0.0, float(loaded_miles or 0))
    deadhead_out = max(0.0, float(deadhead_out_miles or 0))

    total_miles = deadhead_in + loaded + deadhead_out

    days = estimate_days(total_miles, cfg)

    fuel_cost = (total_miles / cfg.mpg) * cfg.fuel_price_per_gallon
    driver_pay = total_miles * cfg.driver_pay_per_mile
    maintenance = total_miles * cfg.maintenance_per_mile
    insurance = days * cfg.insurance_per_day
    tolls = total_miles * cfg.toll_per_mile

    # FLEET model (spec): driver pay is a cost the carrier pays a hired driver.
    total_cost = fuel_cost + driver_pay + maintenance + insurance + tolls
    net_profit = gross_revenue - total_cost
    net_per_mile = (net_profit / total_miles) if total_miles > 0 else 0.0

    # OWNER-OPERATOR model: the driver IS the owner, so driver pay is take-home
    # earnings, not a cost. Operating cost excludes it; take-home = profit + wage.
    operating_cost = fuel_cost + maintenance + insurance + tolls  # = total_cost - driver_pay
    owner_net_profit = gross_revenue - operating_cost             # = net_profit + driver_pay
    owner_net_per_mile = (owner_net_profit / total_miles) if total_miles > 0 else 0.0

    gr = gross_revenue if gross_revenue else 0.0
    fleet_margin_pct = (net_profit / gr * 100) if gr else 0.0
    owner_margin_pct = (owner_net_profit / gr * 100) if gr else 0.0

    breakdown = {
        "deadhead_in_miles": round(deadhead_in, 1),
        "loaded_miles": round(loaded, 1),
        "deadhead_out_miles": round(deadhead_out, 1),
        "total_miles": round(total_miles, 1),
        "estimated_days": round(days, 2),
        "fuel_cost": round(fuel_cost, 2),
        "driver_pay": round(driver_pay, 2),
        "maintenance": round(maintenance, 2),
        "insurance": round(insurance, 2),
        "tolls": round(tolls, 2),
        "total_cost": round(total_cost, 2),
        "operating_cost": round(operating_cost, 2),
        "gross_revenue": round(gross_revenue, 2),
        # Fleet (default / spec)
        "net_profit": round(net_profit, 2),
        "net_per_mile": round(net_per_mile, 3),
        "fleet_margin_pct": round(fleet_margin_pct, 1),
        # Owner-operator
        "owner_net_profit": round(owner_net_profit, 2),
        "owner_net_per_mile": round(owner_net_per_mile, 3),
        "owner_margin_pct": round(owner_margin_pct, 1),
    }
    return breakdown


def format_breakdown_table(breakdown: Dict[str, Any]) -> str:
    """Human friendly text table for CLI/debug (UI will use columns)."""
    lines = []
    lines.append(f"Gross Revenue:     ${breakdown['gross_revenue']:,.2f}")
    lines.append("--- Costs ---")
    lines.append(f"  Fuel:            ${breakdown['fuel_cost']:,.2f}")
    lines.append(f"  Driver Pay:      ${breakdown['driver_pay']:,.2f}")
    lines.append(f"  Maintenance:     ${breakdown['maintenance']:,.2f}")
    lines.append(f"  Insurance:       ${breakdown['insurance']:,.2f}")
    lines.append(f"  Tolls:           ${breakdown['tolls']:,.2f}")
    lines.append("--- Totals ---")
    lines.append(f"Total Cost:        ${breakdown['total_cost']:,.2f}")
    lines.append(f"NET PROFIT:        ${breakdown['net_profit']:,.2f}")
    lines.append(f"Net per mile:      ${breakdown['net_per_mile']:.3f}")
    lines.append(
        f"Miles (in+loaded+out): {breakdown['deadhead_in_miles']:.0f} + "
        f"{breakdown['loaded_miles']:.0f} + {breakdown['deadhead_out_miles']:.0f} = "
        f"{breakdown['total_miles']:.0f}"
    )
    return "\n".join(lines)
