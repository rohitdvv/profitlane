"""
tests/test_cost_engine.py

Hand-checked unit tests for the deterministic cost engine.
Run with: python -m pytest tests/test_cost_engine.py -q --tb=short

The goal: prove that net_profit calculations are transparent and correct.
"""

import sys
from pathlib import Path

# Make imports work when running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from cost_engine import (
    haversine_miles,
    CostConfig,
    calculate_costs,
    get_config,
)


def test_haversine_atlanta_to_fort_worth():
    """Realistic distance check (Atlanta -> Fort Worth road ~780-820mi, haversine ~750)."""
    atl = (33.749, -84.388)
    ftw = (32.7555, -97.3308)
    dist = haversine_miles(*atl, *ftw)
    assert 740 < dist < 770, f"Unexpected haversine: {dist}"


def test_haversine_same_location_zero():
    assert haversine_miles(33.749, -84.388, 33.749, -84.388) == 0.0


def test_costs_hand_checked_example():
    """
    HAND-CHECKED EXAMPLE (all math shown below).

    Inputs:
        deadhead_in = 0 miles (driver already at origin)
        loaded_miles = 500
        deadhead_out = 150 (expected empty out)
        gross_revenue = 2000.0

    Constants (from spec defaults):
        mpg=6.5, fuel=$4/gal → fuel/mile = 4/6.5 ≈ 0.6153846
        driver_pay=0.65
        maint=0.18
        toll=0.03
        insurance=$50/day
        avg_speed=50, hours/day=11 → miles/day = 550

    Calculation:
        total_miles = 0 + 500 + 150 = 650
        days = 650 / 550 = 1.1818...

        fuel_cost   = 650 * (4 / 6.5)     = 650 * 0.6153846 ≈ 400.00
        driver_pay  = 650 * 0.65          = 422.50
        maintenance = 650 * 0.18          = 117.00
        insurance   = 1.1818... * 50      ≈ 59.09
        tolls       = 650 * 0.03          = 19.50

        total_cost = 400 + 422.5 + 117 + 59.09 + 19.5 = 1018.09
        net_profit = 2000 - 1018.09 = 981.91
        net_per_mile = 981.91 / 650 ≈ 1.511
    """
    cfg = get_config()  # defaults

    bd = calculate_costs(
        deadhead_in_miles=0.0,
        loaded_miles=500.0,
        deadhead_out_miles=150.0,
        gross_revenue=2000.0,
        cfg=cfg,
    )

    # Core invariants
    assert bd["total_miles"] == 650.0
    assert bd["gross_revenue"] == 2000.0
    assert bd["net_profit"] > 900   # obviously positive

    # Approximate checks (allow small floating tolerance from days calc)
    assert abs(bd["fuel_cost"] - 400.0) < 1.0
    assert abs(bd["driver_pay"] - 422.5) < 1.0
    assert abs(bd["maintenance"] - 117.0) < 1.0
    assert abs(bd["tolls"] - 19.5) < 0.5
    assert 55 < bd["insurance"] < 65

    # Net math
    expected_net = round(2000.0 - bd["total_cost"], 2)
    assert abs(bd["net_profit"] - expected_net) < 0.01

    # Net per mile positive and sensible
    assert bd["net_per_mile"] > 1.0
    assert bd["net_per_mile"] < 3.0

    # Full breakdown keys present
    required = {
        "deadhead_in_miles", "loaded_miles", "deadhead_out_miles",
        "total_miles", "fuel_cost", "driver_pay", "maintenance",
        "insurance", "tolls", "total_cost", "gross_revenue",
        "net_profit", "net_per_mile"
    }
    assert required.issubset(bd.keys())


def test_costs_negative_net():
    """Low revenue + high deadhead_out should be able to go negative."""
    cfg = get_config()
    bd = calculate_costs(
        deadhead_in_miles=120,
        loaded_miles=300,
        deadhead_out_miles=400,   # bad market
        gross_revenue=800,
        cfg=cfg,
    )
    assert bd["net_profit"] < 0
    assert bd["total_miles"] == 820


def test_costs_zero_miles_edge():
    bd = calculate_costs(0, 0, 0, 0, get_config())
    assert bd["total_miles"] == 0
    # With zero miles we incur almost no cost (insurance is 0 now)
    assert bd["net_profit"] == 0
    assert bd["total_cost"] == 0


if __name__ == "__main__":
    # Allow direct run: python tests/test_cost_engine.py
    import pytest
    sys.exit(pytest.main([__file__, "-q", "--tb=line"]))
