"""
config.py
All editable constants in one place.
Money math is 100% deterministic here - never in the LLM.
"""

import os as _os

# === BRAND ===
BRAND = {
    "name": "ProfitLane",
    "tagline": "See what you actually keep.",
    "promise": "The load board that ranks by net profit, not gross.",
}

# === MODEL ===
# Latest Sonnet by default; override with PROFITLANE_MODEL (e.g. the original
# hackathon pin "claude-sonnet-4-6"). The model only parses text and writes prose.
MODEL = _os.environ.get("PROFITLANE_MODEL", "claude-sonnet-5-5")

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

# === Profit Chain (two-load lookahead, deterministic) ===
# For the best CHAIN_FIRST_LEGS loads, find the best follow-on load out of each
# destination. The first leg's *estimated* empty-out is replaced by the *actual*
# drive to the second pickup. Second pickup must be on/after first delivery day.
CHAIN_FIRST_LEGS = 8
CHAIN_SHOW = 3

# === Plans (single source of truth for pricing + gating) ===
# Benchmarks (2026): DAT One carrier plans start at $59/mo; TruckSmarter $49/mo.
# None = unlimited.
PLANS = {
    "free": {
        "name": "Free", "price": 0, "per": "month",
        "daily_searches": 5, "board_rows": 3, "chains": False, "api": False,
        "features": ["#1 smart pick vs highest-gross", "Top 3 loads", "5 searches / day"],
    },
    "pro": {
        "name": "Pro", "price": 39, "per": "month",
        "daily_searches": None, "board_rows": None, "chains": True, "api": False,
        "features": ["Full ranked board", "Profit Chain: plan 2 loads ahead",
                     "Unlimited searches", "Owner-op + fleet math"],
    },
    "fleet": {
        "name": "Fleet", "price": 29, "per": "truck / month", "min_trucks": 3,
        "daily_searches": None, "board_rows": None, "chains": True, "api": True,
        "features": ["Everything in Pro", "REST API for your TMS / dispatch",
                     "Per-truck billing", "Priority support"],
    },
}

# === Persistence (local SQLite, no server) ===
DB_PATH = _os.environ.get("PROFITLANE_DB", _os.path.join(_os.path.dirname(__file__), "profitlane.db"))

# === Billing: Stripe Payment Links (no SDK; configure links in the Stripe dashboard) ===
# *_URL is where the Upgrade button sends the user; *_ID (plink_...) is how the
# webhook knows which plan was bought. Without these, Upgrade captures a lead instead.
STRIPE_LINKS = {
    "pro": {"url": _os.environ.get("STRIPE_LINK_PRO_URL"), "id": _os.environ.get("STRIPE_LINK_PRO_ID")},
    "fleet": {"url": _os.environ.get("STRIPE_LINK_FLEET_URL"), "id": _os.environ.get("STRIPE_LINK_FLEET_ID")},
}
STRIPE_WEBHOOK_SECRET = _os.environ.get("STRIPE_WEBHOOK_SECRET")
ADMIN_TOKEN = _os.environ.get("PROFITLANE_ADMIN_TOKEN")
COOKIE_SECURE = _os.environ.get("PROFITLANE_COOKIE_SECURE", "").lower() in ("1", "true", "yes")

# === Partner revenue (referral links; a CTA only appears when its URL is set) ===
PARTNERS = {
    "factoring": {
        "url": _os.environ.get("PARTNER_FACTORING_URL"),
        "label": "Get paid in 24 hours instead of 30-60 days",
    },
    "fuel": {
        "url": _os.environ.get("PARTNER_FUEL_URL"),
        "label": "Save on this load's fuel",
    },
}
# Assumed partner fuel discount used for the on-load savings estimate (editable).
FUEL_DISCOUNT_PER_GALLON = float(_os.environ.get("FUEL_DISCOUNT_PER_GALLON", "0.25"))

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
