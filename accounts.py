"""
accounts.py

Local SQLite store for the business side: accounts + plans, daily search
metering, the "money found" ledger, leads, and partner-click attribution.
Plus apply_plan(), the pure function that gates a recommendation by plan.

Identity is deliberately light (email -> account, secret token in a cookie).
Each account has a public `id` (safe to hand to Stripe as client_reference_id)
and a secret `token` (the session). Never expose the token outside the cookie.
"""

import re
import secrets
import sqlite3
import time
import uuid
from datetime import date
from typing import Any, Dict, Optional, Tuple

import config
from config import PLANS

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
  plan TEXT NOT NULL DEFAULT 'free', api_key TEXT UNIQUE,
  stripe_customer TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
  subject TEXT NOT NULL, day TEXT NOT NULL, searches INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (subject, day)
);
CREATE TABLE IF NOT EXISTS wins (
  account_id TEXT NOT NULL, load_id TEXT NOT NULL, extra REAL NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (account_id, load_id)
);
CREATE TABLE IF NOT EXISTS leads (
  email TEXT NOT NULL, plan TEXT, source TEXT, created_at REAL NOT NULL,
  PRIMARY KEY (email, plan)
);
CREATE TABLE IF NOT EXISTS clicks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, partner TEXT NOT NULL,
  load_id TEXT, created_at REAL NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    # Read config.DB_PATH at call time so tests can point it at a temp file.
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db() as c:
        c.executescript(SCHEMA)


def _today() -> str:
    return date.today().isoformat()


def normalize_email(email: str) -> Optional[str]:
    e = (email or "").strip().lower()
    return e if EMAIL_RE.match(e) and len(e) <= 254 else None


# ------------------------------------------------------------------
# Accounts
# ------------------------------------------------------------------
def create_account(email: str) -> Optional[Dict[str, Any]]:
    """New free account. None if the email is invalid or already registered."""
    e = normalize_email(email)
    if not e:
        return None
    row = {"id": uuid.uuid4().hex, "token": secrets.token_urlsafe(32), "email": e,
           "plan": "free", "created_at": time.time()}
    try:
        with _db() as c:
            c.execute("INSERT INTO accounts (id, token, email, plan, created_at) VALUES (?,?,?,?,?)",
                      (row["id"], row["token"], e, "free", row["created_at"]))
    except sqlite3.IntegrityError:
        return None
    return row


def _one(sql: str, args: tuple) -> Optional[Dict[str, Any]]:
    with _db() as c:
        r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def get_by_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    return _one("SELECT * FROM accounts WHERE token = ?", (token,)) if token else None


def get_by_api_key(key: Optional[str]) -> Optional[Dict[str, Any]]:
    return _one("SELECT * FROM accounts WHERE api_key = ?", (key,)) if key else None


def get_by_email(email: str) -> Optional[Dict[str, Any]]:
    e = normalize_email(email)
    return _one("SELECT * FROM accounts WHERE email = ?", (e,)) if e else None


def set_plan(account_id: str, plan: str, stripe_customer: Optional[str] = None) -> bool:
    """Change plan. API-capable plans get an API key on first upgrade."""
    if plan not in PLANS:
        return False
    with _db() as c:
        cur = c.execute(
            "UPDATE accounts SET plan = ?, stripe_customer = COALESCE(?, stripe_customer) WHERE id = ?",
            (plan, stripe_customer, account_id))
        if cur.rowcount and PLANS[plan]["api"]:
            c.execute("UPDATE accounts SET api_key = COALESCE(api_key, ?) WHERE id = ?",
                      ("pl_live_" + secrets.token_urlsafe(24), account_id))
        return cur.rowcount > 0


def downgrade_customer(stripe_customer: str) -> int:
    with _db() as c:
        return c.execute("UPDATE accounts SET plan = 'free' WHERE stripe_customer = ?",
                         (stripe_customer,)).rowcount


# ------------------------------------------------------------------
# Metering
# ------------------------------------------------------------------
def searches_today(subject: str) -> int:
    r = _one("SELECT searches FROM usage WHERE subject = ? AND day = ?", (subject, _today()))
    return r["searches"] if r else 0


