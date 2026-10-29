"""
billing.py

Stripe via Payment Links — the fastest path to revenue: no SDK, no checkout
code. Create a recurring Payment Link per plan in the Stripe dashboard, set
STRIPE_LINK_<PLAN>_URL / _ID, and point a webhook at /api/stripe/webhook.

  Upgrade  -> redirect to the Payment Link with client_reference_id=<account public id>
  Webhook  -> checkout.session.completed      : grant the plan bought (from the link id)
              customer.subscription.deleted   : downgrade to free

The plan is decided by WHICH link was paid (payment_link id), never by anything
the browser sent, so a user can't buy Pro and get Fleet.
"""

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import accounts
from config import STRIPE_LINKS


def checkout_url(plan: str, account_id: str) -> Optional[str]:
    link = (STRIPE_LINKS.get(plan) or {}).get("url")
    if not link:
        return None
    sep = "&" if "?" in link else "?"
    return f"{link}{sep}{urlencode({'client_reference_id': account_id})}"


def checkout_enabled() -> Dict[str, bool]:
    return {plan: bool(v.get("url")) for plan, v in STRIPE_LINKS.items()}


def verify_signature(payload: bytes, sig_header: Optional[str], secret: Optional[str],
                     tolerance: int = 300, now: Optional[float] = None) -> bool:
    """Stripe-Signature check: HMAC-SHA256 of '<t>.<payload>' must match a v1 entry; t must be fresh."""
    if not (sig_header and secret):
        return False
    parts: Dict[str, list] = {}
    for item in sig_header.split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs((now or time.time()) - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for v in parts.get("v1", []))


def plan_for_link(payment_link_id: Optional[str]) -> Optional[str]:
    for plan, v in STRIPE_LINKS.items():
        if payment_link_id and v.get("id") == payment_link_id:
            return plan
    return None


def handle_event(event: Dict[str, Any]) -> str:
    """Apply a verified Stripe event. Returns a short status string for logs/tests."""
    etype = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    if etype == "checkout.session.completed":
        account_id = obj.get("client_reference_id")
        plan = plan_for_link(obj.get("payment_link"))
        if not account_id or not plan:
            return "ignored: unknown account or payment link"
        ok = accounts.set_plan(account_id, plan, stripe_customer=obj.get("customer"))
        return f"granted {plan}" if ok else "ignored: no such account"
    if etype == "customer.subscription.deleted" and obj.get("customer"):
        n = accounts.downgrade_customer(obj["customer"])
        return f"downgraded {n}"
    return "ignored"


def parse_event(payload: bytes) -> Dict[str, Any]:
    return json.loads(payload.decode("utf-8"))
