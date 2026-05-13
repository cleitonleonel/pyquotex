"""Broker router + manager tests.

We replace ``pyquotex.stable_api.Quotex`` with a small stub class that
remembers the most-recently-built instance so individual tests can
toggle behaviour (immediate-success, OTP-required, broker-rejected).
The suite never touches the network.
"""

from __future__ import annotations

import asyncio
import contextlib
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
    # Asset universe returned by get_all_assets. Tests that exercise
    # the asset-availability pre-flight can override this mapping.
    # Convention mirrors real pyquotex: {symbol_name: internal_numeric_id}.
    # Symbol names are the trading codes used in WebSocket subscribes and
    # signal.asset (e.g. "EURUSD_otc"); values are opaque numeric IDs.
    _DEFAULT_ASSETS_MAPPING: ClassVar[dict[str, str]] = {
        "EURUSD": "1",
        "EURUSD_otc": "2",
        "GBPUSD": "3",
        "XAUUSD": "4",
        "USDBDT_otc": "5",
    }
    assets_mapping: ClassVar[dict[str, str]] = dict(_DEFAULT_ASSETS_MAPPING)

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
        if FakeQuotex.behavior == "ok_no_otp":
            # Caller provided session_data with a token; pyquotex
            # would skip authenticate() in this case. Fake it: succeed
            # WITHOUT invoking on_otp_callback.
            assert self.session_data.get("token"), (
                "ok_no_otp expects pre-warmed session_data — the test "
                "should have wired a SessionStore with a primed payload."
            )
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
        # Tests that want a custom universe set ``FakeQuotex.assets_mapping``
        # before the asset cache is populated.
        return dict(FakeQuotex.assets_mapping)

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
    FakeQuotex.assets_mapping = dict(FakeQuotex._DEFAULT_ASSETS_MAPPING)
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


