"""Health probe + auth-header behaviour."""
from __future__ import annotations


def test_health_no_auth_required(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["connected"] is True
    assert body["auth_status"] == "AUTHENTICATED"


def test_protected_endpoint_rejects_missing_key(client):
    r = client.post("/auth/connect")
    assert r.status_code == 401
    assert "API key" in r.json()["detail"]


def test_protected_endpoint_rejects_wrong_key(client):
    r = client.post("/auth/connect", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_x_api_key_header_accepted(client, auth_headers):
    r = client.post("/auth/connect", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_authorization_bearer_header_accepted(client):
    from .conftest import API_KEY
    r = client.post(
        "/auth/connect",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200, r.text


def test_connect_returns_profile_and_balance(client, auth_headers, fake_quotex):
    """Warm-cache path: ``check_connect()`` returns True so the
    endpoint short-circuits and returns the cached profile + balance
    without re-running the full connect handshake."""
    r = client.post("/auth/connect", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["balance"] == 10000.0
    assert body["nickname"] == "tester"
    assert body["profile_id"] == 42
    assert body["otp_required"] is False


def test_connect_cold_start_runs_full_handshake(client, auth_headers, fake_quotex):
    """Cold-start path: ``check_connect()`` False forces the route to
    spawn the connect task. Once it completes the response carries
    profile + balance."""
    fake_quotex.check_connect.return_value = False
    r = client.post("/auth/connect", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["otp_required"] is False
    fake_quotex.connect.assert_awaited()
