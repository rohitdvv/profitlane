"""
app.py

ProfitLane — FastAPI + single-page dashboard (no Streamlit).

Driver / dispatcher facing tool:
  1. Natural-language query OR Location + Equipment + pickup-radius controls.
  2. Deterministic engine ranks loads the driver can actually pick up (within radius)
     by REAL net profit, pricing the expected empty (deadhead) miles out of the
     destination market.
  3. The #1 pick is shown big with a full itemized cost waterfall, side-by-side
     against the highest-GROSS load to make "pays more, earns less" obvious.
  4. Profit Chain plans two loads ahead (Pro), replacing the guessed empty-out.
  5. A Fleet / Owner-Operator toggle re-frames driver pay (cost vs take-home).
  6. Top-10 markets tab and a Pricing tab.

Business layer: Free / Pro / Fleet plans gated server-side (accounts.apply_plan),
daily search metering, a "money found" ledger, Stripe Payment Links (billing.py),
partner referral redirects, an admin metrics endpoint, and a REST API for Fleet.

CRITICAL DESIGN RULE: all money math is 100% pure Python (cost_engine + forward_scorer
+ chain_planner). The LLM (agent.py) only parses NL queries and writes prose.
Every figure on screen is reproducible.
"""

import hmac
import math
import secrets
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import accounts
import billing
from config import (
    ADMIN_TOKEN, BRAND, COOKIE_SECURE, DEFAULTS, FUEL_DISCOUNT_PER_GALLON,
    MARKET_SCORE_WEIGHTS, MODEL, PARTNERS, PICKUP_RADIUS_MILES, PLANS,
    STRIPE_WEBHOOK_SECRET,
)
from data_layer import load_all_loads, get_city_coords, get_market_scores, get_top_markets
from agent import get_recommendation

app = FastAPI(title="ProfitLane")

SESSION_COOKIE = "pl_session"
ANON_COOKIE = "pl_anon"


@app.on_event("startup")
def _warm() -> None:
    accounts.init_db()
    df = load_all_loads()
    ms = get_market_scores()
    print(f"[startup] {len(df):,} loads parsed, {len(ms)} markets scored. Ready.")


def _clean(obj: Any) -> Any:
    """pandas/numpy scalars -> plain python; NaN/Inf -> None (JSON-safe)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if hasattr(obj, "item"):
        try:
            return _clean(obj.item())
        except Exception:
            return obj
    return obj


class RecommendRequest(BaseModel):
    query: str = ""
    location: Optional[str] = None
    equipment: Optional[str] = None
    pickup_radius: Optional[float] = None
    hide_unprofitable: Optional[bool] = None


class EmailRequest(BaseModel):
    email: str
    plan: Optional[str] = None
    source: str = "web"


class GrantRequest(BaseModel):
    email: str
    plan: str


# ------------------------------------------------------------------
# Identity: signed-in account (session cookie) or anonymous visitor
# ------------------------------------------------------------------
def _identity(request: Request):
    """-> (account or None, metering subject, new anon id to set or None)."""
    acct = accounts.get_by_token(request.cookies.get(SESSION_COOKIE))
    if acct:
        return acct, "acct:" + acct["id"], None
    anon = request.cookies.get(ANON_COOKIE)
    new = None
    if not anon or len(anon) > 64:
        anon = new = secrets.token_urlsafe(16)
    return None, "anon:" + anon, new


def _set_cookie(resp, name: str, value: str) -> None:
    resp.set_cookie(name, value, max_age=400 * 86400, httponly=True,
                    samesite="lax", secure=COOKIE_SECURE)


def _offers(best: Optional[dict]) -> dict:
    """Partner CTAs for the #1 pick. Only partners with a configured link appear."""
    out = {}
    if not best:
        return out
    if PARTNERS["factoring"]["url"]:
        out["factoring"] = {"label": PARTNERS["factoring"]["label"]}
    if PARTNERS["fuel"]["url"]:
        gallons = best["total_miles"] / DEFAULTS["mpg"]
        out["fuel"] = {"label": PARTNERS["fuel"]["label"], "gallons": round(gallons),
                       "savings": round(gallons * FUEL_DISCOUNT_PER_GALLON, 2),
                       "per_gallon": FUEL_DISCOUNT_PER_GALLON}
    return out