def consume_search(subject: str, daily_limit: Optional[int]) -> Tuple[bool, Optional[int]]:
    """Count one search. Returns (allowed, remaining_after). remaining is None when unlimited."""
    day = _today()
    with _db() as c:
        r = c.execute("SELECT searches FROM usage WHERE subject = ? AND day = ?", (subject, day)).fetchone()
        used = r["searches"] if r else 0
        if daily_limit is not None and used >= daily_limit:
            return False, 0
        c.execute("INSERT INTO usage (subject, day, searches) VALUES (?,?,1) "
                  "ON CONFLICT(subject, day) DO UPDATE SET searches = searches + 1", (subject, day))
    return True, (None if daily_limit is None else daily_limit - used - 1)


# ------------------------------------------------------------------
# Ledger: extra net profit ProfitLane found vs. the highest-gross load
# ------------------------------------------------------------------
def record_win(account_id: str, load_id: str, extra: float) -> None:
    """Once per (account, load) so repeating a search does not inflate the total."""
    if extra <= 0:
        return
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO wins (account_id, load_id, extra, created_at) VALUES (?,?,?,?)",
                  (account_id, str(load_id), float(extra), time.time()))


def total_found(account_id: str) -> float:
    r = _one("SELECT COALESCE(SUM(extra), 0) AS s FROM wins WHERE account_id = ?", (account_id,))
    return round(r["s"], 2) if r else 0.0


# ------------------------------------------------------------------
# Leads + partner clicks
# ------------------------------------------------------------------
def add_lead(email: str, plan: Optional[str], source: str) -> bool:
    e = normalize_email(email)
    if not e:
        return False
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO leads (email, plan, source, created_at) VALUES (?,?,?,?)",
                  (e, plan if plan in PLANS else "none", source[:40], time.time()))
    return True


def record_click(subject: Optional[str], partner: str, load_id: Optional[str]) -> None:
    with _db() as c:
        c.execute("INSERT INTO clicks (subject, partner, load_id, created_at) VALUES (?,?,?,?)",
                  (subject, partner, (load_id or "")[:40], time.time()))


def metrics() -> Dict[str, Any]:
    """Operator dashboard numbers (admin only)."""
    with _db() as c:
        plans = {r["plan"]: r["n"] for r in c.execute("SELECT plan, COUNT(*) n FROM accounts GROUP BY plan")}
        day = c.execute("SELECT COALESCE(SUM(searches),0) s, COUNT(*) n FROM usage WHERE day = ?",
                        (_today(),)).fetchone()
        clicks = {r["partner"]: r["n"] for r in c.execute("SELECT partner, COUNT(*) n FROM clicks GROUP BY partner")}
        leads = {(r["plan"] or "none"): r["n"] for r in c.execute("SELECT plan, COUNT(*) n FROM leads GROUP BY plan")}
        found = c.execute("SELECT COALESCE(SUM(extra),0) s FROM wins").fetchone()["s"]
    fleet_min = PLANS["fleet"].get("min_trucks", 1)
    mrr = plans.get("pro", 0) * PLANS["pro"]["price"] + plans.get("fleet", 0) * PLANS["fleet"]["price"] * fleet_min
    return {
        "accounts_by_plan": plans,
        "paying_accounts": plans.get("pro", 0) + plans.get("fleet", 0),
        "mrr_estimate_usd": mrr,  # fleet counted at the minimum truck count
        "searches_today": day["s"], "active_searchers_today": day["n"],
        "leads_by_plan": leads, "partner_clicks": clicks,
        "money_found_for_users_usd": round(found, 2),
    }


# ------------------------------------------------------------------
# Plan gating (pure)
# ------------------------------------------------------------------
def apply_plan(result: Dict[str, Any], plan: str) -> Dict[str, Any]:
    """Trim a full recommendation to what `plan` may see. Server-side, so it can't be bypassed."""
    p = PLANS.get(plan, PLANS["free"])
    out = dict(result)
    loads = result.get("top_loads") or []
    rows = p["board_rows"]
    if rows is not None and len(loads) > rows:
        out["top_loads"] = loads[:rows]
        out["locked"] = {"count": len(loads) - rows,
                         "top_net": max(l["net_profit"] for l in loads[rows:])}
    else:
        out["locked"] = None

    chains = result.get("chains")
    if chains and not p["chains"]:
        best = chains["chains"][0] if chains["chains"] else None
        # Teaser: reveal THAT a better plan exists and what it's worth, not the loads.
        out["chains"] = {"locked": True, "count": chains["count"],
                         "best_net": best["net_profit"] if best else None,
                         "best_net_per_day": best["net_per_day"] if best else None,
                         "first_leg_differs": chains["first_leg_differs"]}
    return out