def test_manager_assets_are_symbol_codes_not_numeric_ids(client: TestClient) -> None:
    """REGRESSION: previously _refresh_assets_locked read mapping.values()
    which yielded internal numeric IDs ('1','157',...). The pre-flight in
    the executor compared signal.asset (a symbol like 'USDBRL_otc')
    against these IDs and false-rejected every trade. This test locks
    the schema: manager.assets must contain the SYMBOL NAMES used in
    WebSocket subscribes."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    headers = _login(client)
    _put_credentials(client, headers)
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200, r.text

    # Wait until connected so the assets are fetched.
    import time  # noqa: PLC0415
    for _ in range(40):
        if manager.status().state == "connected":
            break
        time.sleep(0.05)

    assets = manager.assets
    assert assets, "manager.assets is empty after connect"
    # Must contain at least one of the symbols from the fake mapping.
    # The fake uses real-convention keys (symbol names -> numeric IDs).
    assert "EURUSD" in assets or "EURUSD_otc" in assets, (
        f"expected symbol-style codes in assets; got {assets[:10]}"
    )
    # Numeric IDs should NOT appear as standalone entries.
    assert not any(a.isdigit() for a in assets), (
        f"manager.assets contains numeric IDs (the old bug); first 10: "
        f"{sorted(assets)[:10]}"
    )


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


# ---------------------------------------------------------------------------
# Fix C — manager halts pyquotex's internal ReconnectSupervisor after N
# consecutive OTP-timeout failures. Without this gate, pyquotex's
# supervisor (default `max_attempts=-1`) regenerates a PIN email
# inside one `_do_connect` call forever. Production incident
# 2026-05-12: 5 PIN emails in 13min before the operator could intervene.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_halts_after_consecutive_otp_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After N consecutive OTP-callback timeouts (`settings.otp_max_attempts`),
    the manager must:

    * transition to `awaiting_manual_recovery`,
    * disable pyquotex's reconnect supervisor (`policy.enabled = False`),
    * emit a `system.error` event signalling the operator must run /reconnect,
    * raise `QuotexManagerError` from `_on_otp_callback` to abort the
      in-flight `client.connect()` call.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    from autotrader.config import settings  # noqa: PLC0415
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        QuotexManager,
        QuotexManagerError,
    )

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )

    captured_events: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, event_type: str, payload: dict) -> None:
            captured_events.append((event_type, payload))

    mgr = QuotexManager(root_path=".", event_bus=_SpyBus())
    relay = _FakeOTPRelay()
    mgr.set_otp_relay(relay)

    # Inject a stub client with a supervisor whose ``policy.enabled``
    # we'll observe getting flipped to False.
    policy = SimpleNamespace(enabled=True)
    supervisor = SimpleNamespace(policy=policy)
    api_ns = SimpleNamespace(reconnect_supervisor=supervisor)
    mgr._client = SimpleNamespace(api=api_ns)  # type: ignore[assignment]

    cap = settings.otp_max_attempts
    assert cap == 3, f"test assumes default cap=3, got {cap}"

    # First `cap` calls all park on `asyncio.wait_for` and hit our
    # timeout — model that by failing the future fast (cancel it) so
    # the except branch in `_on_otp_callback` runs and bumps the
    # `_consecutive_otp_failures` counter.
    async def _drive_one_timeout() -> None:
        # Schedule the callback, then cancel its future from outside.
        task = asyncio.create_task(mgr._on_otp_callback("prompt"))
        # Give it a tick to register the future.
        for _ in range(20):
            if mgr._otp_future is not None and not mgr._otp_future.done():
                break
            await asyncio.sleep(0)
        assert mgr._otp_future is not None
        mgr._otp_future.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    for _ in range(cap):
        await _drive_one_timeout()
    assert mgr._consecutive_otp_failures == cap

    # The (cap+1)-th call must NOT park on the future — it must
    # detect the cap, disable the supervisor, emit the error, and
    # raise.
    with pytest.raises(QuotexManagerError, match="otp.*exhausted|exhausted.*otp"):
        await mgr._on_otp_callback("prompt over cap")

    # Manager state transitioned to manual-recovery.
    assert mgr._state == "awaiting_manual_recovery"
    assert mgr._last_error and "exhausted" in mgr._last_error.lower()

    # Supervisor was disabled.
    assert policy.enabled is False, (
        "manager did not disable pyquotex's reconnect supervisor — "
        "the internal loop will keep regenerating PIN emails"
    )

    # `system.error` event was published with an exhausted-kind.
    exhausted = [
        p for kind, p in captured_events
        if kind == "system.error"
        and "exhausted" in str(p.get("kind", "")).lower()
    ]
    assert exhausted, (
        f"expected a system.error event for OTP exhaustion; got "
        f"{[k for k, _ in captured_events]}"
    )

    # Relay was notified of the over-cap attempt so Fix A can fire its
    # 'gave up' alert. We can't assert exhausted-text here (Fix A logic
    # lives in the relay) — just that on_otp_required was called with
    # attempt > cap.
    over_cap_calls = [
        a for (_, a) in relay.required_calls if a > cap
    ]
    assert over_cap_calls, (
        f"expected at least one relay.on_otp_required call with "
        f"attempt > {cap}; got {relay.required_calls}"
    )


@pytest.mark.asyncio
async def test_manager_resets_otp_failures_on_successful_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful connect must zero the consecutive-failure counter
    so a future disconnect's cap starts fresh."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )

    mgr = QuotexManager(root_path=".")
    mgr.set_credentials("u@v.com", "pw", "PRACTICE")
    # Pre-seed the counter as if a previous disconnect window had bumped it.
    mgr._consecutive_otp_failures = 2

    mgr.begin_connect()
    await mgr.wait_settled(timeout=2.0)
    assert mgr.status().state == "connected", mgr.status().last_error
    assert mgr._consecutive_otp_failures == 0, (
        f"successful connect must reset counter; got "
        f"{mgr._consecutive_otp_failures}"
    )
    await mgr.disconnect()


@pytest.mark.asyncio
async def test_manager_reset_for_manual_reconnect_clears_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/reconnect` calls `reset_for_manual_reconnect()`: zero the
    counter, re-enable the supervisor, and clear the relay's
    exhaustion latch."""
    from types import SimpleNamespace  # noqa: PLC0415

    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )

    mgr = QuotexManager(root_path=".")

    # Track relay calls.
    relay_resets: list[int] = []

    class _Relay(_FakeOTPRelay):
        def reset_exhaustion(self) -> None:
            relay_resets.append(1)

    relay = _Relay()
    mgr.set_otp_relay(relay)

    # Pre-seed the manager as if Fix C had halted it.
    mgr._consecutive_otp_failures = 3
    mgr._state = "awaiting_manual_recovery"
    mgr._last_error = "OTP attempts exhausted — awaiting /reconnect"
    policy = SimpleNamespace(enabled=False)
    supervisor = SimpleNamespace(policy=policy)
    api_ns = SimpleNamespace(reconnect_supervisor=supervisor)
    mgr._client = SimpleNamespace(api=api_ns)  # type: ignore[assignment]

    mgr.reset_for_manual_reconnect()

    assert mgr._consecutive_otp_failures == 0
    assert policy.enabled is True, (
        "reset_for_manual_reconnect must re-enable pyquotex's supervisor"
    )
    assert relay_resets == [1], (
        f"relay.reset_exhaustion was not called; got {relay_resets}"
    )


