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
import html
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import curl_cffi.requests as _curl_requests
import structlog
from pyquotex.global_value import AuthStatus, WebsocketStatus
from pyquotex.stable_api import Quotex
from pyquotex.utils.proxy_config import ProxyConfig
from pyquotex.utils.reconnect import ReconnectPolicy

from autotrader.config import settings
from autotrader.models.base import utc_now

log = structlog.get_logger(__name__)


def _curl_get(url: str, *, impersonate: str, timeout: float) -> Any:
    """Module-level indirection so tests can monkeypatch the HTTP
    call without mocking ``curl_cffi.requests.get`` globally.

    Note: ``curl_cffi.requests.get`` constructs and tears down a
    one-shot ``Session`` per call — fine for a single pre-flight
    probe, but a caller must not put this in a hot loop.
    """
    return _curl_requests.get(url, impersonate=impersonate, timeout=timeout)

AccountMode = Literal["PRACTICE", "REAL"]
ConnectState = Literal[
    "idle",
    "connecting",
    "awaiting_otp",
    "connected",
    "reconnecting",  # WS dropped after first connect, supervisor is retrying
    "awaiting_manual_recovery",  # OTP cap exhausted; operator must run /reconnect
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


class BrokerNotLive(RuntimeError):  # noqa: N818 — intentional name (spec §3.5 interface)
    """Raised by :meth:`QuotexManager.assert_live` when the broker WS
    is not in a state fit to accept an order.

    Attributes
    ----------
    reason : str
        One of ``"not_connected"``, ``"ws_not_authed"``,
        ``"no_tick_seen"``, ``"stale_feed"``.
    detail : dict
        Extra context (e.g. ``age_seconds``, ``threshold``, ``asset``,
        ``state``).  Always a ``dict`` — never ``None``.
    """

    def __init__(self, reason: str, **detail: object) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail: dict[str, object] = dict(detail)


class BrokerPreflightFailed(RuntimeError):
    """Raised by :meth:`QuotexManager._preflight_check` when the
    broker sign-in page returns a hard error (Cloudflare 403,
    upstream 5xx) before pyquotex even tries to connect. Caught
    by ``_do_connect:SETUP`` and converted to ``last_error``.
    """


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
        self._ceiling_halt_task: asyncio.Task[None] | None = None
        self._disconnected_at: datetime | None = None
        self._consecutive_failed_reconnects: int = 0

        # Session persistence. When attached (typically by lifespan),
        # we hydrate ``client.session_data`` from this before calling
        # ``client.connect()`` and push the (now-warm) session back
        # into this after a successful connect. Until attached, both
        # operations are no-ops — the manager works fine without
        # persistence, just at the cost of an extra HTTP login per
        # restart.
        self._session_store: Any | None = None

        # OTP relay. When attached (by lifespan), receives direct
        # calls at the start, timeout, and resolved branches of the
        # OTP cycle. Until attached, the manager's existing UI-side
        # ``awaiting_otp`` state is the sole surface (the dashboard
        # works without the relay).
        self._otp_relay: Any | None = None
        # Per-cycle attempt counter. Reset to 0 on every successful
        # connect; incremented on each ``_on_otp_callback`` entry.
        self._otp_attempt: int = 0
        # Distinct from ``_otp_attempt``: this counts CONSECUTIVE OTP
        # FAILURES (timeouts / cancels) across pyquotex's internal
        # ReconnectSupervisor retries. Pyquotex's supervisor has
        # ``max_attempts=-1`` (never give up), so without an explicit
        # gate here it regenerates a PIN email forever — see
        # production incident 2026-05-12 (5 PIN emails in 13min).
        # When this reaches ``settings.otp_max_attempts`` we halt the
        # supervisor (``policy.enabled = False``) and require manual
        # /reconnect from the operator.
        self._consecutive_otp_failures: int = 0

        # Broker asset codes (e.g. "EURUSD", "EURUSD_otc"). Populated
        # on each successful connect and refreshable via
        # ``refresh_assets``. The parser layer reads this to auto-
        # resolve channel-side asset names to broker codes.
        self._assets: tuple[str, ...] = ()

        # Serialises the actual login round-trip. Two ``begin_connect``
        # calls are coalesced via the ``_state`` check before this lock
        # is reached.
        self._lock = asyncio.Lock()

    # Phase 0 instrumentation threshold (audit 2026-05-13, H2). Awaits
    # longer than this are logged as ``broker.lock.contention`` so we
    # can measure the real-world blast radius of the wide ``_do_connect``
    # critical section before splitting it in Phase 3a.
    _LOCK_CONTENTION_THRESHOLD_SECONDS: float = 0.5

    @contextlib.asynccontextmanager
    async def _timed_lock(self, site: str) -> AsyncIterator[None]:
        """``async with self._timed_lock("site"):`` — same semantics as
        ``async with self._lock:`` plus a structured-log warning when
        the wait to acquire exceeds
        :attr:`_LOCK_CONTENTION_THRESHOLD_SECONDS`.

        ``site`` is a short, stable string (the calling method) so a
        contention log line tells the operator *which* lock-holder is
        the slow one. Pure observability — never raises and never
        changes lock semantics.
        """
        start = time.monotonic()
        async with self._lock:
            waited_s = time.monotonic() - start
            if waited_s >= self._LOCK_CONTENTION_THRESHOLD_SECONDS:
                log.warning(
                    "broker.lock.contention",
                    site=site,
                    waited_ms=int(waited_s * 1000),
                )
            yield

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
        # Phase 1 (audit 2026-05-13, L7): the OTP prompt round-trips
        # broker-controlled text to the dashboard. Today the React app
        # renders it inside a plain text node, so an unescaped angle
        # bracket is harmless — but ``html.escape`` is a cheap defence
        # against any future consumer (HTML email, raw-HTML preview,
        # admin-bot Telegram digest) that interpolates the value into
        # an HTML context. Escape at the boundary so downstream code
        # never has to think about it.
        if self._state == "awaiting_otp" and self._otp_prompt is not None:
            otp_prompt = html.escape(self._otp_prompt)
        else:
            otp_prompt = None
        return BrokerStatus(
            configured=self.configured,
            connected=self.connected,
            state=self._state,
            awaiting_otp=self._state == "awaiting_otp",
            otp_prompt=otp_prompt,
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

    def set_session_store(self, store: Any | None) -> None:
        """Attach a SessionStore-like object. ``None`` clears it.

        Duck-typed on three methods: ``load() -> dict | None``,
        ``save(dict) -> None``, ``clear() -> None``. Tests inject a
        fake; production wires the real ``SessionStore``.
        """
        self._session_store = store

    def set_otp_relay(self, relay: Any | None) -> None:
        """Attach an AdminBotOTPRelay-like object. Duck-typed on
        three async methods: ``on_otp_required(prompt, attempt)``,
        ``on_otp_resolved()``, ``on_otp_timeout()``.
        """
        self._otp_relay = relay

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

    _PREFLIGHT_URL = "https://qxbroker.com/en/sign-in"
    _PREFLIGHT_TIMEOUT_SECONDS = 5.0

    async def _preflight_check(self) -> None:
        """Spec §3.1. Short HTTP probe to catch broker hard-failures
        before pyquotex burns OTP-supervisor retry budget on them.

        Falls through silently on network-level errors — the probe
        isn't conclusive in that case, so pyquotex still gets a turn.
        Raises :class:`BrokerPreflightFailed` only when the broker
        explicitly answers with 403 or 5xx.
        """
        profile = settings.broker_curl_cffi_profile
        try:
            resp = await asyncio.to_thread(
                _curl_get,
                self._PREFLIGHT_URL,
                impersonate=profile,
                timeout=self._PREFLIGHT_TIMEOUT_SECONDS,
            )
        except _curl_requests.RequestsError as exc:
            log.warning(
                "broker.preflight.network_error",
                detail=str(exc),
                impersonate_profile=profile,
            )
            return
        # No separate TimeoutError branch: curl_cffi maps every curl
        # timeout to curl_cffi.requests.exceptions.Timeout (a
        # RequestsError subclass), caught above. We don't wrap in
        # asyncio.wait_for, so no bare asyncio TimeoutError can
        # surface here either.

        status = int(resp.status_code)
        if status == 403:
            log.error(
                "broker.preflight.cloudflare_403",
                impersonate_profile=profile,
                body_bytes=len(getattr(resp, "content", b"") or b""),
            )
            raise BrokerPreflightFailed(
                "cloudflare 403 — fingerprint regression suspected; "
                "see RUNBOOK §B (rotate curl_cffi profile)"
            )
        if 500 <= status < 600:
            log.error(
                "broker.preflight.upstream_5xx",
                status=status,
                impersonate_profile=profile,
            )
            raise BrokerPreflightFailed(
                f"broker upstream returned {status} — "
                "check brokerstatus + retry in 5 min"
            )
        log.info("broker.preflight.ok", status=status)

    async def _do_connect(self) -> None:
        # Phase 3a (audit 2026-05-13, H2): the broker login round-trip
        # takes 2–30s against Cloudflare; holding ``self._lock`` for
        # that whole window blocked every other broker method
        # (disconnect, set_account_mode, refresh_assets, status
        # introspection paths that lock-snapshot). The fix is to keep
        # the lock for SETUP and FINALIZE only — release it for the
        # slow ``await client.connect()`` in the middle. The pre-
        # ``connecting`` state check in ``begin_connect`` already
        # single-flights this method, so two ``_do_connect`` coroutines
        # can't race; everything else just becomes responsive again.
        # M3 (the ``_status_watcher_task`` spawn race) closes for free
        # because ``_start_status_watcher`` runs inside the FINALIZE
        # critical section.

        # ── Phase A: lock-held setup. Runs ``_preflight_check()`` (≤5s
        # network probe, bounded by ``_PREFLIGHT_TIMEOUT_SECONDS``) plus
        # the in-memory Quotex() construction and one disk read for
        # session hydration.
        #
        # Why hold the lock over a 5s network call? The H2 fix (Phase
        # 3a) was specifically about the *unbounded* 2-30s login wait
        # inside ``client.connect()`` — holding the lock for that window
        # blocked every other broker method for up to 30s. The preflight
        # probe is different: it is *bounded* at 5s and it fails fast
        # (raising ``BrokerPreflightFailed``) when the broker is hard-
        # down. That 5s bound is an acceptable tradeoff: a probe that
        # aborts immediately prevents the Phase B login from burning
        # ~30s of OTP-supervisor retry budget on a dead broker, which is
        # a net win even if it briefly delays a concurrent caller by up
        # to 5s. On the happy path the probe latency precedes work that
        # would happen inside Phase B anyway.
        async with self._timed_lock("do_connect:setup"):
            # Spec §3.1: short HTTP probe before pyquotex burns OTP
            # budget on a broker-side hard failure.
            try:
                await self._preflight_check()
            except BrokerPreflightFailed as exc:
                self._state = "error"
                self._last_error = str(exc)
                log.warning("broker.connect.preflight_failed", detail=str(exc))
                return

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
                # Hydrate session_data from disk if we have a store and
                # a cached payload. Pyquotex's ``_connect_unlocked``
                # checks ``self.session_data.get("token")`` and skips
                # the HTTP login when present — so this is the path
                # that lets a container restart skip OTP.
                used_cached_session = False  # local flag for I1 cleanup
                if self._session_store is not None:
                    cached = self._session_store.load()
                    if cached:
                        client.session_data = cached
                        used_cached_session = True  # ← track cache hit
                        log.info(
                            "broker.session.loaded",
                            token_present=bool(cached.get("token")),
                        )
            except Exception as exc:
                # Setup failures (missing creds, Quotex() ctor blew up
                # — neither expected in steady-state) flip the state
                # machine to ``error`` while still under the lock.
                self._state = "error"
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("broker.connect.setup_failed")
                return

        # ── Phase B: LOCK-FREE. The blast-radius reduction.
        # While pyquotex is doing its TLS handshake / login POST /
        # WebSocket handshake / OTP wait, every other broker method
        # gets a fast lock acquire. ``_on_otp_callback`` runs inside
        # this await but only touches its own fields (``_otp_future``,
        # ``_otp_prompt``, ``_otp_attempt``, ``_consecutive_otp_failures``,
        # ``_state``) — none of which the lock was protecting either.
        _connect_start_monotonic = time.monotonic()
        try:
            ok, reason = await client.connect()
        except asyncio.CancelledError:
            # User-initiated cancel. ``cancel_connect`` is the one
            # that resolves the visible state — we just bail cleanly.
            self._reset_otp()
            # Reset attempt counter so the NEXT begin_connect
            # starts at attempt=1, sending a fresh Telegram
            # OTP message instead of editing the dead one
            # from the cancelled cycle.
            self._otp_attempt = 0
            log.info("broker.connect.cancelled")
            raise
        except Exception as exc:
            # Spec §3.2: forensic capture for future M6 taxonomy.
            _elapsed_ms = int(
                (time.monotonic() - _connect_start_monotonic) * 1000
            )
            _api = getattr(client, "api", None)
            _state = getattr(_api, "state", None)
            _auth = getattr(_state, "auth_status", None)
            # auth_status enum → its .name for grep-friendly logs;
            # falls back to the raw value if it's not an enum.
            _auth_name = getattr(_auth, "name", _auth)
            _ssid_loaded = bool(getattr(_state, "SSID", None))
            _is_authed = (
                _auth == AuthStatus.AUTHENTICATED
                if _auth is not None else None
            )
            log.warning(
                "broker.connect.rejection_probe",
                raw_error=str(exc),
                error_class=type(exc).__name__,
                elapsed_ms=_elapsed_ms,
                auth_status=_auth_name,
                ssid_loaded=_ssid_loaded,
                is_authenticated=_is_authed,
                ws_url=getattr(_api, "wss_url", None),
                impersonate_profile=settings.broker_curl_cffi_profile,
                consecutive_otp_failures=self._consecutive_otp_failures,
            )
            # Same error rationale as before. Re-acquire the lock to
            # write terminal state — the ``awaiting_manual_recovery``
            # preservation has to be inside the critical section
            # because ``_halt_for_otp_exhaustion`` (which sets it)
            # races otherwise.
            async with self._timed_lock("do_connect:error"):
                # Preserve the ``awaiting_manual_recovery`` state set by
                # ``_halt_for_otp_exhaustion`` — when the OTP cap is
                # exhausted, ``_on_otp_callback`` raises
                # ``QuotexManagerError("otp_attempts_exhausted")`` from
                # inside ``client.connect()``. That exception propagates
                # up to here, but the manager state machine has ALREADY
                # been advanced to ``awaiting_manual_recovery`` with a
                # human-readable ``_last_error``. Overwriting both back
                # to ``error`` here would tell the operator they hit a
                # transient blip and a retry might fix it — which is the
                # exact opposite of true: the supervisor is permanently
                # halted until /reconnect is run. See Critical #2 in the
                # 2026-05-13 reviewer follow-up.
                if self._state != "awaiting_manual_recovery":
                    self._state = "error"
                    self._last_error = f"{type(exc).__name__}: {exc}"
                self._reset_otp()
                # Same rationale as the CancelledError branch — a
                # pyquotex crash inside connect() must not leave a
                # stale OTP attempt counter on the next try.
                self._otp_attempt = 0
            log.exception("broker.connect.error")
            return

        # ── Phase C: lock-held finalize.
        async with self._timed_lock("do_connect:finalize"):
            if ok:
                self._client = client
                self._connected_at = utc_now()
                self._state = "connected"
                self._last_error = None
                self._consecutive_failed_reconnects = 0
                # Fix C — clean connect resets the OTP-failure gate so a
                # FUTURE disconnect window starts the cap from zero.
                self._consecutive_otp_failures = 0
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
                # Persist the freshly-warm session so the NEXT
                # container restart can skip OTP. Best-effort: log on
                # failure but don't fail the connect.
                if self._session_store is not None:
                    try:
                        self._session_store.save(client.session_data)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("broker.session.save_failed", error=str(exc))
                # Notify the relay so the Telegram OTP message edits
                # to '✅ Connected.' Best-effort: failure here is a
                # cosmetic glitch, not a connect failure.
                if self._otp_relay is not None and self._otp_attempt > 0:
                    try:
                        await self._otp_relay.on_otp_resolved()
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "broker.otp.relay_resolved_failed",
                            error=str(exc),
                        )
                self._otp_attempt = 0
                # Best-effort: fetch the asset universe so the parser
                # layer can auto-resolve channel-side names to broker
                # codes. Failure here doesn't fail the connect.
                try:
                    await self._refresh_assets_locked()
                except Exception as exc:  # pragma: no cover - depends on broker
                    log.warning("broker.assets.refresh_failed", error=str(exc))
            else:
                # Spec §3.2: forensic capture for future M6 taxonomy.
                # ``raw_error`` is ``str(reason)`` (pyquotex's string)
                # — no credentials there by inspection.
                _elapsed_ms = int(
                    (time.monotonic() - _connect_start_monotonic) * 1000
                )
                _api = getattr(client, "api", None)
                _state = getattr(_api, "state", None)
                _auth = getattr(_state, "auth_status", None)
                # auth_status enum → its .name for grep-friendly logs;
                # falls back to the raw value if it's not an enum.
                _auth_name = getattr(_auth, "name", _auth)
                _ssid_loaded = bool(getattr(_state, "SSID", None))
                _is_authed = (
                    _auth == AuthStatus.AUTHENTICATED
                    if _auth is not None else None
                )
                log.warning(
                    "broker.connect.rejection_probe",
                    raw_error=str(reason),
                    error_class="connect_returned_false",
                    elapsed_ms=_elapsed_ms,
                    auth_status=_auth_name,
                    ssid_loaded=_ssid_loaded,
                    is_authenticated=_is_authed,
                    ws_url=getattr(_api, "wss_url", None),
                    impersonate_profile=settings.broker_curl_cffi_profile,
                    consecutive_otp_failures=self._consecutive_otp_failures,
                )
                self._state = "error"
                self._last_error = reason
                log.warning("broker.connect.rejected", reason=reason)
                # Stale SSID? Clear the cached session so the next
                # connect attempt re-runs the HTTP login from scratch
                # instead of loop-failing on the same dead token.
                if used_cached_session and self._session_store is not None:
                    try:
                        self._session_store.clear()
                        log.info("broker.session.cleared.after_rejection")
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "broker.session.clear_failed",
                            error=str(exc),
                        )
                self._emit_system_error(kind="connect.rejected", detail=reason)
                self._otp_attempt = 0
            self._reset_otp()

    async def disconnect(self) -> None:
        # Kill any in-flight connect first so it can't race against
        # us into "connected" right after we've torn down.
        await self.cancel_connect()
        # Stop the resilience watcher *before* taking the lock — the
        # watcher itself may briefly contend on it via callbacks and
        # we want a clean cancel before the client object disappears.
        await self._stop_status_watcher()
        # Stop any in-flight ceiling-halt task — if _halt_at_ceiling is
        # mid-flight it will race into the finalize lock after we've set
        # state="idle" and null _client, overwriting state back to
        # awaiting_manual_recovery and emitting a stale event. Cancel it
        # here, alongside the watcher stop, before we take the lock.
        await self._stop_ceiling_halt_task()
        async with self._timed_lock("disconnect"):
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

    async def _stop_ceiling_halt_task(self) -> None:
        task = self._ceiling_halt_task
        if task is None or task.done():
            self._ceiling_halt_task = None
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        self._ceiling_halt_task = None

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
        if self._state == "awaiting_manual_recovery":
            return  # terminal — operator must /reconnect; watcher must not un-stick it
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

    # ── Reconnect escalation ladder (Task 4 / spec §3.3) ─────────
    # Step 1 — at this many failures, downgrade the admin-bot tone
    # from "transient" to "outage". The supervisor KEEPS RUNNING.
    _SOFT_DOWNGRADE_AFTER_ATTEMPTS = 10
    # Step 2 — env-overridable hard ceiling. Default 20.
    # See settings.broker_reconnect_hard_ceiling.

    def _on_reconnect_attempt_failed(self, failed_count: int) -> None:
        """Called each time pyquotex's supervisor counts a failed retry.

        Spec §3.3 / Task 4: split the old cosmetic
        ``_HARD_OUTAGE_AFTER_ATTEMPTS`` into a soft downgrade (tone
        change) and a hard ceiling (auto-halt).
        """
        hard_ceiling = settings.broker_reconnect_hard_ceiling
        is_outage = failed_count >= self._SOFT_DOWNGRADE_AFTER_ATTEMPTS
        at_ceiling = failed_count >= hard_ceiling

        self._emit_system_error(
            kind="broker.recover_stalled",
            detail=f"reconnect attempt {failed_count} failed; still trying",
            recoverable=not is_outage,
        )

        if at_ceiling and self._state != "awaiting_manual_recovery" and (
            self._ceiling_halt_task is None or self._ceiling_halt_task.done()
        ):
            log.error(
                "broker.reconnect_ceiling_reached",
                failed_attempts=failed_count,
                ceiling=hard_ceiling,
            )
            self._ceiling_halt_task = asyncio.create_task(
                self._halt_at_ceiling(failed_count, hard_ceiling),
            )

    async def _halt_at_ceiling(
        self, failed_count: int, ceiling: int,
    ) -> None:
        """Stop the pyquotex supervisor and flip state machine.
        Idempotent — guarded by the state check."""
        if self._state == "awaiting_manual_recovery":
            return  # already halted (e.g. OTP exhaustion got there first)

        # I3-A: stop the status watcher before tearing down the connection.
        # The watcher mirrors WS state — its next tick after WS closes would
        # call _on_ws_dropped and clobber the awaiting_manual_recovery state
        # we're about to set. Cancel it here, exactly as disconnect() does,
        # before supervisor.stop() / client.disconnect().
        #
        # Deadlock safety: _halt_at_ceiling runs as its own asyncio.Task
        # (spawned via asyncio.create_task in _on_reconnect_attempt_failed).
        # _stop_status_watcher cancels+awaits _status_watcher_task, which is
        # a DIFFERENT task. There is no self-cancellation — safe to await.
        await self._stop_status_watcher()

        client = self._client
        if client is not None:
            supervisor = getattr(
                getattr(client, "api", None), "reconnect_supervisor", None,
            )
            if supervisor is not None:
                try:
                    await supervisor.stop()
                except Exception as exc:
                    log.warning(
                        "broker.reconnect_ceiling.stop_failed",
                        error=str(exc),
                    )
            try:
                await client.disconnect()
            except Exception as exc:
                log.warning(
                    "broker.reconnect_ceiling.disconnect_failed",
                    error=str(exc),
                )

        async with self._timed_lock("ceiling_halt:finalize"):
            self._state = "awaiting_manual_recovery"
            self._last_error = (
                f"auto reconnect ceiling reached after {failed_count} "
                f"attempts (limit {ceiling}); check account + IP, then "
                f"run /reconnect"
            )
            self._emit_system_error(
                kind="broker.reconnect_ceiling_reached",
                detail=self._last_error,
                recoverable=False,
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

        Re-prompts (broker re-challenges after a wrong code) come in
        as additional invocations of this same callback within one
        ``client.connect()`` await; we track that via
        ``self._otp_attempt`` so the relay can edit (vs. send) the
        Telegram message.

        Fix C (2026-05-12) — guards against pyquotex's internal
        supervisor regenerating a PIN email forever. If
        ``_consecutive_otp_failures`` has already reached
        ``settings.otp_max_attempts`` from prior timeouts in this
        disconnect window, we relay one final over-cap call (so
        Fix A's exhaustion alert fires on the operator's Telegram),
        disable the supervisor's policy, and raise to abort the
        in-flight connect. The operator must run /reconnect.
        """
        loop = asyncio.get_running_loop()
        self._otp_future = loop.create_future()
        self._otp_prompt = prompt.strip() or "Enter the code sent to your email."
        self._state = "awaiting_otp"
        self._otp_attempt += 1
        log.info(
            "broker.otp.prompted",
            prompt=self._otp_prompt[:80],
            attempt=self._otp_attempt,
        )
        # Bus-side broadcast for observers (notifier silently no-ops on
        # this event type today; future dashboards / digest tooling
        # may subscribe). Off the critical path — fire-and-forget.
        if self._event_bus is not None:
            try:
                self._event_bus.publish("broker.otp_required", {
                    "prompt": self._otp_prompt,
                    "attempt": self._otp_attempt,
                })
            except Exception as exc:  # noqa: BLE001
                log.warning("broker.otp.bus_publish_failed", error=str(exc))

        cap = settings.otp_max_attempts
        over_cap = self._consecutive_otp_failures >= cap

        # Relay the prompt to Telegram FIRST — whether under-cap (so
        # the operator can reply) or over-cap (so Fix A's relay can
        # fire its 'gave up' exhaustion alert). The over-cap signal
        # lives in the ``attempt`` arg we pass (cap+1+) — relay's
        # entry-point check handles it.
        relay_attempt = (
            self._consecutive_otp_failures + 1
            if over_cap
            else self._otp_attempt
        )
        if self._otp_relay is not None:
            try:
                await self._otp_relay.on_otp_required(
                    prompt=self._otp_prompt,
                    attempt=relay_attempt,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("broker.otp.relay_required_failed", error=str(exc))

        if over_cap:
            # Important #3 (2026-05-13) — narrow race window: while
            # we were inside the ``relay.on_otp_required`` await above,
            # the operator may have run /reconnect, which calls
            # ``reset_for_manual_reconnect`` and zeroes
            # ``_consecutive_otp_failures``. Re-read it RIGHT BEFORE
            # raising — if the operator reset us between the entry-
            # point check and now, fall through to the normal future-
            # await path so their fresh code reaches the broker.
            #
            # We don't need a lock because both writers (here and
            # ``reset_for_manual_reconnect``) run on the asyncio loop,
            # serialised by ``await`` boundaries. The race is purely
            # between two coroutines, not threads.
            if self._consecutive_otp_failures < cap:
                log.info(
                    "broker.otp.exhaustion_canceled_by_reset",
                    failures_now=self._consecutive_otp_failures,
                    cap=cap,
                )
            else:
                # Halt pyquotex's supervisor and bail out of the connect.
                self._halt_for_otp_exhaustion()
                self._reset_otp(keep_state=True)
                raise QuotexManagerError("otp_attempts_exhausted")

        try:
            return await asyncio.wait_for(self._otp_future, timeout=_OTP_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            self._last_error = "OTP timed out"
            # Fix C — this timeout/cancel is a real failure (operator
            # didn't reply in the 180s window). Bump the gate counter
            # BEFORE the relay call so a subsequent _on_otp_callback
            # can read the up-to-date value.
            self._consecutive_otp_failures += 1
            if self._otp_relay is not None:
                try:
                    await self._otp_relay.on_otp_timeout()
                except Exception as exc:  # noqa: BLE001
                    log.warning("broker.otp.relay_timeout_failed", error=str(exc))
            raise
        finally:
            self._reset_otp(keep_state=True)

    def _halt_for_otp_exhaustion(self) -> None:
        """Manager-side response to the OTP cap being exceeded.

        Flips the state machine into ``awaiting_manual_recovery``,
        disables pyquotex's internal reconnect supervisor (the loop
        that was regenerating PIN emails), and publishes a
        ``system.error`` event so the admin notifier knows this is a
        wedged-needs-operator state, not a transient blip.
        """
        cap = settings.otp_max_attempts
        log.warning(
            "broker.otp.exhausted_halting_supervisor",
            consecutive_failures=self._consecutive_otp_failures,
            cap=cap,
        )
        self._state = "awaiting_manual_recovery"
        self._last_error = (
            f"OTP attempts exhausted ({self._consecutive_otp_failures}/{cap})"
            f" — awaiting /reconnect"
        )
        # Halt the supervisor so its retry loop stops issuing PIN
        # emails. We flip TWO flags belt-and-braces:
        #
        # * ``_stopping = True`` — exits the INNER backoff loop
        #   (``_reconnect_with_backoff``) on its very next iteration.
        #   This is the critical one: that loop is the hot path that
        #   was regenerating PIN emails forever. It only checks
        #   ``_stopping``, NOT ``policy.enabled``, and it catches every
        #   exception from ``_attempt_reconnect`` via a broad ``except``
        #   so the ``QuotexManagerError`` we raise here would be
        #   swallowed and the loop would retry forever otherwise.
        # * ``policy.enabled = False`` — exits the OUTER ``_run`` loop
        #   the next time the supervisor sees a clean disconnect/
        #   reconnect cycle. Belt-and-braces for the path where the
        #   inner loop happens to exit by some other means.
        #
        # Why ``_stopping = True`` instead of ``await supervisor.stop()``?
        # ``stop()`` awaits the supervisor's task — but right now we
        # are CURRENTLY RUNNING inside that task chain (pyquotex called
        # us via the ``on_otp_callback`` hook). Awaiting our own task
        # would deadlock. Setting the flag and re-raising lets the
        # supervisor's loops exit cleanly on their own clock, with no
        # cross-coroutine cancellation gymnastics.
        #
        # ``_stopping`` is a private attribute on pyquotex's
        # ReconnectSupervisor. Touching it is a deliberate cross-
        # module assertion — we're working AROUND the upstream
        # library's broad-except-in-backoff-loop limitation. If
        # pyquotex ever grows a public ``halt()`` we should switch to
        # that. See production incident 2026-05-12.
        try:
            supervisor = self._client.api.reconnect_supervisor  # type: ignore[union-attr]
            if supervisor is not None:
                supervisor._stopping = True
                supervisor.policy.enabled = False
                log.info("broker.otp.supervisor_halted")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "broker.otp.supervisor_halt_failed",
                error=str(exc),
            )

        self._emit_system_error(
            kind="otp.exhausted_manual_recovery_required",
            detail=(
                f"OTP cap ({cap}) hit after consecutive timeouts; "
                f"halted pyquotex supervisor — operator must run /reconnect"
            ),
            recoverable=False,
        )

    def reset_for_manual_reconnect(self) -> None:
        """Called by the /reconnect command BEFORE :meth:`begin_connect`.

        Clears the OTP-failure gate so the next cycle has a fresh cap
        budget, clears the relay's exhaustion latch (so its prompts
        relay normally again), and re-enables pyquotex's reconnect
        supervisor so subsequent transient WS drops are still handled
        internally.

        Idempotent and safe to call when ``_client`` / ``api`` are
        ``None`` (the typical state before the first connect).
        """
        log.info(
            "broker.otp.manual_reconnect_reset",
            previous_failures=self._consecutive_otp_failures,
            previous_state=self._state,
        )
        self._consecutive_otp_failures = 0
        # Clear the relay's exhaustion latch (duck-typed — relay may
        # be ``None`` or may be a stub without the method, depending
        # on wiring stage).
        if self._otp_relay is not None:
            reset = getattr(self._otp_relay, "reset_exhaustion", None)
            if callable(reset):
                try:
                    reset()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "broker.otp.relay_reset_failed", error=str(exc),
                    )
        # Re-enable pyquotex's supervisor — without this, the next
        # connect's transient WS drops would never auto-recover.
        # We clear BOTH ``_stopping`` and ``policy.enabled`` because
        # ``_halt_for_otp_exhaustion`` flips both (see the matching
        # halt path for the rationale).
        #
        # Cold-start path: when /reconnect runs before any successful
        # connect has built a client (e.g. fresh boot, or after a halt
        # that tore the client down), ``_client`` is ``None`` and there
        # is no supervisor to re-enable. That's an EXPECTED state — log
        # it at info with a structured reason, not a warning, so the
        # next disconnect investigation isn't distracted by a phantom
        # 'failed to re-enable' line.
        client = self._client
        api = getattr(client, "api", None) if client is not None else None
        supervisor = getattr(api, "reconnect_supervisor", None) if api is not None else None
        if supervisor is None:
            log.info(
                "broker.otp.supervisor_reenable_skipped",
                reason="no_client",
            )
        else:
            try:
                supervisor._stopping = False
                supervisor.policy.enabled = True
            except Exception as exc:  # noqa: BLE001
                # Genuinely unexpected — the supervisor existed but
                # we couldn't flip its flags. Keep this as a warning
                # so it surfaces in alerting.
                log.warning(
                    "broker.otp.supervisor_reenable_failed",
                    error=str(exc),
                )
        # Drop the manual-recovery state back to idle so begin_connect
        # can transition cleanly into connecting on the operator's
        # next /reconnect step.
        if self._state == "awaiting_manual_recovery":
            self._state = "idle"
            self._last_error = None

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

        async with self._timed_lock("set_account_mode"):
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

    # Phase 4 cleanup (audit 2026-05-13, L2): the ``account_type_int``
    # property was defined but never called from anywhere in the
    # codebase. Dead code removed — the integer form is read straight
    # from pyquotex's ``AccountType`` enum at the only sites that
    # ever needed it.

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
        async with self._timed_lock("refresh_assets"):
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
        # ``get_all_assets`` returns ``{symbol_name: internal_id}``.
        # Pyquotex's WebSocket subscribes (and our parsers + the
        # executor's signal.asset field) use the SYMBOL NAMES — e.g.
        # "EURUSD_otc", "USDBRL_otc" — not the internal numeric IDs.
        # We want the KEY side of the mapping.
        codes = sorted({str(k).strip() for k in (mapping or {}).keys() if k})
        self._assets = tuple(codes)
        log.info("broker.assets.refreshed", count=len(codes))

    # ------------------------------------------------------------------
    # Pre-trade health gate (Task 5 / spec §3.5)
    # ------------------------------------------------------------------

    def _last_tick_age_seconds(self, asset: str) -> float | None:
        """Return how many seconds old the latest realtime tick is,
        or ``None`` if no tick has ever been seen for ``asset``.

        Reads ``client.api.realtime_price[asset]`` — a
        ``deque[{"time": float, "price": float}]`` populated by
        pyquotex's ``_on_message`` at ``api.py:700``.  The ``"time"``
        value is a Unix epoch timestamp in **seconds** (confirmed by
        ``timesync.py``: ``datetime.fromtimestamp(ts)`` used directly).

        All accesses are null-safe: any missing attribute returns
        ``None`` rather than raising, so ``assert_live`` can only ever
        raise ``BrokerNotLive`` — never an unexpected AttributeError.
        """
        client = self._client
        if client is None:
            return None
        api = getattr(client, "api", None)
        if api is None:
            return None
        realtime_price = getattr(api, "realtime_price", None)
        if realtime_price is None:
            return None
        price_deque = realtime_price.get(asset)
        if not price_deque:
            return None
        # deque is ordered oldest→newest; [-1] is the most recent.
        latest = price_deque[-1]
        ts = latest.get("time") if isinstance(latest, dict) else None
        if ts is None:
            return None
        return time.time() - float(ts)

    async def assert_live(self, asset: str) -> None:
        """Assert the broker WS is genuinely live and the asset's price
        feed is fresh enough to accept an order.

        Raises :class:`BrokerNotLive` with one of:

        * ``reason="not_connected"`` — manager state machine is not
          ``"connected"`` (could be reconnecting, idle, error, etc.).
        * ``reason="ws_not_authed"`` — state is ``"connected"`` but
          pyquotex's ``api.state.auth_status`` is NOT
          ``AuthStatus.AUTHENTICATED``. Uses the real state-object
          path (NOT the ``is_authenticated`` method — that is a bound
          method object, always truthy).
        * ``reason="no_tick_seen"`` — no realtime-price tick has
          arrived for ``asset`` yet.
        * ``reason="stale_feed"`` — the latest tick is older than
          ``settings.broker_stale_feed_max_age_seconds``.

        Returns ``None`` (no raise) when all checks pass.
        """
        # ── Check 1: manager state machine ───────────────────────────
        if self._state != "connected":
            raise BrokerNotLive("not_connected", state=self._state)

        # ── Check 2: pyquotex WS auth ─────────────────────────────────
        # Read client.api.state.auth_status — the real pyquotex path.
        # is_authenticated on QuotexAPI is a *bound method*, not a bool;
        # using it would always be truthy. Task 3's rejection-probe code
        # (quotex_manager.py:~566) uses the same state.auth_status path.
        _authed = False
        try:
            _api = getattr(self._client, "api", None)
            _state = getattr(_api, "state", None)
            _auth = getattr(_state, "auth_status", None)
            _authed = (_auth == AuthStatus.AUTHENTICATED)
        except Exception as _exc:  # null-safe: an auth-probe error must never leak
            log.warning(
                "broker.assert_live.auth_probe_failed",
                error=str(_exc),
                exc_info=True,
            )
        if not _authed:
            raise BrokerNotLive("ws_not_authed")

        # ── Check 3 + 4: tick freshness ───────────────────────────────
        age = self._last_tick_age_seconds(asset)
        if age is None:
            raise BrokerNotLive("no_tick_seen", asset=asset)

        threshold = float(settings.broker_stale_feed_max_age_seconds)
        if age > threshold:
            raise BrokerNotLive(
                "stale_feed",
                asset=asset,
                age_seconds=age,
                threshold=threshold,
            )