def _me(acct: Optional[dict], subject: str) -> dict:
    plan = acct["plan"] if acct else "free"
    limit = PLANS[plan]["daily_searches"]
    return {
        "signed_in": bool(acct),
        "email": acct["email"] if acct else None,
        "plan": plan,
        "searches_left": None if limit is None else max(0, limit - accounts.searches_today(subject)),
        "money_found": accounts.total_found(acct["id"]) if acct else 0.0,
        "api_key": acct.get("api_key") if acct and PLANS[plan]["api"] else None,
        "checkout": billing.checkout_enabled(),
    }


@app.post("/api/recommend")
def api_recommend(req: RecommendRequest, request: Request) -> JSONResponse:
    acct, subject, new_anon = _identity(request)
    plan = acct["plan"] if acct else "free"

    allowed, remaining = accounts.consume_search(subject, PLANS[plan]["daily_searches"])
    if not allowed:
        resp = JSONResponse({"error": "daily_limit", "plan": plan,
                             "message": "You've used today's free searches."}, status_code=402)
    else:
        result = get_recommendation(
            query=req.query or "",
            force_location=req.location or None,
            force_equipment=req.equipment or None,
            pickup_radius=req.pickup_radius,
            hide_unprofitable=req.hide_unprofitable,
        )
        best, alt = result.get("best"), result.get("highest_gross")
        if acct and best and alt and alt.get("id") != best.get("id"):
            accounts.record_win(acct["id"], best["id"], best["net_profit"] - alt["net_profit"])
        result = accounts.apply_plan(result, plan)
        result["offers"] = _offers(best)
        result["me"] = _me(acct, subject)
        resp = JSONResponse(_clean(result))
    if new_anon:
        _set_cookie(resp, ANON_COOKIE, new_anon)
    return resp


# ------------------------------------------------------------------
# Accounts, leads, billing
# ------------------------------------------------------------------
@app.get("/api/me")
def api_me(request: Request) -> JSONResponse:
    acct, subject, new_anon = _identity(request)
    resp = JSONResponse(_clean(_me(acct, subject)))
    if new_anon:
        _set_cookie(resp, ANON_COOKIE, new_anon)
    return resp


@app.get("/api/plans")
def api_plans() -> JSONResponse:
    return JSONResponse({"plans": PLANS, "brand": BRAND})


@app.post("/api/signup")
def api_signup(req: EmailRequest) -> JSONResponse:
    acct = accounts.create_account(req.email)
    if not acct:
        if accounts.normalize_email(req.email) is None:
            return JSONResponse({"error": "invalid_email"}, status_code=400)
        # Don't hand an existing account to whoever types its email.
        return JSONResponse({"error": "exists",
                             "message": "That email already has an account. Use the device you signed up on, "
                                        "or contact support to restore access."}, status_code=409)
    accounts.add_lead(acct["email"], req.plan, "signup:" + req.source)
    resp = JSONResponse({"ok": True, "email": acct["email"]})
    _set_cookie(resp, SESSION_COOKIE, acct["token"])
    return resp


@app.post("/api/logout")
def api_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.post("/api/lead")
def api_lead(req: EmailRequest) -> JSONResponse:
    ok = accounts.add_lead(req.email, req.plan, req.source)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 400)


