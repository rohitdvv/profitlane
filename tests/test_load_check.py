"""
tests/test_load_check.py

Paste any load: offer reading, the money math on top of it, the Claude call shape,
and parity with web/check-core.js (which the static demo runs).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import agent
import config
import load_check
from cost_engine import calculate_costs, get_config

CFG = get_config(config.DEFAULTS)

OFFERS = [
    "Dallas, TX to Atlanta, GA 781 mi $2,100 dry van MC# 123456",
    "REEFER load\nPickup: Memphis, TN\nDelivery: Chicago, IL\n530 miles\nRate: $1,450.00",
    "Fort Worth TX -> Boston MA, 1,938 loaded miles, $2.88/mi, flatbed",
    "Laredo, TX → Kansas City, MO step deck $3,200 (detention $50) 900 mi",
    "need a truck today, call me",
]


# ---------------- Reading ----------------
def test_parse_basic_reads_a_one_line_offer():
    f = load_check.parse_basic(OFFERS[0])
    assert (f["origin"], f["destination"]) == ("Dallas, TX", "Atlanta, GA")
    assert (f["loaded_miles"], f["pay"], f["equipment"], f["broker_mc"]) == (781.0, 2100.0, "Dry Van", "123456")


def test_parse_basic_reads_labelled_fields():
    f = load_check.parse_basic(OFFERS[1])
    assert (f["origin"], f["destination"]) == ("Memphis, TN", "Chicago, IL")
    assert (f["loaded_miles"], f["pay"], f["equipment"]) == (530.0, 1450.0, "Reefer")


def test_rate_per_mile_is_not_mistaken_for_pay():
    r = load_check.clean_fields(load_check.parse_basic(OFFERS[2]), OFFERS[2])
    assert r["fields"]["rate_per_mile"] == 2.88
    assert r["fields"]["pay"] == 5581.44 and r["pay_from_rate"]


def test_largest_amount_is_taken_as_pay():
    assert load_check.parse_basic(OFFERS[3])["pay"] == 3200.0


def test_number_not_in_the_text_is_flagged_for_review():
    r = load_check.clean_fields({"pay": 9999, "loaded_miles": 781}, OFFERS[0])
    assert r["check"] == ["pay"]


def test_every_number_from_a_file_is_flagged_for_review():
    r = load_check.clean_fields({"pay": 2100, "loaded_miles": 781}, None)
    assert set(r["check"]) == {"pay", "loaded_miles"}


def test_impossible_values_are_dropped():
    f = load_check.clean_fields({"pay": -5, "loaded_miles": 9e9, "rate_per_mile": True, "origin": 7}, "x")["fields"]
    assert f["pay"] is None and f["loaded_miles"] is None and f["rate_per_mile"] is None and f["origin"] is None


def test_upload_without_claude_explains_itself(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert load_check.read_load("", "abc", "image/png")["error"] == "ai_unavailable"


def test_text_falls_back_to_pattern_matching_when_claude_fails(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(load_check, "ask_claude", boom)
    r = load_check.read_load(OFFERS[0])
    assert r["reader"] == "basic" and r["fields"]["pay"] == 2100.0


# ---------------- Claude call ----------------
def _fake_client(calls, blocks, stop="end_turn"):
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason=stop, content=blocks)
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))


def test_claude_read_uses_structured_output_and_skips_thinking(monkeypatch):
    calls = []
    answer = {k: None for k in load_check.OFFER_SCHEMA["properties"]} | {"pay": 2100, "loaded_miles": 781}
    blocks = [SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(answer))]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(agent, "get_client", lambda: _fake_client(calls, blocks))
    r = load_check.read_load(OFFERS[0])
    assert r["reader"] == "ai" and r["fields"]["pay"] == 2100.0 and r["check"] == []
    kw = calls[0]
    assert "tool_choice" not in kw and "temperature" not in kw
    assert kw["output_config"]["format"]["schema"] is load_check.OFFER_SCHEMA


def test_upload_is_sent_as_the_right_block(monkeypatch):
    for media, kind in (("application/pdf", "document"), ("image/png", "image")):
        calls = []
        answer = {k: None for k in load_check.OFFER_SCHEMA["properties"]}
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        monkeypatch.setattr(agent, "get_client", lambda: _fake_client(calls, [SimpleNamespace(type="text", text=json.dumps(answer))]))
        load_check.read_load("", "QUJD", media)
        block = calls[0]["messages"][0]["content"][0]
        assert block["type"] == kind and block["source"]["media_type"] == media


def test_refusal_is_not_read_as_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(agent, "get_client", lambda: _fake_client([], [], stop="refusal"))
    with pytest.raises(RuntimeError):
        agent.ask_claude("x", system="y")


# ---------------- Money ----------------
def test_price_matches_the_cost_engine_and_hand_math():
    p = load_check.price_load(5575, 1938, 31.046, 192.2, CFG)
    b = calculate_costs(31.046, 1938, 192.2, 5575, CFG)
    assert p["net_profit"] == b["net_profit"] == 2189.85
    assert p["target_gross"] == round(3385.15 + 0.30 * 2161.2, 2)
    assert p["verdict"] == "take" and p["counter_price"] is None


def test_counter_rounds_up_and_covers_the_target():
    p = load_check.price_load(1100, 500, 50, 100, CFG)   # 650 mi, cost about 1018
    assert p["verdict"] == "counter"
    assert p["counter_price"] % 25 == 0
    assert p["target_gross"] <= p["counter_price"] < p["target_gross"] + 25


def test_a_load_below_cost_is_a_pass_with_a_price_that_works():
    p = load_check.price_load(800, 500, 50, 100, CFG)
    assert p["verdict"] == "pass" and p["net_profit"] < 0 and p["counter_price"] > 800


def test_owner_operator_keeps_driver_pay():
    p = load_check.price_load(2000, 800, 50, 100, CFG)
    assert p["owner_net_profit"] == round(p["net_profit"] + p["driver_pay"], 2)


def test_own_costs_change_the_answer():
    base = load_check.evaluate_load(2000, 800, empty_in_miles=50, empty_out_miles=100)
    thrifty = load_check.evaluate_load(2000, 800, empty_in_miles=50, empty_out_miles=100, costs={"mpg": 8.0})
    assert thrifty["net_profit"] > base["net_profit"]


def test_unknown_miles_are_reported_not_hidden(monkeypatch):
    monkeypatch.setattr(load_check, "resolve_location", lambda x: None)
    monkeypatch.setattr(load_check, "get_market_scores", lambda: {})
    r = load_check.evaluate_load(2000, 800, "Nowhere, ZZ", "Elsewhere, ZZ", "Somewhere, ZZ")
    assert r["sources"] == {"loaded": "entered", "empty_in": "unknown", "empty_out": "estimated"}
    assert r["empty_out_miles" if "empty_out_miles" in r else "deadhead_out_miles"] == 162.5
    assert "error" in load_check.evaluate_load(2000, None, "Nowhere, ZZ", "Elsewhere, ZZ")


def test_counter_message_states_the_numbers():
    m = load_check.counter_message("Dallas, TX", "Atlanta, GA", 781, 2100, 2350)
    assert "Dallas, TX to Atlanta, GA" in m and "$2,350" in m and "$3.01/mi" in m


# ---------------- Demo parity ----------------
@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_demo_javascript_gives_the_same_answers():
    prices = [(5575, 1938, 31.046, 192.2), (1100, 500, 50, 100), (800, 500, 50, 100), (300, 60, 5, 10), (2000, 800, 0, 162.5)]
    script = (
        "require(process.argv[1]);const c=JSON.parse(process.argv[2]);"
        "console.log(JSON.stringify({prices:c.prices.map(p=>PLCheck.priceLoad(p[0],p[1],p[2],p[3],c.cfg,0.30)),"
        "offers:c.offers.map(t=>PLCheck.parseBasic(t))}))"
    )
    payload = json.dumps({"prices": prices, "offers": OFFERS, "cfg": config.DEFAULTS})
    out = subprocess.run(["node", "-e", script, str(ROOT / "web" / "check-core.js"), payload],
                         capture_output=True, text=True, check=True).stdout
    js = json.loads(out)
    for args, got in zip(prices, js["prices"]):
        want = load_check.price_load(*args, CFG)
        for key in ("net_profit", "owner_net_profit", "total_cost", "operating_cost", "target_gross", "total_miles"):
            assert got[key] == pytest.approx(want[key], abs=0.011), key
        assert got["verdict"] == want["verdict"] and got["counter_price"] == want["counter_price"]
    for text, got in zip(OFFERS, js["offers"]):
        assert got == load_check.parse_basic(text), text


# ---------- Endpoints ----------
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import accounts
    import app as app_module
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    accounts.init_db()
    return TestClient(app_module.app)


def test_refused_requests_do_not_use_a_read(client):
    assert client.post("/api/check/read", json={"text": "  "}).status_code == 400
    assert client.post("/api/check/read", json={"upload_base64": "QUJD", "media_type": "text/html"}).status_code == 400
    assert client.post("/api/check/read", json={"upload_base64": "QUJD", "media_type": "image/png"}).status_code == 422
    assert client.get("/api/me").json()["searches_left"] == config.PLANS["free"]["daily_searches"]


def test_free_reads_run_out_but_typing_a_load_never_does(client):
    limit = config.PLANS["free"]["daily_searches"]
    for _ in range(limit):
        assert client.post("/api/check/read", json={"text": OFFERS[0]}).status_code == 200
    assert client.post("/api/check/read", json={"text": OFFERS[0]}).status_code == 402
    body = {"gross": 2100, "loaded_miles": 781, "empty_in_miles": 10, "empty_out_miles": 100}
    assert client.post("/api/check", json=body).json()["verdict"] in ("take", "counter", "pass")


def test_bad_numbers_are_rejected(client):
    assert client.post("/api/check", json={"gross": -5, "loaded_miles": 500}).status_code == 422
    assert client.post("/api/check", json={"gross": 900, "loaded_miles": 500, "costs": {"mpg": 0}}).status_code == 422


def test_fleet_api_requires_a_key(client):
    assert client.post("/v1/check", json={"gross": 900, "loaded_miles": 500}).status_code == 401
    assert client.post("/v1/check/read", json={"text": OFFERS[0]}).status_code == 401
