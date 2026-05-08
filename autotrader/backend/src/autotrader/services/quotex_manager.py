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
  login cost.

pyquotex itself owns reconnect supervision and session caching, so we
just stay out of its way and call its async methods.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog
from pyquotex.stable_api import Quotex
from pyquotex.utils.account_type import AccountType

from autotrader.config import settings
from autotrader.models.base import utc_now

log = structlog.get_logger(__name__)

AccountMode = Literal["PRACTICE", "REAL"]
ConnectState = Literal["idle", "connecting", "awaiting_otp", "connected", "error"]

# How long the manager waits for the user to type an OTP before giving
# up and tearing the in-flight connect down. Three minutes covers slow
# email delivery / SMS without keeping a stale connect task forever.
_OTP_TIMEOUT_SECONDS = 180


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

    def __init__(self, *, root_path: str = ".") -> None:
        self._root_path = root_path
        Path(root_path).mkdir(parents=True, exist_ok=True)

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
        return self._client is not None and self._client.api is not None

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
                log.info(
                    "broker.connect.ok",
                    email_masked=_mask_email(self._email or ""),
                    account_mode=self._account_mode,
                )
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
            self._reset_otp()

    async def disconnect(self) -> None:
        # Kill any in-flight connect first so it can't race against
        # us into "connected" right after we've torn down.
        await self.cancel_connect()
        async with self._lock:
            if self._client is None:
                self._state = "idle"
                return
            try:
                await self._client.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                log.warning("broker.disconnect.error", error=str(exc))
            finally:
                self._client = None
                self._connected_at = None
                self._state = "idle"
                log.info("broker.disconnect.ok")

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
