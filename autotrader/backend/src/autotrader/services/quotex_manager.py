"""Long-lived wrapper around a single ``pyquotex.stable_api.Quotex`` client.

The manager is the only thing in the app that talks to pyquotex
directly. Everything else (HTTP routers, the future Telegram pipeline,
the trade executor) goes through it. Keeping this surface narrow gives
us a single place to:

* serialise login / logout (so two requests can't race into duplicate
  Quotex sessions),
* enforce the REAL-trading gate from ``settings.live_trading_enabled``,
* expose a tiny, stable status snapshot to the rest of the app,
* pre-warm the connection at app startup so the first trade pays no
  login cost.

pyquotex itself owns reconnect supervision (``ReconnectPolicy`` /
``ReconnectSupervisor``) and session caching (``session.json`` under
``root_path``), so we just stay out of its way and call its async
methods.
"""

from __future__ import annotations

import asyncio
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


class QuotexManagerError(Exception):
    """Raised for caller-visible failures (auth rejected, REAL gate, etc.)."""


@dataclass(frozen=True, slots=True)
class BrokerStatus:
    """Public snapshot — safe to serialise to the dashboard."""

    configured: bool          # credentials are stored
    connected: bool           # WebSocket is live
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
        # ``Quotex`` writes session.json under root_path. Putting it on
        # the persistent volume means container restarts skip re-auth.
        Path(root_path).mkdir(parents=True, exist_ok=True)

        self._client: Quotex | None = None
        self._email: str | None = None
        self._password: str | None = None
        self._account_mode: AccountMode = "PRACTICE"
        self._connected_at: datetime | None = None
        self._last_error: str | None = None
        # Serialises connect / disconnect / mode-change so two callers
        # can't race into half-built state.
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
        """In-memory only — DB persistence is the caller's concern.

        Does NOT (re)connect. Call ``connect()`` afterwards if the
        manager was already live.
        """
        self._email = email
        self._password = password
        self._account_mode = account_mode

    def clear_credentials(self) -> None:
        self._email = None
        self._password = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> tuple[bool, str]:
        """Idempotent: returns immediately if already connected."""
        async with self._lock:
            if self.connected:
                return True, "already connected"
            if not self.configured:
                return False, "credentials not set"

            self._enforce_live_gate(self._account_mode)

            assert self._email is not None  # narrowed by configured check
            assert self._password is not None

            client = Quotex(
                email=self._email,
                password=self._password,
                root_path=self._root_path,
                lang="en",
            )
            client.set_account_mode(self._account_mode)

            try:
                ok, reason = await client.connect()
            except Exception as exc:  # pragma: no cover - depends on broker
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.error("broker.connect.failed", error=self._last_error)
                return False, self._last_error

            if not ok:
                self._last_error = reason
                log.warning("broker.connect.rejected", reason=reason)
                return False, reason

            self._client = client
            self._connected_at = utc_now()
            self._last_error = None
            log.info(
                "broker.connect.ok",
                email_masked=_mask_email(self._email),
                account_mode=self._account_mode,
            )
            return True, "connected"

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            try:
                await self._client.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                log.warning("broker.disconnect.error", error=str(exc))
            finally:
                self._client = None
                self._connected_at = None
                log.info("broker.disconnect.ok")

    # ------------------------------------------------------------------
    # Account mode
    # ------------------------------------------------------------------

    async def set_account_mode(self, mode: AccountMode) -> None:
        """Hot-swap accounts. Hits the broker only when connected."""
        self._enforce_live_gate(mode)

        async with self._lock:
            self._account_mode = mode
            if self._client is None:
                # Mode will be applied on next connect.
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
        """Block REAL when the env-level kill switch is off."""
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
        """Underlying ``AccountType`` int value (0=REAL, 1=DEMO).

        Useful for future code that calls pyquotex APIs which want the
        int directly.
        """
        return AccountType.DEMO if self._account_mode == "PRACTICE" else AccountType.REAL
