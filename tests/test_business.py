"""
tests/test_business.py

The revenue paths: plan gating, metering, the money-found ledger, Stripe
signature verification + webhook grants, and Profit Chain math.
No dataset needed: everything here uses a temp DB and synthetic loads.
"""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import accounts
import billing
import config
from chain_planner import combine_legs
from cost_engine import calculate_costs, get_config


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    accounts.init_db()


def _sign(payload: bytes, secret: str, ts: int) -> str:
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


# ---------------- Stripe ----------------
def test_signature_valid_tampered_and_stale():
    body, secret, now = b'{"type":"x"}', "whsec_test", int(time.time())
    assert billing.verify_signature(body, _sign(body, secret, now), secret)
    assert not billing.verify_signature(body + b" ", _sign(body, secret, now), secret)
    assert not billing.verify_signature(body, _sign(body, "other", now), secret)
    assert not billing.verify_signature(body, _sign(body, secret, now - 3600), secret)
    assert not billing.verify_signature(body, None, secret)
    assert not billing.verify_signature(body, _sign(body, secret, now), None)


def test_webhook_grants_plan_by_link_not_by_client(monkeypatch):
    monkeypatch.setitem(billing.STRIPE_LINKS, "pro", {"url": "https://buy.stripe.com/x", "id": "plink_pro"})
    monkeypatch.setitem(billing.STRIPE_LINKS, "fleet", {"url": None, "id": "plink_fleet"})
    a = accounts.create_account("Driver@Example.com")
    ev = {"type": "checkout.session.completed",
          "data": {"object": {"client_reference_id": a["id"], "payment_link": "plink_pro", "customer": "cus_1"}}}
    assert billing.handle_event(ev) == "granted pro"
    assert accounts.get_by_token(a["token"])["plan"] == "pro"

    # Unknown link -> nothing granted
    ev["data"]["object"]["payment_link"] = "plink_unknown"
    assert billing.handle_event(ev).startswith("ignored")

    # Cancellation downgrades
    assert billing.handle_event({"type": "customer.subscription.deleted",
                                 "data": {"object": {"customer": "cus_1"}}}) == "downgraded 1"
    assert accounts.get_by_token(a["token"])["plan"] == "free"


def test_fleet_upgrade_issues_api_key():
    a = accounts.create_account("fleet@example.com")
    assert accounts.set_plan(a["id"], "fleet")
    acct = accounts.get_by_token(a["token"])
    assert acct["api_key"].startswith("pl_live_")
    assert accounts.get_by_api_key(acct["api_key"])["id"] == a["id"]


def test_checkout_url_uses_public_id_not_token(monkeypatch):
    monkeypatch.setitem(billing.STRIPE_LINKS, "pro", {"url": "https://buy.stripe.com/abc", "id": "plink_pro"})
    a = accounts.create_account("x@example.com")
    url = billing.checkout_url("pro", a["id"])
    assert url == f"https://buy.stripe.com/abc?client_reference_id={a['id']}"
    assert a["token"] not in url


# ---------------- Accounts ----------------
def test_duplicate_and_invalid_email():
    assert accounts.create_account("a@b.co")
    assert accounts.create_account("A@B.CO") is None
    assert accounts.create_account("not-an-email") is None


def test_free_metering_blocks_after_limit():
    limit = config.PLANS["free"]["daily_searches"]
    for i in range(limit):
        ok, left = accounts.consume_search("anon:x", limit)
        assert ok and left == limit - i - 1
    assert accounts.consume_search("anon:x", limit) == (False, 0)
    assert accounts.consume_search("anon:y", limit)[0]  # separate subject
    assert accounts.consume_search("acct:pro", None) == (True, None)


def test_money_found_counts_each_load_once():
    a = accounts.create_account("w@example.com")
    accounts.record_win(a["id"], "L1", 250.0)
    accounts.record_win(a["id"], "L1", 250.0)  # same search repeated
    accounts.record_win(a["id"], "L2", 100.0)
    accounts.record_win(a["id"], "L3", -50.0)  # never negative
    assert accounts.total_found(a["id"]) == 350.0


# ---------------- Gating ----------------
def _result(n=10):
    loads = [{"id": f"L{i}", "net_profit": 1000.0 - i * 10} for i in range(n)]
    chains = {"chains": [{"net_profit": 3000.0, "net_per_day": 900.0}], "count": 4, "first_leg_differs": True}
    return {"top_loads": loads, "chains": chains}


def test_free_plan_is_trimmed_server_side():
    out = accounts.apply_plan(_result(), "free")
    rows = config.PLANS["free"]["board_rows"]
    assert len(out["top_loads"]) == rows
    assert out["locked"] == {"count": 10 - rows, "top_net": 1000.0 - rows * 10}
    # Chain teaser shows the value, never the loads
    assert out["chains"] == {"locked": True, "count": 4, "best_net": 3000.0,
                             "best_net_per_day": 900.0, "first_leg_differs": True}


def test_pro_plan_sees_everything():
    r = _result()
    out = accounts.apply_plan(r, "pro")
    assert out["top_loads"] == r["top_loads"] and out["locked"] is None
    assert out["chains"] == r["chains"]


# ---------------- Profit Chain ----------------
def _scored(id_, dh_in, loaded, dh_out, gross, cfg):
    return {"id": id_, "origin": "A", "destination": "B", **calculate_costs(dh_in, loaded, dh_out, gross, cfg)}


def test_chain_replaces_guessed_empty_out_with_real_leg():
    cfg = get_config(config.DEFAULTS)
    leg1 = _scored("L1", 50, 500, 180, 1800, cfg)   # 180 = guessed empty-out
    leg2 = _scored("L2", 40, 600, 100, 2100, cfg)   # 40 = real drive to 2nd pickup
    c = combine_legs(leg1, leg2, cfg)

    leg1_no_out = calculate_costs(50, 500, 0, 1800, cfg)
    assert c["net_profit"] == round(leg1_no_out["net_profit"] + leg2["net_profit"], 2)
    assert c["net_profit"] > leg1["net_profit"] + leg2["net_profit"]  # guess removed
    assert c["empty_miles"] == 50 + 40 + 100
    assert c["gross_revenue"] == 3900
    assert c["net_per_day"] == round(c["net_profit"] / c["estimated_days"], 2)