@app.get("/api/checkout/{plan}")
def api_checkout(plan: str, request: Request):
    acct, _, _ = _identity(request)
    if not acct:
        return JSONResponse({"error": "signin_required"}, status_code=401)
    url = billing.checkout_url(plan, acct["id"]) if plan in PLANS and plan != "free" else None
    if not url:
        return JSONResponse({"error": "checkout_unavailable"}, status_code=404)
    return RedirectResponse(url, status_code=303)


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request) -> JSONResponse:
    payload = await request.body()
    if not billing.verify_signature(payload, request.headers.get("stripe-signature"), STRIPE_WEBHOOK_SECRET):
        return JSONResponse({"error": "bad_signature"}, status_code=400)
    status = billing.handle_event(billing.parse_event(payload))
    print(f"[stripe] {status}")
    return JSONResponse({"received": True, "status": status})


@app.get("/go/{partner}")
def go_partner(partner: str, request: Request, load: Optional[str] = None):
    """Attributed redirect to a configured partner (fixed allowlist: no open redirect)."""
    p = PARTNERS.get(partner)
    if not p or not p["url"]:
        return JSONResponse({"error": "unknown_partner"}, status_code=404)
    _, subject, _ = _identity(request)
    accounts.record_click(subject, partner, load)
    return RedirectResponse(p["url"], status_code=302)


# ------------------------------------------------------------------
# Admin (disabled unless PROFITLANE_ADMIN_TOKEN is set)
# ------------------------------------------------------------------
def _is_admin(request: Request) -> bool:
    tok = request.headers.get("x-admin-token") or ""
    return bool(ADMIN_TOKEN) and hmac.compare_digest(tok, ADMIN_TOKEN)


@app.get("/admin/metrics")
def admin_metrics(request: Request) -> JSONResponse:
    if not _is_admin(request):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(accounts.metrics())


@app.post("/admin/grant")
def admin_grant(req: GrantRequest, request: Request) -> JSONResponse:
    """Manual upgrade for customers who paid by invoice/ACH before Stripe is wired."""
    if not _is_admin(request):
        return JSONResponse({"error": "not_found"}, status_code=404)
    acct = accounts.get_by_email(req.email)
    if not acct or not accounts.set_plan(acct["id"], req.plan):
        return JSONResponse({"error": "no_such_account_or_plan"}, status_code=400)
    return JSONResponse({"ok": True, "email": acct["email"], "plan": req.plan})


# ------------------------------------------------------------------
# Public REST API (Fleet plan): Authorization: Bearer <api_key>
# ------------------------------------------------------------------
@app.post("/v1/recommend")
def v1_recommend(req: RecommendRequest, request: Request) -> JSONResponse:
    auth = request.headers.get("authorization") or ""
    acct = accounts.get_by_api_key(auth[7:].strip() if auth.lower().startswith("bearer ") else None)
    if not acct or not PLANS[acct["plan"]]["api"]:
        return JSONResponse({"error": "invalid_api_key_or_plan"}, status_code=401)
    accounts.consume_search("api:" + acct["id"], None)
    result = get_recommendation(
        query=req.query or "",
        force_location=req.location or None,
        force_equipment=req.equipment or None,
        pickup_radius=req.pickup_radius,
        hide_unprofitable=req.hide_unprofitable,
    )
    return JSONResponse(_clean(accounts.apply_plan(result, acct["plan"])))


@app.get("/api/markets")
def api_markets() -> JSONResponse:
    rows = get_top_markets(10).to_dict(orient="records")
    return JSONResponse(_clean({"markets": rows, "weights": MARKET_SCORE_WEIGHTS}))


@app.get("/api/config")
def api_config() -> JSONResponse:
    return JSONResponse(_clean({
        "defaults": DEFAULTS, "model": MODEL, "pickup_radius": PICKUP_RADIUS_MILES,
    }))


@app.get("/api/locations")
def api_locations() -> JSONResponse:
    """Known markets for the location autocomplete."""
    return JSONResponse({"locations": sorted(m for m in get_city_coords() if m)})


INDEX_PATH = Path(__file__).parent / "web" / "index.html"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # Read per request so UI edits show up without a restart.
    return INDEX_PATH.read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
