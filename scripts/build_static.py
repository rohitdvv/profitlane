"""
scripts/build_static.py

Build the GitHub Pages demo into ./docs.

Runs the real deterministic engine (agent.get_recommendation -> forward_scorer
-> cost_engine -> chain_planner) for every demo city x equipment type and saves
each result as JSON. web/index.html + web/static-shim.js then serve those files
instead of calling the FastAPI server. Only computed results are published
(top loads per search), never the raw dataset.

Usage (needs the dataset, see README):
    LOADS_DATA_DIR=/path/to/text python scripts/build_static.py
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ANTHROPIC_API_KEY", None)  # deterministic summaries; no API calls in a build

from accounts import apply_plan  # noqa: E402
from agent import get_recommendation  # noqa: E402
from app import _clean  # noqa: E402
from config import FALLBACK_CITY_COORDS, MARKET_SCORE_WEIGHTS, PICKUP_RADIUS_MILES, PLANS  # noqa: E402
from data_layer import get_top_markets, resolve_location  # noqa: E402

OUT = ROOT / "docs"
EQUIPMENT = ["Dry Van", "Reefer", "Flatbed", "Step Deck"]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    top_markets = get_top_markets(10).to_dict(orient="records")
    cities = sorted({*FALLBACK_CITY_COORDS, *(m["market"] for m in top_markets)})
    cities = [c for c in cities if resolve_location(c)]

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data").mkdir(parents=True)

    kept = []
    for city in cities:
        any_loads = False
        for eq in EQUIPMENT:
            res = get_recommendation(query="", force_location=city, force_equipment=eq,
                                     pickup_radius=PICKUP_RADIUS_MILES, hide_unprofitable=True)
            res = apply_plan(res, "pro")  # the demo shows the full Pro view
            res["offers"] = {}
            any_loads = any_loads or bool(res["top_loads"])
            (OUT / "data" / f"{slug(city)}__{slug(eq)}.json").write_text(
                json.dumps(_clean(res), separators=(",", ":")))
        if any_loads:
            kept.append(city)
        print(f"  {city}: {'ok' if any_loads else 'no loads, dropped'}")

    (OUT / "data" / "manifest.json").write_text(json.dumps({"cities": kept, "radius": PICKUP_RADIUS_MILES}))
    (OUT / "data" / "markets.json").write_text(json.dumps(_clean({"markets": top_markets, "weights": MARKET_SCORE_WEIGHTS})))
    (OUT / "data" / "plans.json").write_text(json.dumps({"plans": PLANS}))

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<script src="static-shim.js"></script>\n</head>', 1)
    (OUT / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(ROOT / "web" / "static-shim.js", OUT / "static-shim.js")
    (OUT / ".nojekyll").write_text("")
    print(f"Built {OUT} with {len(kept)} cities x {len(EQUIPMENT)} equipment types.")


if __name__ == "__main__":
    main()