@pytest.mark.asyncio
async def test_manager_reset_for_manual_reconnect_safe_when_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_client` may be None (first-ever connect, or post-disconnect)
    — `reset_for_manual_reconnect` must not crash."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )

    mgr = QuotexManager(root_path=".")
    mgr._consecutive_otp_failures = 3
    # `_client` is None by default — no setattr needed.

    # Must not raise.
    mgr.reset_for_manual_reconnect()
    assert mgr._consecutive_otp_failures == 0


def test_persisted_ssid_skips_otp_on_second_connect(client: TestClient) -> None:
    """End-to-end: first connect goes through OTP, second connect on
    the same manager reuses the saved session_data and SKIPS the
    on_otp_callback entirely. This is the production restart win the
    spec promises."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    store = _FakeSessionStore()
    manager.set_session_store(store)

    headers = _login(client)
    _put_credentials(client, headers)

    # ---------- First connect: OTP-required path ----------------------
    FakeQuotex.behavior = "needs_otp"
    client.post("/broker/connect", headers=headers)
    client.post("/broker/otp", headers=headers, json={"code": "654321"})
    import time  # noqa: PLC0415
    for _ in range(40):
        if manager.status().state == "connected":
            break
        time.sleep(0.05)
    assert manager.status().state == "connected"
    # Session got saved.
    assert len(store.saved_payloads) >= 1

    # Simulate a restart: disconnect, wipe the in-memory manager state
    # but keep the SessionStore (its primed_payload is the last save).
    primed = store.saved_payloads[-1]
    client.post("/broker/disconnect", headers=headers)
    for _ in range(20):
        if manager.status().state == "idle":
            break
        time.sleep(0.05)
    assert manager.status().state == "idle", (
        "Disconnect did not settle within 1s — "
        "second-connect cycle would test the wrong path"
    )

    # Build a NEW SessionStore primed with the previous save — this
    # is what a fresh container start sees when reading the on-disk file.
    primed_store = _FakeSessionStore(primed=primed)
    manager.set_session_store(primed_store)

    # ---------- Second connect: SSID-reuse path -----------------------
    FakeQuotex.behavior = "ok_no_otp"
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200, r.text
    for _ in range(40):
        if manager.status().state == "connected":
            break
        time.sleep(0.05)
    assert manager.status().state == "connected"
    # No new OTP cycle this time — but the save still ran (fresh
    # session_data refreshes the on-disk copy).
    assert len(primed_store.saved_payloads) >= 1


# ---------------------------------------------------------------------------
# Regression test: final holistic review I1
# ---------------------------------------------------------------------------


def test_manager_clears_session_store_after_rejected_connect_with_cached_session(
    client: TestClient,
) -> None:
    """REGRESSION (final-review I1): when the cached SSID is dead and
    the broker rejects, the manager must clear the on-disk cache so the
    next attempt does a fresh login instead of looping on the same dead
    token."""
    import time  # noqa: PLC0415

    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    primed = {
        "token": "expired-ssid",
        "cookies": "x",
        "user_agent": "x",
    }
    store = _FakeSessionStore(primed=primed)
    manager.set_session_store(store)

    headers = _login(client)
    _put_credentials(client, headers)

    FakeQuotex.behavior = "rejected"
    client.post("/broker/connect", headers=headers)

    # Wait for connect to settle to error.
    for _ in range(40):
        if manager.status().state == "error":
            break
        time.sleep(0.05)

    # The clear() call must have fired because we DID use a cached session.
    assert store.cleared_count == 1, (
        f"expected session_store.clear() to fire after rejected connect "
        f"with cached session; cleared_count={store.cleared_count}"
    )


# ---------------------------------------------------------------------------
# Asset-availability pre-flight (fix: 30s timeout-burn on unavailable assets)
# ---------------------------------------------------------------------------


class _StubManager:
    """Minimal QuotexManager stand-in for _asset_is_available unit tests.

    Only ``assets`` and ``refresh_assets`` are needed — the executor's
    pre-flight reads the cache then optionally calls refresh.
    """

    def __init__(self, assets: tuple[str, ...] = ()) -> None:
        self._assets = assets
        self.refresh_calls: int = 0
        # Simulated post-refresh universe (defaults to same as initial).
        self.refresh_result: tuple[str, ...] = assets
        self.raise_on_refresh: bool = False

    @property
    def assets(self) -> tuple[str, ...]:
        return self._assets

    async def refresh_assets(self) -> tuple[str, ...]:
        self.refresh_calls += 1
        if self.raise_on_refresh:
            raise RuntimeError("broker disconnected")
        return self.refresh_result

    # -- Stubs required only to construct TradeExecutor ------------------

    def status(self):  # type: ignore[return]
        class _S:
            account_mode = "PRACTICE"
        return _S()

    connected = True
    _client = None


async def test_executor_skips_unavailable_asset_after_refresh() -> None:
    """A signal for an asset absent from both the cached and refreshed
    universe must never reach client.buy() — it is marked broker_error
    with reason 'asset_not_available'."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    # Universe does NOT contain USDBRL_otc (either before or after refresh).
    mgr = _StubManager(assets=("EURUSD_otc", "GBPUSD_otc"))
    mgr.refresh_result = ("EURUSD_otc", "GBPUSD_otc")

    executor = TradeExecutor(
        manager=mgr,  # type: ignore[arg-type]
        live_trading_enabled_env=False,
    )
    available = await executor._asset_is_available("USDBRL_otc")

    # Pre-flight must return False and have tried one refresh.
    assert available is False, "expected False for asset absent from universe"
    assert mgr.refresh_calls == 1, "expected exactly one refresh attempt"


async def test_executor_fails_fast_when_asset_refresh_raises() -> None:
    """REGRESSION: _asset_is_available must return False when
    refresh_assets() raises — fail fast, do not optimistically proceed
    and burn 30s on the broker side. Locks the exception-branch
    contract against silent regressions."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    manager = _StubManager(assets=("EURUSD_otc",))  # asset of interest is NOT here
    manager.raise_on_refresh = True

    executor = TradeExecutor.__new__(TradeExecutor)  # bypass full ctor
    executor._manager = manager  # type: ignore[attr-defined]

    available = await executor._asset_is_available("USDBRL_otc")
    assert available is False
    assert manager.refresh_calls == 1  # refresh attempted exactly once


async def test_executor_proceeds_when_asset_is_available() -> None:
    """A signal for an asset that IS in the cached universe must pass the
    pre-flight check without touching refresh_assets."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    mgr = _StubManager(assets=("EURUSD_otc", "GBPUSD_otc", "USDBRL_otc"))

    executor = TradeExecutor(
        manager=mgr,  # type: ignore[arg-type]
        live_trading_enabled_env=False,
    )
    available = await executor._asset_is_available("USDBRL_otc")

    assert available is True, "expected True for asset present in universe"
    # Cache hit — refresh must NOT have been called.
    assert mgr.refresh_calls == 0, "unexpected refresh for a cache-hit asset"


# ---------------------------------------------------------------------------
# "Did you mean?" inverse-pair swap suggestion
# ---------------------------------------------------------------------------


def test_inverse_currency_pair_helper() -> None:
    """Verifies the inverse helper handles common shapes."""
    from autotrader.services.executor import _inverse_currency_pair  # noqa: PLC0415

    assert _inverse_currency_pair("USDBRL_otc") == "BRLUSD_otc"
    assert _inverse_currency_pair("EURUSD") == "USDEUR"
    assert _inverse_currency_pair("USDBRL") == "BRLUSD"
    # Non-6-letter shapes return None.
    assert _inverse_currency_pair("BTC_otc") is None
    assert _inverse_currency_pair("XAUUSD3_otc") is None
    assert _inverse_currency_pair("") is None
    # Non-alpha bodies return None.
    assert _inverse_currency_pair("USD123") is None


@pytest.mark.asyncio
async def test_executor_emits_swap_suggestion_when_inverse_exists() -> None:
    """REGRESSION: when an asset is missing but its inverse-ordering is
    in the broker's universe (e.g., user sends USDBRL_otc but broker has
    BRLUSD_otc), the executor must emit a system.error event suggesting
    the operator update their config — without auto-swapping the trade."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    # Capture published events.
    captured: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, event_type: str, payload: dict) -> None:
            captured.append((event_type, payload))

    manager = _StubManager(assets=("BRLUSD_otc", "EURUSD_otc"))  # inverse IS present
    manager.refresh_result = manager._assets  # refresh returns same set

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = manager  # type: ignore[attr-defined]
    executor._event_bus = _SpyBus()  # type: ignore[attr-defined]

    executor._maybe_emit_swap_suggestion("USDBRL_otc", "call")

    assert len(captured) == 1
    event_type, payload = captured[0]
    assert event_type == "system.error"
    assert payload["kind"] == "asset_not_available.suggested_swap"
    # Detail mentions both symbols and the direction flip.
    detail = payload["detail"]
    assert "BRLUSD_otc" in detail
    assert "USDBRL_otc" in detail
    assert "put" in detail  # flipped from call


@pytest.mark.asyncio
async def test_executor_skips_swap_suggestion_when_inverse_missing() -> None:
    """When the inverse pair is ALSO not in the broker's universe,
    no swap suggestion is emitted — the asset is genuinely unknown."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    captured: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, event_type: str, payload: dict) -> None:
            captured.append((event_type, payload))

    manager = _StubManager(assets=("EURUSD_otc",))  # no BRLUSD, no USDBRL
    manager.refresh_result = manager._assets

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = manager  # type: ignore[attr-defined]
    executor._event_bus = _SpyBus()  # type: ignore[attr-defined]

    executor._maybe_emit_swap_suggestion("USDBRL_otc", "call")

    assert captured == []


@pytest.mark.asyncio
async def test_executor_swap_suggestion_uses_case_fold_match() -> None:
    """REGRESSION: when the broker carries the inverse pair with a casing
    quirk (e.g. ``BrlUsd_otc`` instead of ``BRLUSD_otc``), the swap
    suggestion must still fire AND the alert detail must name the broker's
    actual casing so the operator copies it correctly into their parser
    config. Locks down case-fold parity with ``_maybe_emit_ticker_suggestion``."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    captured: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, event_type: str, payload: dict) -> None:
            captured.append((event_type, payload))

    # Broker carries the inverse with a case-quirk — uppercase symbol would
    # miss on literal `in`, must hit via case-fold.
    manager = _StubManager(assets=("BrlUsd_otc",))
    manager.refresh_result = manager._assets

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = manager  # type: ignore[attr-defined]
    executor._event_bus = _SpyBus()  # type: ignore[attr-defined]

    emitted = executor._maybe_emit_swap_suggestion("USDBRL_otc", "call")

    assert emitted is True, "swap suggestion must fire on case-fold inverse hit"
    assert len(captured) == 1
    _, payload = captured[0]
    # Detail names the broker's casing, not the upper-cased synthetic inverse.
    assert "BrlUsd_otc" in payload["detail"]
    assert "BRLUSD_otc" not in payload["detail"]


# ---------------------------------------------------------------------------
# Case-insensitive auto-fix + ticker-alias "did you mean?" suggestion
# (USCRUDE→USCrude case-fold, RIPPLE→XRPUSD ticker alias, etc.)
# ---------------------------------------------------------------------------


async def test_executor_case_corrects_asset() -> None:
    """A case-mismatched signal (USCRUDE_otc) must resolve to the broker's
    canonical casing (USCrude_otc) on a cache hit — no refresh required."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    mgr = _StubManager(assets=("USCrude_otc",))

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = mgr  # type: ignore[attr-defined]
    executor._event_bus = None  # type: ignore[attr-defined]

    resolved = await executor._resolve_asset("USCRUDE_otc")

    assert resolved == "USCrude_otc"
    # Case-fold cache hit — refresh must NOT have fired.
    assert mgr.refresh_calls == 0, "case-fold hit must not refresh"


async def test_resolve_asset_returns_literal_when_exact() -> None:
    """Literal cache hit: return the asset unchanged, never touch refresh."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    mgr = _StubManager(assets=("EURUSD_otc",))

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = mgr  # type: ignore[attr-defined]
    executor._event_bus = None  # type: ignore[attr-defined]

    resolved = await executor._resolve_asset("EURUSD_otc")

    assert resolved == "EURUSD_otc"
    assert mgr.refresh_calls == 0


async def test_resolve_asset_returns_none_when_truly_missing() -> None:
    """Asset absent before AND after refresh — return None so the caller
    rejects the trade. Refresh must have been attempted exactly once."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    mgr = _StubManager(assets=("EURUSD_otc",))
    mgr.refresh_result = ("EURUSD_otc",)  # still missing after refresh

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = mgr  # type: ignore[attr-defined]
    executor._event_bus = None  # type: ignore[attr-defined]

    resolved = await executor._resolve_asset("USDCOP_otc")

    assert resolved is None
    assert mgr.refresh_calls == 1


@pytest.mark.asyncio
async def test_executor_emits_ticker_suggestion_for_known_alias() -> None:
    """RIPPLE_otc with broker streaming XRPUSD_otc → emit a
    ``asset_not_available.ticker_suggestion`` event naming both symbols.
    Trade still rejects — operator must update parser config deliberately."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    captured: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, event_type: str, payload: dict) -> None:
            captured.append((event_type, payload))

    manager = _StubManager(assets=("XRPUSD_otc",))
    manager.refresh_result = manager._assets

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = manager  # type: ignore[attr-defined]
    executor._event_bus = _SpyBus()  # type: ignore[attr-defined]

    emitted = executor._maybe_emit_ticker_suggestion("RIPPLE_otc", "call")

    assert emitted is True
    assert len(captured) == 1
    event_type, payload = captured[0]
    assert event_type == "system.error"
    assert payload["kind"] == "asset_not_available.ticker_suggestion"
    detail = payload["detail"]
    assert "RIPPLE_otc" in detail
    assert "XRPUSD_otc" in detail


@pytest.mark.asyncio
async def test_executor_no_ticker_suggestion_when_alias_unknown() -> None:
    """USDCOP_otc has no entry in the alias map — silently do nothing.
    The catalog-sample fallback log handles this case at the call site."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    captured: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, event_type: str, payload: dict) -> None:
            captured.append((event_type, payload))

    manager = _StubManager(assets=("EURUSD_otc",))
    manager.refresh_result = manager._assets

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = manager  # type: ignore[attr-defined]
    executor._event_bus = _SpyBus()  # type: ignore[attr-defined]

    emitted = executor._maybe_emit_ticker_suggestion("USDCOP_otc", "call")

    assert emitted is False
    assert captured == []


@pytest.mark.asyncio
async def test_executor_no_ticker_suggestion_when_alias_target_missing_from_catalog(
) -> None:
    """Alias is known (RIPPLE→XRPUSD) but XRPUSD_otc is ALSO absent from
    the broker's universe — don't emit a misleading suggestion. The
    asset is genuinely unrecognized (catalog-sample log handles it)."""
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    captured: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, event_type: str, payload: dict) -> None:
            captured.append((event_type, payload))

    manager = _StubManager(assets=("EURUSD_otc",))  # XRPUSD_otc absent
    manager.refresh_result = manager._assets

    executor = TradeExecutor.__new__(TradeExecutor)
    executor._manager = manager  # type: ignore[attr-defined]
    executor._event_bus = _SpyBus()  # type: ignore[attr-defined]

    emitted = executor._maybe_emit_ticker_suggestion("RIPPLE_otc", "call")

    assert emitted is False
    assert captured == []
