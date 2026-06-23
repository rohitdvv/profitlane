"""
data_layer.py

- Loads and parses the 100k synthetic loads from the provided text files.
- Builds CITY_COORDS from any loads that include lat/lon.
- Precomputes market intelligence scores ONCE at startup (fast).
- Exposes clean DataFrame + market score dicts.

All numbers (scores) are deterministic.
"""

import os
import re
import glob
import pandas as pd
from typing import Dict, Tuple, Optional, List
from functools import lru_cache

from config import (
    DATA_DIR,
    FALLBACK_CITY_COORDS,
    MARKET_SCORE_WEIGHTS,
    normalize_market_name,
)


# ------------------------------------------------------------------
# Robust parser for the messy multi-format load text files
# ------------------------------------------------------------------

def _extract_pairs(text: str) -> List[Tuple[float, float]]:
    """Find all (lat, lon) pairs in a block."""
    return [
        (float(a), float(b))
        for a, b in re.findall(r"\(\s*([-+]?\d+\.?\d*)\s*,\s*([-+]?\d+\.?\d*)\s*\)", text)
    ]


def _parse_single_block(block: str, load_id: str) -> Dict:
    rec: Dict = {"id": load_id}

    # --- Origin ---
    m = re.search(r"Origin\.*:?\s*([^\n(]{3,50})", block, re.I)
    if m:
        rec["origin_raw"] = m.group(1).strip()
    if "origin_raw" not in rec:
        m = re.search(r"PU:\s*([^|\n]{3,50})", block)
        if m:
            rec["origin_raw"] = m.group(1).strip()
    if "origin_raw" not in rec:
        m = re.search(r"\|\s*([A-Za-z][^->]{2,40}?)\s*->", block)
        if m:
            rec["origin_raw"] = m.group(1).strip()

    # --- Destination ---
    m = re.search(r"Destination\.*:?\s*([^\n(]{3,50})", block, re.I)
    if m:
        rec["dest_raw"] = m.group(1).strip()
    if "dest_raw" not in rec:
        m = re.search(r"DEL:\s*([^|\n]{3,50})", block)
        if m:
            rec["dest_raw"] = m.group(1).strip()
    if "dest_raw" not in rec:
        m = re.search(r"->\s*([A-Za-z][^,\n]{2,40}(?:,[A-Z]{2})?)", block)
        if m:
            rec["dest_raw"] = m.group(1).strip()

    # Lat/lon (take first pair as origin, second as dest if present)
    pairs = _extract_pairs(block)
    if pairs:
        rec["origin_lat"] = pairs[0][0]
        rec["origin_lon"] = pairs[0][1]
        if len(pairs) > 1:
            rec["dest_lat"] = pairs[1][0]
            rec["dest_lon"] = pairs[1][1]

    # Equipment
    m = re.search(r"(?:Equipment\.*:?\s*|Equip\s+)(Dry Van|Reefer|Flatbed|Step Deck)", block, re.I)
    if m:
        rec["equipment"] = m.group(1)
    else:
        m = re.search(r"(Dry Van|Reefer|Flatbed|Step Deck)", block, re.I)
        if m:
            rec["equipment"] = m.group(1)

    # Miles
    m = re.search(r"Miles\.*:?\s*([\d,]{2,5})", block, re.I)
    if m:
        rec["miles"] = int(m.group(1).replace(",", ""))
    else:
        m = re.search(r"(\d{2,4})\s*mi", block, re.I)
        if m:
            rec["miles"] = int(m.group(1))

    # Revenue (gross)
    m = re.search(r"\$\s*([\d,]{2,6}(?:\.\d+)?)", block)
    if m:
        val = float(m.group(1).replace(",", ""))
        if val > 50:
            rec["revenue"] = val

    # RPM (if explicit)
    m = re.search(r"[\(\s@]\s*\$?([\d.]{1,4})\s*(?:/mi|per mile|rpm)", block, re.I)
    if m:
        rec["rpm"] = float(m.group(1))

    # Pickup date
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", block)
    if m:
        rec["pickup_date"] = m.group(1)

    # Normalize markets
    rec["origin_market"] = normalize_market_name(rec.get("origin_raw"))
    rec["dest_market"] = normalize_market_name(rec.get("dest_raw"))

    # Derive rpm if missing
    if "rpm" not in rec and rec.get("revenue") and rec.get("miles"):
        try:
            rec["rpm"] = round(rec["revenue"] / rec["miles"], 3)
        except Exception:
            pass

    return rec


