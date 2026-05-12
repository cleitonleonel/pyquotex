"""Long-lived wrapper around a single ``pyquotex.stable_api.Quotex`` client.

The manager is the only thing in the app that talks to pyquotex
directly. Everything else (HTTP routers, the future Telegram pipeline,
the trade executor) goes through it. Keeping this surface narrow gives
us a single place to:

* serialise login / logout (so two requests can't race into duplicate
  Quotex sessions),
* enforce the REAL-trading gate from ``settings.live_trading_enabled``,
* handle the broker's OTP / 2FA challenge as a non-blocking state
  machine — the connect coroutine runs in the background and parks on
  an ``asyncio.Future`` while the user types the code into the UI,
* expose a tiny, stable status snapshot to the rest of the app,
* pre-warm the connection at app startup so the first trade pays no
  login cost,
* **observe** pyquotex's auto-reconnect supervisor and surface those
  transitions to the rest of the app — the previous version trusted
  pyquotex to handle everything internally and silently degraded for
  the few seconds it took to swap sockets, which is unacceptable for
  a real-money trading bot. See :meth:`_status_watcher` below.

pyquotex itself owns the *mechanism* of reconnecting (TCP reset, fresh
WS handshake, replay of subscriptions). The manager owns the *policy*
of how aggressive that should be and the *observability* of when it's
happening.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from pyquotex.global_value import AuthStatus, WebsocketStatus
from pyquotex.stable_api import Quotex
from pyquotex.utils.account_type import AccountType
from pyquotex.utils.proxy_config import ProxyConfig
from pyquotex.utils.reconnect import ReconnectPolicy

from autotrader.config import settings
from autotrader.models.base import utc_now

log = structlog.get_logger(__name__)

AccountMode = Literal["PRACTICE", "REAL"]
ConnectState = Literal[
    "idle",
    "connecting",
    "awaiting_otp",
    "connected",
    "reconnecting",  # WS dropped after first connect, supervisor is retrying
    "error",
]

# How long the manager waits for the user to type an OTP before giving
# up and tearing the in-flight connect down. Three minutes covers slow
# email delivery / SMS without keeping a stale connect task forever.
_OTP_TIMEOUT_SECONDS = 180

# Reconnect policy passed into pyquotex. Defaults there (1s → 60s,
# unlimited attempts) are tuned for a long-running data scraper; for
# binary-options trading where a missed signal is a missed dollar, we
# want the first retry near-instant and the backoff capped well below
# a single 60s option duration.
_MANAGER_RECONNECT_POLICY = ReconnectPolicy(
    enabled=True,
    max_attempts=-1,         # never give up — operator decides via UI
    initial_delay=0.5,       # first retry inside half a second
    max_delay=15.0,          # cap at 15s — shorter than a 1-min option
    backoff_factor=2.0,
    jitter=0.25,
)

# Status-watcher poll cadence. 100ms is well below human-perception
# latency for "Connected → Reconnecting" UI flips and small enough
# that the risk gate never ships a trade more than ~100ms after the
# socket died. Cheap — just two int comparisons per tick.
_STATUS_POLL_INTERVAL = 0.1


class QuotexManagerError(Exception):
    """Raised for caller-visible failures (auth rejected, REAL gate, etc.)."""


@dataclass(frozen=True, slots=True)
class BrokerStatus:
    """Public snapshot — safe to serialise to the dashboard."""

    configured: bool          # credentials are stored
    connected: bool           # WebSocket is live
    state: ConnectState       # finer-grained machine state
    awaiting_otp: bool        # convenience for the UI
    otp_prompt: str | None    # the prompt pyquotex passed to the callback
    email_masked: str | None  # "j***@example.com" or None
    account_mode: AccountMode
    connected_at: datetime | None
    last_error: str | None


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    name, _, domain = email.partition("@")
    if len(name) <= 1:
        return f"{name}***@{domain}"
    return f"{name[0]}***@{domain}"


class QuotexManager:
    """Single warm Quotex client, async-safe."""

    def __init__(
        self,
        *,
        root_path: str = ".",
        event_bus: Any | None = None,
    ) -> None:
        self._root_path = root_path
        Path(root_path).mkdir(parents=True, exist_ok=True)

        self._event_bus = event_bus
        self._client: Quotex | None = None
        self._email: str | None = None
        self._password: str | None = None
        self._account_mode: AccountMode = "PRACTICE"
        self._connected_at: datetime | None = None
        self._last_error: str | None = None

        # Connect state machine.
        self._state: ConnectState = "idle"
        self._connect_task: asyncio.Task[None] | None = None
        self._otp_future: asyncio.Future[str] | None = None
        self._otp_prompt: str | None = None

        # Resilience watcher — runs once a session is established and
        # mirrors the underlying WS state (which pyquotex's supervisor
        # mutates on its own clock) into our state machine. ``None``
        # while we're idle/connecting/error; populated on the way out
        # of ``_do_connect`` if the login succeeded.
        self._status_watcher_task: asyncio.Task[None] | None = None
        self._disconnected_at: datetime | None = None
        self._consecutive_failed_reconnects: int = 0

        # Broker asset codes (e.g. "EURUSD", "EURUSD_otc"). Populated
        # on each successful connect and refreshable via
        # ``refresh_assets``. The parser layer reads this to auto-
        # resolve channel-side asset names to broker codes.
        self._assets: tuple[str, ...] = ()

        # Serialises the actual login round-trip. Two ``begin_connect``
        # calls are coalesced via the ``_state`` check before this lock
        # is reached.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._email is not None and self._password is not None

    @property
    def connected(self) -> bool:
        """Whether the broker WS is *actually* live and authorised.

        Previously this only checked Python-object existence
        (``self._client.api is not None``), which stayed ``True`` for
        the rest of the process after the first successful login —
        even when the WebSocket had been dead for minutes. The risk
        gate keys on this flag, so every trade attempted during a
        reconnect window was firing into a closed pipe and timing
        out at ``confirm_timeout`` (~10s). Now we mirror the real
        state of the underlying socket so the gate blocks cleanly.
        """
        if self._client is None or self._client.api is None:
            return False
        state = getattr(self._client.api, "state", None)
        if state is None:
            return False
        return (
            state.status == WebsocketStatus.CONNECTED
            and state.auth_status == AuthStatus.AUTHENTICATED
        )

    def status(self) -> BrokerStatus:
        return BrokerStatus(
            configured=self.configured,
            connected=self.connected,
            state=self._state,
            awaiting_otp=self._state == "awaiting_otp",
            otp_prompt=self._otp_prompt if self._state == "awaiting_otp" else None,
            email_masked=_mask_email(self._email) if self._email else None,
            account_mode=self._account_mode,
            connected_at=self._connected_at,
            last_error=self._last_error,
        )

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def set_credentials(
        self,
        email: str,
        password: str,
        account_mode: AccountMode = "PRACTICE",
    ) -> None:
        self._email = email
        self._password = password
        self._account_mode = account_mode

    def clear_credentials(self) -> None:
        self._email = None
        self._password = None

    # ------------------------------------------------------------------
    # Connect lifecycle (state-machine, non-blocking)
    # ------------------------------------------------------------------

    def begin_connect(self) -> None:
        """Schedule a connect attempt. Idempotent — no-ops if already
        in flight or already connected.

        Sync on purpose: callers (HTTP handlers) shouldn't await this.
        They should kick it off and then poll ``status()`` (or wait
        briefly for it to settle on the no-OTP fast path).
        """
        if self._state in ("connecting", "awaiting_otp"):
            return
        if self.connected:
            self._state = "connected"
            return
        if not self.configured:
            self._last_error = "credentials not set"
            self._state = "error"
            return
        # Validate the REAL gate on the calling task — the failure
        # surface is much better here than from inside the background.
        self._enforce_live_gate(self._account_mode)

        self._state = "connecting"
        self._last_error = None
        self._connect_task = asyncio.create_task(self._do_connect())

    async def wait_settled(self, timeout: float) -> None:  # noqa: ASYNC109  (custom poll, not asyncio.timeout)
        """Best-effort wait for the connect task to leave the transient
        ``connecting`` state. Used by the HTTP layer so non-OTP
        connects feel synchronous to the caller.

        Returns silently when the deadline expires — the caller should
        then read ``status()`` to decide what to do.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._state == "connecting":
            if loop.time() >= deadline:
                return
            await asyncio.sleep(0.025)

    async def _do_connect(self) -> None:
        async with self._lock:
            try:
                assert self._email is not None
                assert self._password is not None

                client = Quotex(
                    email=self._email,
                    password=self._password,
                    root_path=self._root_path,
                    lang="en",
                    on_otp_callback=self._on_otp_callback,
                    # Trading-tuned reconnect policy — see the
                    # ``_MANAGER_RECONNECT_POLICY`` constant for the
                    # rationale on the timing constants.
                    reconnect_policy=_MANAGER_RECONNECT_POLICY,
                    # Quotex's ``qxbroker.com`` login page is behind
                    # Cloudflare and 403s plain ``httpx`` because the
                    # TLS / JA3 fingerprint isn't a real browser. We
                    # let ``curl_cffi`` impersonate a real browser via
                    # ``use_browser_tls=True``; pyquotex flips to that
                    # backend automatically.
                    #
                    # Profile choice: ``firefox144``. Cloudflare's bot
                    # scoring rotates which JA3s it currently trusts;
                    # as of May 2026 every Chrome variant curl_cffi 0.15
                    # ships (chrome120…chrome146) is being challenged on
                    # qxbroker.com, while Firefox 144 passes cleanly.
                    # Firefox also avoids the ``Sec-Ch-Ua`` header trio
                    # entirely (it's Chrome-only) — fewer cross-checks
                    # to keep in lockstep with the wire fingerprint.
                    # If this stops working, sweep impersonate profiles
                    # against ``/en/sign-in`` and pick a passing one.
                    proxy_config=ProxyConfig(
                        use_browser_tls=True,
                        impersonate="firefox144",
                    ),
                )
                client.set_account_mode(self._account_mode)
                ok, reason = await client.connect()
            except asyncio.CancelledError:
                # User-initiated cancel. ``cancel_connect`` is the
                # one that resolves the final state — we just bail
                # cleanly so the lock unwinds.
                self._reset_otp()
                log.info("broker.connect.cancelled")
                raise
            except Exception as exc:
                self._state = "error"
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._reset_otp()
                log.exception("broker.connect.error")
                return

            if ok:
                self._client = client
                self._connected_at = utc_now()
                self._state = "connected"
                self._last_error = None
                self._consecutive_failed_reconnects = 0
                log.info(
                    "broker.connect.ok",
                    email_masked=_mask_email(self._email or ""),
                    account_mode=self._account_mode,
                )
                # Spawn the resilience watcher. It observes pyquotex's
                # internal WS state (which the supervisor mutates on
                # disconnect / reconnect) and mirrors it into our
                # state machine + admin notifications.
                self._start_status_watcher()
                # Best-effort: fetch the asset universe so the parser
                # layer can auto-resolve channel-side names to broker
                # codes. Failure here doesn't fail the connect.
                try:
                    await self._refresh_assets_locked()
                except Exception as exc:  # pragma: no cover - depends on broker
                    log.warning("broker.assets.refresh_failed", error=str(exc))
            else:
                self._state = "error"
                self._last_error = reason
                log.warning("broker.connect.rejected", reason=reason)
                self._emit_system_error(kind="connect.rejected", detail=reason)
            self._reset_otp()

    async def disconnect(self) -> None:
        # Kill any in-flight connect first so it can't race against
        # us into "connected" right after we've torn down.
        await self.cancel_connect()
        # Stop the resilience watcher *before* taking the lock — the
        # watcher itself may briefly contend on it via callbacks and
        # we want a clean cancel before the client object disappears.
        await self._stop_status_watcher()
        async with self._lock:
            if self._client is None:
                self._state = "idle"
                return
            try:
                await self._client.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                log.warning("broker.disconnect.error", error=str(exc))
                self._emit_system_error(kind="disconnect.error", detail=str(exc))
            finally:
                self._client = None
                self._connected_at = None
                self._disconnected_at = None
                self._consecutive_failed_reconnects = 0
                self._state = "idle"
                log.info("broker.disconnect.ok")

    # ------------------------------------------------------------------
    # Resilience watcher
    # ------------------------------------------------------------------

    def _start_status_watcher(self) -> None:
        """Spawn the WS-state observer if not already running."""
        if (
            self._status_watcher_task is not None
            and not self._status_watcher_task.done()
        ):
            return
        self._status_watcher_task = asyncio.create_task(self._status_watcher())

    async def _stop_status_watcher(self) -> None:
        task = self._status_watcher_task
        if task is None or task.done():
            self._status_watcher_task = None
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        self._status_watcher_task = None

    async def _status_watcher(self) -> None:
        """Mirror the underlying WS health into our state machine.

        Polls ``client.api.state.status`` + ``auth_status``. Pyquotex's
        ``ReconnectSupervisor`` mutates these on its own clock — we
        just *observe* them and:

        * On a CONNECTED → not-CONNECTED transition: flip the manager
          to ``reconnecting``, capture ``_disconnected_at`` for the
          downtime tally, and publish a ``system.error`` so the admin
          bot can notify the operator. This is the loud signal that
          used to be missing.
        * On not-CONNECTED → CONNECTED + AUTHENTICATED: flip back to
          ``connected``, log the downtime, and reset the failed-
          reconnect counter. Currently silent on success — see the
          escalation hook in :meth:`_on_reconnect_attempt_failed` for
          the noisy-vs-quiet policy.
        * While stuck in ``reconnecting``, track the supervisor's
          ``failed_reconnects`` count and escalate if it climbs above
          a configured threshold (the operator should know if we've
          been down for minutes, not silently bleed signals).

        The loop runs until cancelled by :meth:`disconnect` or until
        the underlying client object disappears.
        """
        last_was_connected = True
        last_failed_reconnects = self._supervisor_failed_count()
        try:
            while True:
                await asyncio.sleep(_STATUS_POLL_INTERVAL)

                client = self._client
                if client is None or client.api is None:
                    return

                state = getattr(client.api, "state", None)
                if state is None:
                    continue

                now_connected = (
                    state.status == WebsocketStatus.CONNECTED
                    and state.auth_status == AuthStatus.AUTHENTICATED
                )

                if last_was_connected and not now_connected:
                    self._on_ws_dropped(state)
                elif not last_was_connected and now_connected:
                    self._on_ws_recovered()

                # Watch the supervisor's failed-attempt counter so the
                # admin bot can be told that we've been stuck — this is
                # independent of the per-transition events above.
                failed_now = self._supervisor_failed_count()
                if failed_now > last_failed_reconnects:
                    self._consecutive_failed_reconnects = failed_now
                    self._on_reconnect_attempt_failed(failed_now)
                last_failed_reconnects = failed_now

                last_was_connected = now_connected
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover — watcher must not die silently
            log.exception("broker.status_watcher.crashed", error=str(exc))
            self._emit_system_error(
                kind="status_watcher.crashed",
                detail=str(exc),
                recoverable=False,
            )

    def _supervisor_failed_count(self) -> int:
        client = self._client
        if client is None or client.api is None:
            return 0
        supervisor = getattr(client.api, "reconnect_supervisor", None)
        if supervisor is None:
            return 0
        stats = getattr(supervisor, "stats", None)
        return int(getattr(stats, "failed_reconnects", 0) or 0)

    def _on_ws_dropped(self, state: Any) -> None:
        """Called once per CONNECTED → not-CONNECTED edge."""
        self._disconnected_at = utc_now()
        self._state = "reconnecting"
        reason = (
            getattr(state, "websocket_error_reason", None)
            or f"status={int(state.status)} auth={int(state.auth_status)}"
        )
        log.warning("broker.ws.dropped", reason=reason)
        self._emit_system_error(
            kind="broker.disconnected",
            detail=reason,
            recoverable=True,
        )

    def _on_ws_recovered(self) -> None:
        """Called once per not-CONNECTED → CONNECTED+AUTHENTICATED edge."""
        downtime_s: float | None = None
        if self._disconnected_at is not None:
            downtime_s = (utc_now() - self._disconnected_at).total_seconds()
        self._disconnected_at = None
        self._state = "connected"
        self._last_error = None
        self._consecutive_failed_reconnects = 0
        log.info(
            "broker.ws.recovered",
            downtime_s=round(downtime_s, 2) if downtime_s is not None else None,
        )

    # When the supervisor has racked up this many failed reconnect
    # attempts in a row, the watcher flips the event's ``recoverable``
    # flag to ``False`` so the admin notifier formats it as a hard
    # outage rather than a transient blip. Keeping recoverable=True
    # before this lets the operator tell apart "we're working it" from
    # "this isn't going to fix itself."
    _HARD_OUTAGE_AFTER_ATTEMPTS = 10

    def _on_reconnect_attempt_failed(self, failed_count: int) -> None:
        """Called each time pyquotex's supervisor counts a failed retry.

        **Policy: LOUD** (chosen for real-money trading, 2026-05-12).

        Every failed reconnect attempt past the initial drop fires a
        ``broker.recover_stalled`` event onto the bus. The initial drop
        itself is already announced by :meth:`_on_ws_dropped` as
        ``broker.disconnected``, so the operator sees a clean two-event
        sequence per outage:

          1. ``broker.disconnected`` — "WS just died, supervisor taking over"
          2. ``broker.recover_stalled`` (1..N) — "retry #N failed, still trying"

        Trade-off acknowledged: this maximises visibility at the cost
        of inbox noise. The ``admin_bot_notify`` layer already
        suppresses bursts via its own backoff (see
        ``admin_bot_notify.py:_backoff_state``), so the wire-level
        Telegram message count is bounded even when the bus is loud.
        Picked over the quieter alternatives because the cost of a
        silent gap during real-money trading is strictly worse than
        the cost of a few extra pings.
        """
        # Past ``_HARD_OUTAGE_AFTER_ATTEMPTS`` failures, downgrade
        # ``recoverable`` so the admin bot formats this as an outage
        # rather than a transient hiccup. The supervisor keeps trying
        # regardless — this only affects the operator-facing tone.
        is_hard_outage = failed_count >= self._HARD_OUTAGE_AFTER_ATTEMPTS
        self._emit_system_error(
            kind="broker.recover_stalled",
            detail=f"reconnect attempt {failed_count} failed; still trying",
            recoverable=not is_hard_outage,
        )

    async def cancel_connect(self) -> None:
        """Abort an in-flight connect (e.g. user closed the OTP dialog).

        Force-resets to ``idle`` and clears ``last_error`` so the cancel
        is a clean reset rather than presenting as a connect failure.
        """
        if self._otp_future is not None and not self._otp_future.done():
            self._otp_future.cancel()
        task = self._connect_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._connect_task = None
        if self._state in ("connecting", "awaiting_otp"):
            self._state = "idle"
            self._last_error = None
        self._reset_otp()

    # ------------------------------------------------------------------
    # OTP / 2FA
    # ------------------------------------------------------------------

    async def _on_otp_callback(self, prompt: str) -> str:
        """Hook pyquotex calls when the broker challenges for a code.

        Parks the connect coroutine on a future that ``submit_otp``
        resolves. pyquotex passes the resulting string straight to the
        login form; if the broker rejects it, ``connect()`` returns
        ``(False, ...)`` and the manager state moves to ``error``.
        """
        loop = asyncio.get_running_loop()
        self._otp_future = loop.create_future()
        self._otp_prompt = prompt.strip() or "Enter the code sent to your email."
        self._state = "awaiting_otp"
        log.info("broker.otp.prompted", prompt=self._otp_prompt[:80])
        try:
            return await asyncio.wait_for(self._otp_future, timeout=_OTP_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            self._last_error = "OTP timed out"
            raise
        finally:
            self._reset_otp(keep_state=True)

    async def submit_otp(self, code: str) -> None:
        if self._otp_future is None or self._otp_future.done():
            raise QuotexManagerError("not waiting for an OTP")
        code = code.strip()
        if not code:
            raise QuotexManagerError("empty OTP")
        self._otp_future.set_result(code)
        # Optimistic flip; will go back to awaiting_otp if pyquotex
        # invokes the callback again (e.g. invalid code).
        self._state = "connecting"
        log.info("broker.otp.submitted")

    def _reset_otp(self, *, keep_state: bool = False) -> None:
        self._otp_future = None
        self._otp_prompt = None
        if not keep_state and self._state == "awaiting_otp":
            self._state = "idle"

    # ------------------------------------------------------------------
    # Event bus
    # ------------------------------------------------------------------

    def _emit_system_error(
        self,
        *,
        kind: str,
        detail: str,
        recoverable: bool = True,
    ) -> None:
        """Publish a ``system.error`` event for the admin notifier."""
        if self._event_bus is None:
            return
        self._event_bus.publish("system.error", {
            "component": "broker",
            "kind": kind,
            "detail": detail,
            "recoverable": recoverable,
        })

    # ------------------------------------------------------------------
    # Account mode
    # ------------------------------------------------------------------

    async def set_account_mode(self, mode: AccountMode) -> None:
        """Hot-swap accounts. Hits the broker only when connected."""
        self._enforce_live_gate(mode)

        async with self._lock:
            self._account_mode = mode
            if self._client is None:
                return
            try:
                await self._client.change_account(mode, tournament_id=0)
            except Exception as exc:
                self._last_error = f"change_account: {exc}"
                log.error("broker.account_mode.failed", mode=mode, error=str(exc))
                self._emit_system_error(kind="account_mode.failed", detail=str(exc))
                raise QuotexManagerError(self._last_error) from exc
            log.info("broker.account_mode.ok", mode=mode)

    @staticmethod
    def _enforce_live_gate(mode: AccountMode) -> None:
        if mode == "REAL" and not settings.live_trading_enabled:
            raise QuotexManagerError(
                "real-money trading is disabled — set "
                "AUTOTRADER_LIVE_TRADING_ENABLED=true to enable",
            )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def get_balance(self, timeout: int = 10) -> float:  # noqa: ASYNC109  (forwarded to pyquotex)
        if self._client is None:
            raise QuotexManagerError("not connected")
        try:
            return await self._client.get_balance(timeout=timeout)
        except Exception as exc:
            self._last_error = f"get_balance: {exc}"
            log.warning("broker.balance.failed", error=str(exc))
            self._emit_system_error(kind="balance.failed", detail=str(exc))
            raise QuotexManagerError(self._last_error) from exc

    @property
    def account_type_int(self) -> int:
        """Underlying ``AccountType`` int (0=REAL, 1=DEMO)."""
        return AccountType.DEMO if self._account_mode == "PRACTICE" else AccountType.REAL

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    @property
    def assets(self) -> tuple[str, ...]:
        """Snapshot of broker asset codes (empty until first connect)."""
        return self._assets

    async def refresh_assets(self) -> tuple[str, ...]:
        """Force-refresh the asset cache. Returns the new snapshot."""
        if self._client is None:
            raise QuotexManagerError("not connected")
        async with self._lock:
            await self._refresh_assets_locked()
        return self._assets

    async def _refresh_assets_locked(self) -> None:
        """Caller must hold ``self._lock``."""
        if self._client is None:
            return
        try:
            mapping = await self._client.get_all_assets()
        except Exception:
            raise
        # ``get_all_assets`` returns ``{display_name: code}``; we want
        # the code side.
        codes = sorted({str(c).strip() for c in (mapping or {}).values() if c})
        self._assets = tuple(codes)
        log.info("broker.assets.refreshed", count=len(codes))
