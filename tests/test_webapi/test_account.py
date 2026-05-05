"""Balance + profile endpoints."""
from __future__ import annotations


def test_balance_returns_cached_value(client, auth_headers):
    r = client.get("/account/balance", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["balance"] == 10000.0
    assert body["currency"] == "USD"
    assert body["account_mode"] == "PRACTICE"


def test_profile_passes_through_fields(client, auth_headers):
    r = client.get("/account/profile", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["profile_id"] == 42
    assert body["nickname"] == "tester"
    assert body["currency_code"] == "USD"
    assert body["timezone_offset"] == 0
    assert body["demo_balance"] == 10000.0


def test_balance_502_on_broker_error(client, auth_headers, fake_quotex):
    fake_quotex.get_balance.side_effect = RuntimeError("ws dead")
    r = client.get("/account/balance", headers=auth_headers)
    assert r.status_code == 502
    assert "ws dead" in r.json()["detail"]
