"""
agent.py

Thin Claude orchestration layer.
- LLM is used ONLY for:
  (a) parsing natural language into structured (location + equipment)
  (b) writing the final human-readable explanation

- Every single number shown comes from deterministic Python (cost_engine + forward_scorer).

MODEL comes from config (latest Sonnet; override with PROFITLANE_MODEL).
"""

import os
import json
from typing import Dict, Any, List, Optional
import anthropic

from config import MODEL, DEFAULT_EQUIPMENT, PICKUP_RADIUS_MILES, HIDE_UNPROFITABLE, RETURN_TOP_N
from data_layer import filter_loads, load_all_loads, get_top_markets
from forward_scorer import recommend_loads
from chain_planner import plan_chains

# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------

def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "For demo without LLM use the direct location/equipment controls in the UI."
        )
    return anthropic.Anthropic(api_key=api_key)


# ------------------------------------------------------------------
# Structured parsing of user query via tool use
# ------------------------------------------------------------------

PARSE_TOOL = {
    "name": "extract_load_request",
    "description": "Extract the driver's current location (city, state), equipment type, and any other constraints from the natural language query.",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City and state in 'City, ST' format, e.g. 'Dallas, TX' or 'Atlanta, GA'. Infer from context if possible.",
            },
            "equipment": {
                "type": "string",
                "enum": ["Dry Van", "Reefer", "Flatbed", "Step Deck"],
                "description": "Equipment type requested. Default to Dry Van if unclear.",
            },
            "notes": {
                "type": "string",
                "description": "Any other constraints mentioned (short).",
            },
        },
        "required": ["location", "equipment"],
    },
}


def parse_query(query: str) -> Dict[str, str]:
    """
    Use Claude to turn natural language into structured request.
    Falls back to simple heuristics if LLM unavailable.
    """
    default = {"location": "Dallas, TX", "equipment": DEFAULT_EQUIPMENT, "notes": ""}

    if not query or len(query.strip()) < 3:
        return default

    # Very lightweight local heuristic fallback (no API call)
    q = query.lower()
    loc = default["location"]
    eq = default["equipment"]

    for city in ["dallas", "atlanta", "chicago", "houston", "los angeles", "memphis", "laredo", "indianapolis", "fort worth", "nashville"]:
        if city in q:
            loc = city.title() + ", TX" if city in ["dallas", "houston", "fort worth"] else city.title() + ", GA" if city == "atlanta" else city.title() + ", IL" if city == "chicago" else city.title() + ", TN" if city in ["memphis", "nashville"] else city.title() + ", CA" if city in ["los angeles", "laredo"] else loc
            break

    for e in ["reefer", "flatbed", "step deck", "dry van"]:
        if e in q:
            eq = {"reefer": "Reefer", "flatbed": "Flatbed", "step deck": "Step Deck"}.get(e, "Dry Van")
            break

    # Try real LLM
    try:
        client = get_client()
        msg = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system="You extract structured trucking load query parameters. Use the tool. Be precise.",
            messages=[{"role": "user", "content": query}],
            tools=[PARSE_TOOL],
            tool_choice={"type": "tool", "name": "extract_load_request"},
        )
        for block in msg.content:
            if block.type == "tool_use" and block.name == "extract_load_request":
                inp = block.input or {}
                loc = inp.get("location") or loc
                eq = inp.get("equipment") or eq
                return {"location": loc, "equipment": eq, "notes": inp.get("notes", "")}
    except Exception as e:
        # Silent fallback to heuristic
        pass

    return {"location": loc, "equipment": eq, "notes": ""}


# ------------------------------------------------------------------
# Full agent flow (parse -> deterministic engines -> explanation)
# ------------------------------------------------------------------

