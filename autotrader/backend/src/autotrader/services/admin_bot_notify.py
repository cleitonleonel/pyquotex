"""Admin bot notifier — bridges the in-process event bus to Telegram DMs.

Subscribes to :class:`TradeEventBus`, formats events into compact
Markdown messages, applies a per-class token-bucket rate limit so a
flood (flapping broker, daily-cap breach hammering ``risk.rejected``)
collapses into a single coalesced digest, and DM's the bound admin.

Wired by ``main.py`` after both ``AdminBot`` and ``TradeEventBus`` are
constructed. Never load-bearing — a notifier failure only loses
visibility, not trading.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from autotrader.db import AsyncSessionLocal
from autotrader.models.settings import GlobalSettings

log = structlog.get_logger(__name__)

NotifyClass = Literal["placed", "settled", "risk_rejected", "system_error"]

_NOTIFY_FIELD = {
    "placed": "admin_notify_placed",
    "settled": "admin_notify_settled",
    "risk_rejected": "admin_notify_risk_rejected",
    "system_error": "admin_notify_system_error",
}


@dataclass
class _Bucket:
    """Token-bucket state for one notify-class. Tokens are floats so
    fractional refill is well-defined."""

    capacity: int
    refill_per_sec: float
    tokens: float
    last_refill: float
    suppressed: int = 0
    digest_due_at: float | None = None

    def take(self, now: float) -> bool:
        """Returns True if a token was available; False otherwise.
        Always refills first based on elapsed time."""
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(
                float(self.capacity), self.tokens + elapsed * self.refill_per_sec,
            )
            self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class AdminBotNotifier:
    """Holds the per-class buckets + the consecutive-failure backoff."""

    # After 5 consecutive ``send`` failures we pause outbound. The admin
    # sending *any* message back proves the channel is healthy; that
    # path lives in the message hook (Task 15 wires it).
    _FAILURE_THRESHOLD = 5

    def __init__(
        self,
        *,
        bot: Any,
        bucket_capacity: int = 5,
        refill_seconds: float = 30.0,
        digest_window: float = 60.0,
    ) -> None:
        self._bot = bot
        self._capacity = bucket_capacity
        self._refill = 1.0 / refill_seconds  # tokens per second
        self._digest_window = digest_window
        self._buckets: dict[str, _Bucket] = {}
        self._consecutive_failures = 0
        self._outbound_paused = False
        self._lock = asyncio.Lock()
        self._digest_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def outbound_paused(self) -> bool:
        return self._outbound_paused

    def reset_failures(self) -> None:
        """Called by the message hook when the admin sends *anything* —
        proves the channel is healthy, lift the backoff."""
        if self._consecutive_failures or self._outbound_paused:
            log.info("admin_bot_notify.backoff.cleared")
        self._consecutive_failures = 0
        self._outbound_paused = False

    async def notify(
        self,
        cls: NotifyClass,
        text: str,
        markup: Any | None = None,
    ) -> None:
        """Send a notification, applying per-class throttle + class mute.

        Drops silently when:
        * outbound is paused (backoff active)
        * the class is muted in GlobalSettings
        * no admin is bound
        * the bucket is empty (counts toward the next digest)
        """
        if self._outbound_paused:
            log.debug("admin_bot_notify.skip.paused", cls=cls)
            return

        async with AsyncSessionLocal() as session:
            gs = await session.get(GlobalSettings, 1)
        if gs is None or gs.admin_telegram_user_id is None:
            log.debug("admin_bot_notify.skip.unbound", cls=cls)
            return
        if not getattr(gs, _NOTIFY_FIELD[cls]):
            log.debug("admin_bot_notify.skip.muted", cls=cls)
            return

        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(cls) or _Bucket(
                capacity=self._capacity,
                refill_per_sec=self._refill,
                tokens=float(self._capacity),
                last_refill=now,
            )
            self._buckets[cls] = bucket
            allowed = bucket.take(now)
            if not allowed:
                bucket.suppressed += 1
                if bucket.digest_due_at is None:
                    bucket.digest_due_at = now + self._digest_window
                return

        await self._send(gs.admin_telegram_user_id, text, markup)

    async def flush_digests(self) -> None:
        """Emit any pending suppression-digest messages whose window
        has elapsed. Called from a periodic task in production."""
        now = time.monotonic()
        async with self._lock:
            due = [
                (cls, b) for cls, b in self._buckets.items()
                if b.suppressed > 0
                and b.digest_due_at is not None
                and b.digest_due_at <= now
            ]
            payloads: list[tuple[str, int]] = []
            for cls, b in due:
                payloads.append((cls, b.suppressed))
                b.suppressed = 0
                b.digest_due_at = None

        if not payloads:
            return
        async with AsyncSessionLocal() as session:
            gs = await session.get(GlobalSettings, 1)
        if gs is None or gs.admin_telegram_user_id is None:
            return
        for cls, count in payloads:
            await self._send(
                gs.admin_telegram_user_id,
                (
                    f"{count} `{cls}` events suppressed in last "
                    f"{int(self._digest_window)}s "
                    "(rate limit hit — see dashboard for details)"
                ),
                None,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _send(
        self,
        chat_id: int,
        text: str,
        markup: Any | None,
    ) -> None:
        try:
            await self._bot.send(chat_id, text, reply_markup=markup)
            self._consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            self._consecutive_failures += 1
            log.warning(
                "admin_bot_notify.send_failed",
                error=str(exc),
                consecutive=self._consecutive_failures,
            )
            if self._consecutive_failures >= self._FAILURE_THRESHOLD:
                self._outbound_paused = True
                log.warning(
                    "admin_bot_notify.backoff.engaged",
                    threshold=self._FAILURE_THRESHOLD,
                )
