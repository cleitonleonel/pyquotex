"""Broker router + manager tests.

We replace ``pyquotex.stable_api.Quotex`` with a small stub class that
remembers the most-recently-built instance so individual tests can
toggle behaviour (immediate-success, OTP-required, broker-rejected).
The suite never touches the network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from pyquotex.global_value import AuthStatus, WebsocketStatus

# ---------------------------------------------------------------------------
# Fake Quotex
# ---------------------------------------------------------------------------


@dataclass
class _FakeState:
    """Mirrors :class:`pyquotex.global_value.ConnectionState` — the
    manager keys ``connected`` on these two fields now, so the stub
    has to expose them as real integers (not MagicMock attrs that are
    always truthy)."""

    status: WebsocketStatus = WebsocketStatus.CONNECTED
    auth_status: AuthStatus = AuthStatus.AUTHENTICATED
    websocket_error_reason: str | None = None


@dataclass
class _FakeReconnectStats:
    attempts: int = 0
    successful_reconnects: int = 0
    failed_reconnects: int = 0
    last_error: str | None = None


@dataclass
class _FakeReconnectSupervisor:
    stats: _FakeReconnectStats = field(default_factory=_FakeReconnectStats)


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
        proxy_config=None,
        reconnect_policy: Any = None,
        **_: Any,  # tolerate further kwargs the real Quotex grows
    ) -> None:
        self.email = email
        self.password = password
        self.root_path = root_path
        self.lang = lang
        self.on_otp_callback = on_otp_callback
        # Captured for test assertions — ``QuotexManager`` now passes
        # a ``ProxyConfig(use_browser_tls=True)`` so pyquotex uses
        # curl_cffi to clear Cloudflare on ``qxbroker.com``.
        self.proxy_config = proxy_config
        # Captured so tests can assert the manager forwards a
        # trading-tuned ``ReconnectPolicy`` instead of accepting the
        # pyquotex-default 1s → 60s backoff.
        self.reconnect_policy = reconnect_policy
        # The manager now keys ``connected`` on real WS+auth state
        # (``api.state.status`` + ``auth_status``) — so the fake has
        # to expose a real state object, not a MagicMock. ``connect()``
        # flips these to CONNECTED/AUTHENTICATED on success below.
        self.api = MagicMock()
        self.api.state = _FakeState(
            status=WebsocketStatus.DISCONNECTED,
            auth_status=AuthStatus.NOT_AUTHENTICATED,
        )
        self.api.reconnect_supervisor = _FakeReconnectSupervisor()
        self.account_mode_set: str | None = None
        # Manager mirrors session_data onto the client before
        # connect() and reads it back after. Real pyquotex stores
        # this on ``Quotex.session_data``.
        self.session_data: dict = {}
        FakeQuotex.last_instance = self

    def set_account_mode(self, mode: str) -> None:
        self.account_mode_set = mode

    def _flip_connected(self) -> None:
        """Move the fake state machine into the post-login steady state.

        Real pyquotex transitions through CONNECTING → CONNECTED inside
        ``api.connect``; tests only care about the terminal state, so
        we shortcut to it here on every success path.
        """
        self.api.state.status = WebsocketStatus.CONNECTED
        self.api.state.auth_status = AuthStatus.AUTHENTICATED
        # Mirror pyquotex's behaviour — a successful connect leaves a
        # populated session_data on the client.
        if not self.session_data.get("token"):
            self.session_data = {
                "token": "fake-ssid-from-login",
                "cookies": "fake-cookies",
                "user_agent": "Firefox/144 (test)",
            }

    async def connect(self) -> tuple[bool, str]:
        if FakeQuotex.behavior == "ok":
            self._flip_connected()
            return True, "ok"
        if FakeQuotex.behavior == "rejected":
            return False, "auth rejected by broker"
        if FakeQuotex.behavior == "needs_otp":
            assert self.on_otp_callback is not None
            code = await self.on_otp_callback("Enter the code sent to your email:")
            if str(code) == FakeQuotex.valid_otp:
                self._flip_connected()
                return True, "ok"
            return False, "bad otp"
        raise AssertionError(f"unknown behavior: {FakeQuotex.behavior}")

    async def close(self) -> bool:
        self.api.state.status = WebsocketStatus.DISCONNECTED
        self.api.state.auth_status = AuthStatus.NOT_AUTHENTICATED
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
        manager.set_session_store(None)
        manager.set_otp_relay(None)
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
    # Regression guard for the Cloudflare 403 fix. Without
    # ``use_browser_tls=True`` plain ``httpx`` is rejected at
    # ``qxbroker.com``'s sign-in modal — so the manager must always
    # build the client with curl_cffi browser impersonation on, and
    # the impersonate must name a real browser profile (Cloudflare's
    # bot scoring rotates which family it currently trusts; we don't
    # pin the exact value so ops can re-tune without a code change
    # tripping this assertion).
    pcfg = FakeQuotex.last_instance.proxy_config
    assert pcfg is not None
    assert getattr(pcfg, "use_browser_tls", False) is True
    impersonate = getattr(pcfg, "impersonate", "")
    assert impersonate and impersonate.split("_")[0].rstrip(
        "0123456789"
    ) in {"chrome", "firefox", "safari", "edge"}, (
        f"impersonate must be a recognised browser profile, "
        f"got: {impersonate!r}"
    )


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


# ---------------------------------------------------------------------------
# Session persistence (Task 3 of OTP relay plan)
# ---------------------------------------------------------------------------


class _FakeSessionStore:
    """In-memory drop-in for SessionStore — tests assert on
    ``saved_payloads`` / ``primed_payload`` rather than real disk I/O."""

    def __init__(self, primed: dict | None = None) -> None:
        self.primed_payload = primed
        self.saved_payloads: list[dict] = []
        self.cleared_count = 0

    def load(self) -> dict | None:
        return self.primed_payload

    def save(self, session_data: dict) -> None:
        self.saved_payloads.append(dict(session_data))

    def clear(self) -> None:
        self.cleared_count += 1


def test_manager_loads_session_before_connect_when_attached(
    client: TestClient,
) -> None:
    """When a SessionStore is attached and has a cached payload, the
    manager hydrates client.session_data BEFORE awaiting connect."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    primed = {
        "token": "cached-ssid",
        "cookies": "laravel_session=foo",
        "user_agent": "Firefox/144",
    }
    store = _FakeSessionStore(primed=primed)
    manager.set_session_store(store)

    headers = _login(client)
    _put_credentials(client, headers)
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200, r.text

    # The fake Quotex.connect() doesn't observe session_data directly,
    # so we assert that the manager forwarded the payload onto the
    # client instance constructed by the FakeQuotex factory.
    fq = FakeQuotex.last_instance
    assert fq is not None
    # On real Quotex, session_data lives on self (the fake mirrors via
    # ``session_data`` attr set by the manager).
    assert getattr(fq, "session_data", None) == primed