def get_recommendation(
    query: str,
    force_location: Optional[str] = None,
    force_equipment: Optional[str] = None,
    pickup_radius: Optional[float] = None,
    hide_unprofitable: Optional[bool] = None,
    with_chains: bool = True,
) -> Dict[str, Any]:
    """
    Main entry point used by UI and the public API.

    The LLM only parses the query and writes prose. All ranking/filtering/cost
    math below is deterministic Python.
    """
    parsed = parse_query(query)
    if force_location:
        parsed["location"] = force_location
    if force_equipment:
        parsed["equipment"] = force_equipment

    location = parsed["location"]
    equipment = parsed["equipment"]

    radius = float(pickup_radius) if pickup_radius else PICKUP_RADIUS_MILES
    hide = HIDE_UNPROFITABLE if hide_unprofitable is None else bool(hide_unprofitable)

    # 1. Filter by equipment (exact) - as required by spec
    df = load_all_loads()
    candidates = filter_loads(df, equipment=equipment)
    if len(candidates) == 0:
        candidates = filter_loads(df, equipment=None)  # very rare fallback

    # 2. Deterministic ranking (radius + profitability filter, NO LLM math)
    rec = recommend_loads(
        candidates,
        location,
        pickup_radius=radius,
        hide_unprofitable=hide,
        top_n=RETURN_TOP_N,
    )
    top_loads = rec["loads"]
    stats = rec["stats"]

    # 3. Top markets (precomputed)
    top_markets = get_top_markets(10).to_dict(orient="records")

    # 4. Best (by net) vs highest gross across the whole profitable pool (key demo visual)
    best = rec.get("best") or (top_loads[0] if top_loads else None)
    highest_gross = rec.get("highest_gross") or best

    # 5. Profit Chain: best two-load sequences (deterministic lookahead)
    chains = plan_chains(top_loads, equipment, radius) if with_chains and top_loads else None

    # 6. LLM writes ONLY the explanation (grounded in the computed numbers)
    explanation = _generate_explanation(parsed, top_loads, top_markets, best, highest_gross, query)

    return {
        "chains": chains,
        "parsed": parsed,
        "location_resolved": rec.get("current_coords") is not None,
        "top_loads": top_loads,
        "stats": stats,
        "top_markets": top_markets,
        "explanation": explanation,
        "best": best,
        "highest_gross": highest_gross,
    }


def _generate_explanation(
    parsed: Dict,
    top_loads: List[Dict],
    top_markets: List[Dict],
    best: Optional[Dict],
    highest_gross: Optional[Dict],
    original_query: str,
) -> str:
    """Ask Claude to write nice prose. Pass only computed numbers."""
    if not top_loads:
        return "No suitable loads found for the given criteria."

    # Build a compact, fully numeric context for the model
    best_line = ""
    if best:
        best_line = (
            f"#1: {best['id']} {best['origin']} -> {best['destination']} | "
            f"Gross ${best['gross_revenue']:.0f} | Net ${best['net_profit']:.0f} | "
            f"Net/mi ${best['net_per_mile']:.2f} | Dest score {best['destination_score']:.2f} | "
            f"Expected deadhead out {best['expected_deadhead_out']:.0f} mi"
        )

    gross_line = ""
    if highest_gross and highest_gross != best:
        gross_line = (
            f"Highest gross alternative: {highest_gross['id']} {highest_gross['origin']} -> {highest_gross['destination']} | "
            f"Gross ${highest_gross['gross_revenue']:.0f} | Net ${highest_gross['net_profit']:.0f} | "
            f"Dest score {highest_gross['destination_score']:.2f}"
        )

    context = f"""User query: {original_query}
Parsed: location={parsed.get('location')}, equipment={parsed.get('equipment')}

Top recommendation (computed):
{best_line}

{gross_line}

Top 3 destination market scores (from data):
""" + "\n".join(
        f"- {m['market']}: outbound={m['outbound_demand']}, balance={m['lane_balance']}, rate={m['rate_level']}, score={m['market_score']:.2f}"
        for m in top_markets[:3]
    )

    prompt = f"""You are a helpful freight advisor.
Write a short, clear, confident explanation (4-7 sentences) for a truck driver.
Ground EVERY number and claim in the provided computed data.
Do NOT invent loads, rates, or math.
Highlight why the #1 pick has better net profit than the highest-gross option when relevant (the destination market effect).
Use plain English. Mention the market strength effect.

Data:
{context}
"""

    try:
        client = get_client()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=400,
            system="You write grounded explanations from exact numbers. Never make up data.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        return text.strip()
    except Exception as e:
        # Graceful fallback: pure deterministic summary
        if best:
            return (
                f"Based on current data, the best net-profit load for you starting near {parsed.get('location')} "
                f"with a {parsed.get('equipment')} is {best['id']} going {best['origin']} → {best['destination']}. "
                f"It pays ${best['gross_revenue']:.0f} gross but has an expected net of ${best['net_profit']:.0f} "
                f"(${best['net_per_mile']:.2f}/mi) after all costs including a realistic empty return of ~{best['expected_deadhead_out']:.0f} miles. "
                f"The destination market strength score is {best['destination_score']:.2f}. "
                "This accounts for where you will actually end up."
            )
        return "Unable to generate explanation (LLM unavailable). See the numeric results above."
