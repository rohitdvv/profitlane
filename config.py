"""
config.py
All editable constants in one place.
Money math is 100% deterministic here - never in the LLM.
"""

# === MODEL (pinned exactly as specified) ===
MODEL = "claude-sonnet-4-6"

# === Cost engine defaults (from spec) ===
# Override by editing here or (future) via env/CLI.
DEFAULTS = {
    "mpg": 6.5,
    "fuel_price_per_gallon": 4.00,
    "driver_pay_per_mile": 0.65,
    "maintenance_per_mile": 0.18,
    "insurance_per_day": 50.0,
    "avg_speed_mph": 50.0,
    "hours_per_day": 11.0,
    "toll_per_mile": 0.03,
    "base_deadhead_miles": 250.0,   # used when destination market_score == 0
}

# === Data source (provided dataset, read-only) ===
# The loader looks here first, then falls back to ./data, ../data, etc.
# Point it at the folder holding the loads_part_*.txt files. Override with the
# LOADS_DATA_DIR env var, or just drop the files in a ./data/ folder.
import os as _os
DATA_DIR = _os.environ.get("LOADS_DATA_DIR", _os.path.join(_os.path.dirname(__file__), "data"))

# Fallback coords for popular driver starting cities (used if not in learned CITY_COORDS)
FALLBACK_CITY_COORDS = {
    "Dallas, TX": (32.7767, -96.7970),
    "Atlanta, GA": (33.7490, -84.3880),
    "Chicago, IL": (41.8781, -87.6298),
    "Houston, TX": (29.7604, -95.3698),
    "Los Angeles, CA": (34.0522, -118.2437),
    "Memphis, TN": (35.1495, -90.0490),
    "Indianapolis, IN": (39.7684, -86.1581),
    "Columbus, OH": (39.9612, -82.9988),
    "Fort Worth, TX": (32.7555, -97.3308),
    "Laredo, TX": (27.5306, -99.4803),
    "Nashville, TN": (36.1627, -86.7816),
    "El Paso, TX": (31.7619, -106.4850),
    "Kansas City, MO": (39.0997, -94.5786),
    "Louisville, KY": (38.2527, -85.7585),
    "Harrisburg, PA": (40.2737, -76.8844),
}

# === Market scoring weights (documented) ===
# market_score = 0.4 * norm(outbound_demand) + 0.35 * norm(lane_balance) + 0.25 * norm(rate_level)
MARKET_SCORE_WEIGHTS = {
    "outbound_demand": 0.40,
    "lane_balance": 0.35,
    "rate_level": 0.25,
}

# === UI / ranking defaults ===
TOP_N = 10
DEFAULT_EQUIPMENT = "Dry Van"

# Load-board behavior (deterministic, editable)
PICKUP_RADIUS_MILES = 250     # only recommend loads originating within this of the driver
HIDE_UNPROFITABLE = True      # by default hide loads whose net profit < MIN_NET_PROFIT
MIN_NET_PROFIT = 0.0          # threshold used when HIDE_UNPROFITABLE is on
RETURN_TOP_N = 25             # how many ranked loads to return to the UI (client re-sorts on toggle)

# Normalize market names consistently
def normalize_market_name(name: str) -> str:
    if not name:
        return None
    import re
    name = str(name).strip()
    name = re.sub(r'\s+\d{5}.*$', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # Handle "City,ST" or "City, ST" or "City ST"
    m = re.match(r'^([A-Za-z][A-Za-z .\'-]+?)[, ]*\s*([A-Z]{2})$', name, re.I)
    if m:
        city = m.group(1).strip().rstrip(',').title()
        st = m.group(2).upper()
        return f"{city}, {st}"
    # Fallback
    name = name.title()
    if "," not in name and len(name) > 3:
        name = name[:-3].rstrip() + ", " + name[-2:].upper()
    return name
