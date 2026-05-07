"""Smoke tests for the Phase 0 scaffold."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    # Lazy import: ensures conftest.py's env-var setup runs first.
    from autotrader.main import app  # noqa: PLC0415

    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["live_trading_enabled"] is False
    assert "version" in body


def test_login_wrong_passcode(client: TestClient) -> None:
    r = client.post("/auth/login", json={"passcode": "nope"})
    assert r.status_code == 401


def test_login_then_me(client: TestClient) -> None:
    r = client.post("/auth/login", json={"passcode": "test-passcode"})
    assert r.status_code == 200
    token = r.json()["token"]

    r2 = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert "issued_at" in r2.json()


def test_me_without_token(client: TestClient) -> None:
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_with_garbage_token(client: TestClient) -> None:
    r = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
