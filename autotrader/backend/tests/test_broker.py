"""Broker router + manager tests.

We mock the ``Quotex`` class so the suite never touches the network.
Each test reaches into ``app.state.quotex_manager`` for whitebox
fixture work, which is what the real DI does anyway.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_quotex_class(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Replace ``pyquotex.stable_api.Quotex`` with a controllable mock."""
    instance = MagicMock(name="QuotexInstance")
    instance.api = MagicMock()  # truthy so ``manager.connected`` reads True
    instance.connect = AsyncMock(return_value=(True, "ok"))
    instance.close = AsyncMock(return_value=True)
    instance.change_account = AsyncMock(return_value=None)
    instance.get_balance = AsyncMock(return_value=10_000.0)
    instance.set_account_mode = MagicMock(return_value=None)

    cls = MagicMock(name="QuotexClass", return_value=instance)
    # Patch the symbol the manager actually imports.
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        cls,
    )
    yield cls


@pytest.fixture
def client(fake_quotex_class: MagicMock) -> Iterator[TestClient]:
    """Fresh TestClient + DB cleanup between tests."""
    # Lazy imports keep ``conftest.py``'s env-var setup in scope.
    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.broker_credentials import BrokerCredentials  # noqa: PLC0415

    with TestClient(app) as c:
        yield c

    # Tear down: clear any state the test left behind so the next test
    # gets a clean slate without reloading modules.
    import asyncio  # noqa: PLC0415

    from sqlmodel import delete  # noqa: PLC0415

    async def _wipe() -> None:
        manager = app.state.quotex_manager
        if manager.connected:
            await manager.disconnect()
        manager.clear_credentials()
        async with AsyncSessionLocal() as s:
            await s.exec(delete(BrokerCredentials))  # type: ignore[call-overload]
            await s.commit()
        await engine.dispose()

    asyncio.new_event_loop().run_until_complete(_wipe())


def _login(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"passcode": "test-passcode"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_status_requires_auth(client: TestClient) -> None:
    r = client.get("/broker/status")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_status_empty(client: TestClient) -> None:
    headers = _login(client)
    r = client.get("/broker/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["connected"] is False
    assert body["account_mode"] == "PRACTICE"


def test_connect_without_credentials(client: TestClient) -> None:
    headers = _login(client)
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 400
    assert "credentials" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Credentials lifecycle
# ---------------------------------------------------------------------------


def test_put_credentials_then_connect(client: TestClient, fake_quotex_class: MagicMock) -> None:
    headers = _login(client)

    r = client.put(
        "/broker/credentials",
        headers=headers,
        json={
            "email": "trader@example.com",
            "password": "s3cret-pa55",
            "account_mode": "PRACTICE",
        },
    )
    assert r.status_code == 200, r.text

    r = client.get("/broker/status", headers=headers)
    body = r.json()
    assert body["configured"] is True
    assert body["email_masked"] == "t***@example.com"

    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200
    assert r.json()["connected"] is True

    fake_quotex_class.assert_called_once()
    kwargs = fake_quotex_class.call_args.kwargs
    assert kwargs["email"] == "trader@example.com"
    assert kwargs["password"] == "s3cret-pa55"

    r = client.get("/broker/status", headers=headers)
    assert r.json()["connected"] is True


def test_balance_when_connected(client: TestClient) -> None:
    headers = _login(client)
    client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    client.post("/broker/connect", headers=headers)

    r = client.get("/broker/balance", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"balance": 10_000.0, "account_mode": "PRACTICE"}


def test_balance_when_disconnected(client: TestClient) -> None:
    headers = _login(client)
    client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    r = client.get("/broker/balance", headers=headers)
    assert r.status_code == 409


def test_disconnect(client: TestClient) -> None:
    headers = _login(client)
    client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    client.post("/broker/connect", headers=headers)

    r = client.post("/broker/disconnect", headers=headers)
    assert r.status_code == 200
    r = client.get("/broker/status", headers=headers)
    assert r.json()["connected"] is False


def test_delete_credentials(client: TestClient) -> None:
    headers = _login(client)
    client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    r = client.delete("/broker/credentials", headers=headers)
    assert r.status_code == 200
    r = client.get("/broker/status", headers=headers)
    assert r.json()["configured"] is False


# ---------------------------------------------------------------------------
# REAL-trading gate
# ---------------------------------------------------------------------------


def test_real_mode_blocked_when_live_trading_disabled(client: TestClient) -> None:
    headers = _login(client)
    r = client.put(
        "/broker/credentials",
        headers=headers,
        json={
            "email": "x@y.com",
            "password": "p",
            "account_mode": "REAL",
        },
    )
    assert r.status_code == 403
    assert "real" in r.json()["detail"].lower()


def test_account_mode_switch_to_real_blocked(client: TestClient) -> None:
    headers = _login(client)
    client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    client.post("/broker/connect", headers=headers)

    r = client.post(
        "/broker/account-mode",
        headers=headers,
        json={"mode": "REAL"},
    )
    assert r.status_code == 403


def test_account_mode_switch_to_practice_ok(client: TestClient) -> None:
    headers = _login(client)
    client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    client.post("/broker/connect", headers=headers)

    r = client.post(
        "/broker/account-mode",
        headers=headers,
        json={"mode": "PRACTICE"},
    )
    assert r.status_code == 200
    assert r.json()["account_mode"] == "PRACTICE"