def _parse_file(path: str) -> List[Dict]:
    loads = []
    with open(path, "r", errors="ignore") as f:
        txt = f.read()

    id_re = re.compile(r"L-(\d{7})")
    for m in id_re.finditer(txt):
        load_id = "L-" + m.group(1)
        pos = m.end()
        next_match = id_re.search(txt, pos)
        end = next_match.start() if next_match else min(len(txt), pos + 800)
        block = txt[pos:end]
        rec = _parse_single_block(block, load_id)
        loads.append(rec)
    return loads


@lru_cache(maxsize=1)
def load_all_loads() -> pd.DataFrame:
    """Parse every loads_part file. Result is cached."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        DATA_DIR,
        os.path.join(here, "data"),
        os.path.abspath(os.path.join(here, "..", "data")),
        os.path.abspath(os.path.join(here, "../../Buildathon/Buildathon - Hackathon - Load Agent - Datasets/text")),
        "/Users/rohitdusanapudi/Claude/Buildathon/Buildathon - Hackathon - Load Agent - Datasets/text",
    ]
    base = None
    files = []
    for cand in candidates:
        if cand and os.path.isdir(cand):
            found = sorted(glob.glob(os.path.join(cand, "loads_part_*.txt")))
            if found:
                base = cand
                files = found
                break

    if not files:
        raise FileNotFoundError(
            "Could not find loads_part_*.txt. Tried: " + " | ".join(str(c) for c in candidates) +
            "\nCreate a data/ folder inside the project and put the loads_part files there, "
            "or update DATA_DIR in config.py."
        )

    all_recs = []
    for fp in files:
        all_recs.extend(_parse_file(fp))

    df = pd.DataFrame(all_recs)

    # Clean types
    for col in ("miles", "revenue"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("origin_lat", "origin_lon", "dest_lat", "dest_lon"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Fill missing rpm safely
    mask = df["rpm"].isna() & df["revenue"].notna() & df["miles"].notna()
    df.loc[mask, "rpm"] = (df.loc[mask, "revenue"] / df.loc[mask, "miles"]).round(3)

    # Drop rows with no usable origin or revenue (very rare)
    df = df.dropna(subset=["dest_market", "miles", "revenue"])

    # Standardize equipment
    df["equipment"] = df["equipment"].fillna("Dry Van").str.strip().str.title()
    df.loc[~df["equipment"].isin(["Dry Van", "Reefer", "Flatbed", "Step Deck"]), "equipment"] = "Dry Van"

    # Origin market fallback
    df["origin_market"] = df["origin_market"].fillna(df["origin_raw"].apply(normalize_market_name))

    df = df.reset_index(drop=True)
    return df


# ------------------------------------------------------------------
# CITY_COORDS (learned from data + fallbacks)
# ------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_city_coords() -> Dict[str, Tuple[float, float]]:
    """Build market -> (lat, lon) from any load that provided coordinates."""
    df = load_all_loads()
    coords: Dict[str, Tuple[float, float]] = {}

    # From loads that have lat/lon
    for col_prefix, market_col in [("origin", "origin_market"), ("dest", "dest_market")]:
        latc = f"{col_prefix}_lat"
        lonc = f"{col_prefix}_lon"
        sub = df[[market_col, latc, lonc]].dropna(subset=[latc, lonc])
        for _, row in sub.iterrows():
            mkt = row[market_col]
            if mkt and mkt not in coords:
                coords[mkt] = (float(row[latc]), float(row[lonc]))

    # Merge fallbacks (override only if not present)
    for mkt, ll in FALLBACK_CITY_COORDS.items():
        if mkt not in coords:
            coords[mkt] = ll

    return coords


def resolve_location(location: str) -> Optional[Tuple[float, float]]:
    """Resolve a city string like 'Dallas, TX' or 'Dallas' to (lat, lon)."""
    if not location:
        return None
    mkt = normalize_market_name(location)
    coords = get_city_coords()
    if mkt in coords:
        return coords[mkt]
    # fuzzy: contains
    for k, v in coords.items():
        if mkt and k and mkt in k:
            return v
    # last try: direct fallback keys
    if location in FALLBACK_CITY_COORDS:
        return FALLBACK_CITY_COORDS[location]
    return None


# ------------------------------------------------------------------
# Market intelligence (precomputed once)
# ------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_market_scores() -> Dict[str, Dict[str, float]]:
    """
    Precompute for every market:
      - outbound_demand (count)
      - inbound_count
      - lane_balance = outbound / max(inbound, 1)
      - rate_level = mean rpm of outbound loads
      - market_score (0..1 normalized weighted)

    Weighting (documented in config):
        0.40 * outbound_demand + 0.35 * lane_balance + 0.25 * rate_level
    """
    df = load_all_loads()

    # Outbound stats - compute robust rpm ourselves (raw rpm sometimes noisy)
    df = df.copy()
    df["computed_rpm"] = (df["revenue"] / df["miles"].replace(0, 1)).clip(1.0, 6.0)
    out = (
        df.groupby("origin_market")
        .agg(
            outbound_demand=("id", "count"),
            rate_level=("computed_rpm", "median"),
        )
        .reset_index()
        .rename(columns={"origin_market": "market"})
    )

    # Inbound
    inc = df.groupby("dest_market").size().reset_index(name="inbound_count")
    inc = inc.rename(columns={"dest_market": "market"})

    markets = pd.merge(out, inc, on="market", how="outer").fillna(0)

    # lane_balance (higher = easier to get next load)
    markets["lane_balance"] = markets["outbound_demand"] / markets["inbound_count"].clip(lower=1)

    # Normalize each component to 0-1 (min-max)
    def norm(s: pd.Series) -> pd.Series:
        mn, mx = s.min(), s.max()
        if mx - mn < 1e-9:
            return pd.Series([0.5] * len(s), index=s.index)
        return (s - mn) / (mx - mn)

    markets["d_norm"] = norm(markets["outbound_demand"])
    markets["b_norm"] = norm(markets["lane_balance"])
    markets["r_norm"] = norm(markets["rate_level"])

    w = MARKET_SCORE_WEIGHTS
    markets["market_score"] = (
        w["outbound_demand"] * markets["d_norm"]
        + w["lane_balance"] * markets["b_norm"]
        + w["rate_level"] * markets["r_norm"]
    ).clip(0, 1)

    # Return as dict market -> details
    result = {}
    for _, row in markets.iterrows():
        m = row["market"]
        if not m:
            continue
        result[m] = {
            "outbound_demand": int(row["outbound_demand"]),
            "inbound_count": int(row["inbound_count"]),
            "lane_balance": round(float(row["lane_balance"]), 3),
            "rate_level": round(float(row["rate_level"]), 3),
            "market_score": round(float(row["market_score"]), 4),
        }
    return result


def get_top_markets(n: int = 10) -> pd.DataFrame:
    """Return top N markets sorted by market_score (for UI table)."""
    scores = get_market_scores()
    rows = []
    for m, s in scores.items():
        rows.append(
            {
                "market": m,
                "outbound_demand": s["outbound_demand"],
                "lane_balance": s["lane_balance"],
                "rate_level": s["rate_level"],
                "market_score": s["market_score"],
            }
        )
    df = pd.DataFrame(rows).sort_values("market_score", ascending=False).head(n).reset_index(drop=True)
    return df


def filter_loads(
    df: Optional[pd.DataFrame] = None,
    equipment: Optional[str] = None,
    origin_market: Optional[str] = None,
    max_rows: Optional[int] = None,
) -> pd.DataFrame:
    """Fast filter before scoring. Equipment is exact match (as required)."""
    if df is None:
        df = load_all_loads()

    out = df.copy()
    if equipment:
        out = out[out["equipment"].str.lower() == equipment.lower()]
    if origin_market:
        m = normalize_market_name(origin_market)
        out = out[out["origin_market"].str.contains(m.split(",")[0], case=False, na=False)]

    if max_rows:
        out = out.head(max_rows)
    return out


# Small helper to get a sample of loads for quick testing
def get_sample_loads(n: int = 5, equipment: str = "Dry Van") -> pd.DataFrame:
    df = filter_loads(equipment=equipment)
    return df.head(n)