def test_manager_saves_session_after_successful_connect(
    client: TestClient,
) -> None:
    """On a successful connect, manager pushes the (now-warm)
    client.session_data through SessionStore.save."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    store = _FakeSessionStore()
    manager.set_session_store(store)

    headers = _login(client)
    _put_credentials(client, headers)

    # FakeQuotex.connect populates a fresh session_data on its client.
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200, r.text

    assert len(store.saved_payloads) >= 1
    last = store.saved_payloads[-1]
    assert last.get("token")  # truthy


# ---------------------------------------------------------------------------
# OTP relay integration (Task 7 of OTP relay plan)
# ---------------------------------------------------------------------------


class _FakeOTPRelay:
    """Captures every relay-side call so manager tests can assert
    the wiring."""

    def __init__(self) -> None:
        self.required_calls: list[tuple[str, int]] = []
        self.resolved_count = 0
        self.timeout_count = 0

    async def on_otp_required(self, prompt: str, attempt: int) -> None:
        self.required_calls.append((prompt, attempt))

    async def on_otp_resolved(self) -> None:
        self.resolved_count += 1

    async def on_otp_timeout(self) -> None:
        self.timeout_count += 1


def test_manager_calls_relay_on_otp_required(client: TestClient) -> None:
    """When the broker challenges with OTP, the manager invokes
    relay.on_otp_required(prompt, attempt=1) BEFORE parking on the
    180s timer."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    relay = _FakeOTPRelay()
    manager.set_otp_relay(relay)
    FakeQuotex.behavior = "needs_otp"

    headers = _login(client)
    _put_credentials(client, headers)
    # Fire connect; FakeQuotex.connect parks awaiting OTP via the
    # registered callback. The relay must have been called before
    # the response comes back as 202 awaiting_otp.
    client.post("/broker/connect", headers=headers)

    # Submit so the test doesn't leak a parked task.
    client.post("/broker/otp", headers=headers, json={"code": "654321"})

    assert len(relay.required_calls) >= 1
    prompt, attempt = relay.required_calls[0]
    assert attempt == 1
    assert prompt  # non-empty


def test_manager_calls_relay_on_otp_resolved(client: TestClient) -> None:
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    relay = _FakeOTPRelay()
    manager.set_otp_relay(relay)
    FakeQuotex.behavior = "needs_otp"

    headers = _login(client)
    _put_credentials(client, headers)
    client.post("/broker/connect", headers=headers)
    client.post("/broker/otp", headers=headers, json={"code": "654321"})

    # Allow the background connect task to settle.
    import time  # noqa: PLC0415
    for _ in range(20):
        if manager.status().state == "connected":
            break
        time.sleep(0.05)

    assert relay.resolved_count == 1
