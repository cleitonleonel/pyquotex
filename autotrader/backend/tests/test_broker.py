"""Broker router + manager tests.

We replace ``pyquotex.stable_api.Quotex`` with a small stub class that
remembers the most-recently-built instance so individual tests can
toggle behaviour (immediate-success, OTP-required, broker-rejected).
The suite never touches the network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fake Quotex
# ---------------------------------------------------------------------------


class FakeQuotex:
    """Stand-in for ``pyquotex.stable_api.Quotex``.

    Tests configure behaviour via class-level knobs *before* hitting
    ``/broker/connect`` — once the manager constructs a real instance,
    the relevant knob fires.
    """

    # Class-level config mutated by tests.
    behavior: str = "ok"          # "ok" | "needs_otp" | "rejected"
    valid_otp: str = "654321"
    last_instance: FakeQuotex | None = None
    # Pipeline / executor: capture every trade call here.
    buy_calls: ClassVar[list[dict]] = []
    pending_calls: ClassVar[list[dict]] = []

    def __init__(
        self,
        email: str,
        password: str,
        root_path: str = ".",
        lang: str = "en",
        on_otp_callback=None,
    ) -> None:
        self.email = email
        self.password = password
        self.root_path = root_path
        self.lang = lang
        self.on_otp_callback = on_otp_callback
        # ``api`` is truthy → manager.connected reads True after
        # connect() succeeds.
        self.api = MagicMock()
        self.account_mode_set: str | None = None
        FakeQuotex.last_instance = self

    def set_account_mode(self, mode: str) -> None:
        self.account_mode_set = mode

    async def connect(self) -> tuple[bool, str]:
        if FakeQuotex.behavior == "ok":
            return True, "ok"
        if FakeQuotex.behavior == "rejected":
            return False, "auth rejected by broker"
        if FakeQuotex.behavior == "needs_otp":
            assert self.on_otp_callback is not None
            code = await self.on_otp_callback("Enter the code sent to your email:")
            if str(code) == FakeQuotex.valid_otp:
                return True, "ok"
            return False, "bad otp"
        raise AssertionError(f"unknown behavior: {FakeQuotex.behavior}")

    async def close(self) -> bool:
        return True

    async def change_account(self, mode: str, tournament_id: int = 0) -> None:
        self.account_mode_set = mode

    async def get_balance(self, timeout: int = 10) -> float:  # noqa: ASYNC109
        return 10_000.0

    async def get_all_assets(self) -> dict[str, str]:
        # name -> code mapping mirrors pyquotex's real shape.
        return {
            "EUR/USD": "EURUSD",
            "EUR/USD (OTC)": "EURUSD_otc",
            "GBP/USD": "GBPUSD",
            "Gold": "XAUUSD",
            "USDBDT (OTC)": "USDBDT_otc",
        }

    # -- Trade-execution surface used by the pipeline tests ----------

    async def buy(
        self,
        amount: float,
        asset: str,
        direction: str,
        duration: int,
        time_mode: str = "TIME",
    ) -> tuple[bool, dict]:
        FakeQuotex.buy_calls.append(
            {
                "amount": amount,
                "asset": asset,
                "direction": direction,
                "duration": duration,
            },
        )
        return True, {"id": f"order-{len(FakeQuotex.buy_calls)}"}

    async def open_pending(
        self,
        amount: float,
        asset: str,
        direction: str,
        duration: int,
        open_time: object | None = None,
        confirm_timeout: float = 30.0,
    ) -> tuple[bool, bool]:
        """Mirrors real pyquotex: ticket goes to ``api.pending_id``.

        Real ``open_pending`` returns ``(True, pending_successful=True)``
        — a confirmation flag, not the ticket. The ticket itself is
        written to ``self.api.pending_id`` and that's what
        ``wait_for_order_close`` keys on. Tests assert on
        ``api.pending_id`` to verify the executor reads the right id.
        """
        FakeQuotex.pending_calls.append(
            {
                "amount": amount,
                "asset": asset,
                "direction": direction,
                "duration": duration,
                "open_time": open_time,
            },
        )
        ticket = f"pending-{len(FakeQuotex.pending_calls)}"
        self.api.pending_id = ticket
        self.api.pending_successful = True
        return True, True

    async def wait_for_order_close(
        self,
        order_id: str | int,
        duration: int = 0,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> tuple[str, float]:
        # Tests don't await this; they shut down before the watcher fires.
        _ = (order_id, duration, timeout)
        return "win", 0.85


@pytest.fixture(autouse=True)
def _reset_fake_quotex() -> Iterator[None]:
    FakeQuotex.behavior = "ok"
    FakeQuotex.valid_otp = "654321"
    FakeQuotex.last_instance = None
    FakeQuotex.buy_calls = []
    FakeQuotex.pending_calls = []
    yield


@pytest.fixture
def fake_quotex_class(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeQuotex]]:
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )
    yield FakeQuotex


# ---------------------------------------------------------------------------
# TestClient + DB cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def client(fake_quotex_class: type[FakeQuotex]) -> Iterator[TestClient]:
    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.broker_credentials import BrokerCredentials  # noqa: PLC0415

    with TestClient(app) as c:
        yield c

    from sqlmodel import delete  # noqa: PLC0415

    async def _wipe() -> None:
        manager = app.state.quotex_manager
        await manager.cancel_connect()
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


def _put_credentials(client: TestClient, headers: dict[str, str], **overrides: str) -> None:
    body: dict[str, str] = {"email": "x@y.com", "password": "p", **overrides}
    r = client.put("/broker/credentials", headers=headers, json=body)
    assert r.status_code == 200, r.text


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
    assert body["state"] == "idle"
    assert body["awaiting_otp"] is False


def test_connect_without_credentials(client: TestClient) -> None:
    headers = _login(client)
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 400
    assert "credentials" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Happy path: no OTP
# ---------------------------------------------------------------------------


def test_credentials_then_connect(
    client: TestClient,
    fake_quotex_class: type[FakeQuotex],
) -> None:
    headers = _login(client)
    _put_credentials(client, headers, email="trader@example.com", password="s3cret")

    r = client.get("/broker/status", headers=headers)
    body = r.json()
    assert body["configured"] is True
    assert body["email_masked"] == "t***@example.com"

    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["state"] == "connected"

    assert FakeQuotex.last_instance is not None
    assert FakeQuotex.last_instance.email == "trader@example.com"
    assert FakeQuotex.last_instance.account_mode_set == "PRACTICE"


def test_balance_when_connected(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)
    client.post("/broker/connect", headers=headers)

    r = client.get("/broker/balance", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"balance": 10_000.0, "account_mode": "PRACTICE"}


def test_balance_when_disconnected(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)
    r = client.get("/broker/balance", headers=headers)
    assert r.status_code == 409


def test_disconnect(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)
    client.post("/broker/connect", headers=headers)

    r = client.post("/broker/disconnect", headers=headers)
    assert r.status_code == 200
    r = client.get("/broker/status", headers=headers)
    assert r.json()["connected"] is False


def test_delete_credentials(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)
    r = client.delete("/broker/credentials", headers=headers)
    assert r.status_code == 200
    r = client.get("/broker/status", headers=headers)
    assert r.json()["configured"] is False


# ---------------------------------------------------------------------------
# Asset cache
# ---------------------------------------------------------------------------


def test_assets_endpoint_empty_when_disconnected(client: TestClient) -> None:
    headers = _login(client)
    r = client.get("/broker/assets", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"assets": [], "count": 0}


def test_assets_populated_after_connect(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)
    client.post("/broker/connect", headers=headers)

    r = client.get("/broker/assets", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 4
    assert "EURUSD" in body["assets"]
    assert "EURUSD_otc" in body["assets"]


# ---------------------------------------------------------------------------
# Broker rejects (no OTP, just bad creds)
# ---------------------------------------------------------------------------


def test_connect_rejected_returns_502(client: TestClient) -> None:
    FakeQuotex.behavior = "rejected"
    headers = _login(client)
    _put_credentials(client, headers)

    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 502
    assert "rejected" in r.json()["detail"].lower()

    r = client.get("/broker/status", headers=headers)
    body = r.json()
    assert body["state"] == "error"
    assert body["connected"] is False


# ---------------------------------------------------------------------------
# OTP / 2FA flow
# ---------------------------------------------------------------------------


def test_connect_with_otp_full_flow(client: TestClient) -> None:
    FakeQuotex.behavior = "needs_otp"
    headers = _login(client)
    _put_credentials(client, headers)

    # First /connect call returns 202 with the OTP prompt.
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 202
    body = r.json()
    assert body["connected"] is False
    assert body["state"] == "awaiting_otp"
    assert "code" in body["otp_prompt"].lower()

    # Status endpoint also reports the awaiting_otp state.
    r = client.get("/broker/status", headers=headers)
    body = r.json()
    assert body["state"] == "awaiting_otp"
    assert body["awaiting_otp"] is True
    assert body["otp_prompt"] is not None

    # Submit the correct code → connect resumes and succeeds.
    r = client.post("/broker/otp", headers=headers, json={"code": "654321"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "connected"
    assert body["connected"] is True


def test_otp_submitted_when_not_awaiting_returns_409(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)

    r = client.post("/broker/otp", headers=headers, json={"code": "111111"})
    assert r.status_code == 409


def test_wrong_otp_lands_in_error(client: TestClient) -> None:
    FakeQuotex.behavior = "needs_otp"
    FakeQuotex.valid_otp = "111222"
    headers = _login(client)
    _put_credentials(client, headers)

    client.post("/broker/connect", headers=headers)
    r = client.post("/broker/otp", headers=headers, json={"code": "999000"})
    assert r.status_code == 200  # OTP was accepted by us; broker rejected it
    body = r.json()
    assert body["state"] == "error"
    assert body["connected"] is False


def test_cancel_in_flight_otp(client: TestClient) -> None:
    FakeQuotex.behavior = "needs_otp"
    headers = _login(client)
    _put_credentials(client, headers)

    r = client.post("/broker/connect", headers=headers)
    assert r.json()["state"] == "awaiting_otp"

    r = client.post("/broker/cancel", headers=headers)
    assert r.status_code == 200

    r = client.get("/broker/status", headers=headers)
    assert r.json()["state"] == "idle"


# ---------------------------------------------------------------------------
# REAL-trading gate
# ---------------------------------------------------------------------------


def test_real_mode_blocked_when_live_trading_disabled(client: TestClient) -> None:
    headers = _login(client)
    r = client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p", "account_mode": "REAL"},
    )
    assert r.status_code == 403
    assert "real" in r.json()["detail"].lower()


def test_account_mode_switch_to_real_blocked(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)
    client.post("/broker/connect", headers=headers)

    r = client.post(
        "/broker/account-mode",
        headers=headers,
        json={"mode": "REAL"},
    )
    assert r.status_code == 403


def test_account_mode_switch_to_practice_ok(client: TestClient) -> None:
    headers = _login(client)
    _put_credentials(client, headers)
    client.post("/broker/connect", headers=headers)

    r = client.post(
        "/broker/account-mode",
        headers=headers,
        json={"mode": "PRACTICE"},
    )
    assert r.status_code == 200
    assert r.json()["account_mode"] == "PRACTICE"
