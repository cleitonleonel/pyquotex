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


# --------------------------------------------------------------------------
# Format helpers
# --------------------------------------------------------------------------


def format_trade_placed(payload: dict[str, Any]) -> str:
    asset = payload.get("asset") or "?"
    direction = (payload.get("direction") or "?").upper()
    duration = payload.get("duration_seconds", 0)
    stake = payload.get("stake", 0)
    head = f"🎯 {direction} {asset} {duration}s · ${stake}"
    ladder_line = _ladder_line(payload)
    if ladder_line:
        return f"{head}\n   {ladder_line}"
    return head


def format_trade_settled(payload: dict[str, Any]) -> str:
    """Format a settled-trade notification, optionally appending
    a ladder progress line when one of the parsers' ladders is
    active.

    Defensively coerces ``profit`` and ``direction`` because the
    payload may carry ``None`` for either field — TradeAttempt
    declares both as optional and a manually-constructed event
    bus payload could too.
    """
    status = payload.get("status", "")
    asset = payload.get("asset") or "?"
    direction = (payload.get("direction") or "?").upper()
    stake = payload.get("stake", 0)
    profit_raw = payload.get("profit")
    profit = float(profit_raw) if isinstance(profit_raw, (int, float)) else 0.0

    if status == "won":
        head = f"✅ WON +${profit:.2f} {asset} {direction} ${stake}"
    elif status == "lost":
        head = f"❌ LOST ${profit:.2f} {asset} {direction} ${stake}"
    else:
        head = f"⚠️ {status.upper()} {asset} {direction} ${stake}"

    ladder_line = _ladder_line(payload)
    if ladder_line:
        return f"{head}\n   {ladder_line}"
    return head


def _ladder_line(payload: dict[str, Any]) -> str:
    """Return a one-line ladder hint, or empty string when neither
    ladder is in progress."""
    ladder = payload.get("ladder")
    if not isinstance(ladder, dict):
        return ""
    cur_win = ladder.get("current_win_streak", 0) or 0
    max_win = ladder.get("max_win_streak", 0) or 0
    cur_loss = ladder.get("current_streak", 0) or 0
    max_loss = ladder.get("max_streak", 0) or 0
    next_hint = ladder.get("next_stake_hint", 0) or 0
    if cur_win > 0:
        if max_win > 0 and cur_win >= max_win:
            return f"📈 win streak {cur_win}/{max_win} (max hit, reset) → next ${next_hint}"
        return f"📈 win streak {cur_win}/{max_win} → next ${next_hint}"
    if cur_loss > 0:
        return f"📉 next: martingale recovery ${next_hint} (step {cur_loss}/{max_loss})"
    return ""


def format_risk_rejected(payload: dict[str, Any]) -> str:
    asset = payload.get("asset", "?")
    direction = (payload.get("direction") or "?").upper()
    parser = payload.get("parser_name") or "?"
    reason = payload.get("reason") or "(no reason)"
    return (
        f"REJECTED  {asset} - {direction}  ({parser})\n"
        f"reason: {reason}"
    )


def format_system_error(payload: dict[str, Any]) -> str:
    component = payload.get("component", "?")
    kind = payload.get("kind", "?")
    detail = payload.get("detail", "")
    return (
        f"SYSTEM  {component} {kind}\n"
        f"detail: {detail}"
    )


# --------------------------------------------------------------------------
# Bus subscriber loop — patched onto AdminBotNotifier so the format
# functions above stay module-level (re-usable from tests).
# --------------------------------------------------------------------------


async def _consume(self: "AdminBotNotifier", bus: Any) -> None:
    """Forever-loop: subscribes to the bus, formats events, dispatches
    to ``self.notify``. Cancelled at shutdown."""
    self._digest_task = asyncio.create_task(_digest_loop(self))
    try:
        async for event in bus.subscribe():
            try:
                if event.type == "trade.upserted":
                    status = event.payload.get("status")
                    if status == "pending":
                        await self.notify("placed", format_trade_placed(event.payload))
                    elif status in ("won", "lost", "refund"):
                        await self.notify("settled", format_trade_settled(event.payload))
                elif event.type == "risk.rejected":
                    await self.notify("risk_rejected", format_risk_rejected(event.payload))
                elif event.type == "system.error":
                    await self.notify("system_error", format_system_error(event.payload))
            except Exception:  # noqa: BLE001
                log.exception("admin_bot_notify.consume.format_failed",
                              event_type=event.type)
    finally:
        if self._digest_task is not None:
            self._digest_task.cancel()
            try:
                await self._digest_task
            except asyncio.CancelledError:
                pass


async def _digest_loop(self: "AdminBotNotifier") -> None:
    """Periodic flush of pending suppression digests."""
    while True:
        try:
            await self.flush_digests()
        except Exception:  # noqa: BLE001
            log.exception("admin_bot_notify.digest_loop.failed")
        await asyncio.sleep(self._digest_window)


async def _shutdown(self: "AdminBotNotifier") -> None:
    """Best-effort flush + cancel the digest loop."""
    try:
        await self.flush_digests()
    except Exception:  # noqa: BLE001
        log.exception("admin_bot_notify.shutdown.flush_failed")
    if getattr(self, "_digest_task", None) is not None:
        self._digest_task.cancel()  # type: ignore[union-attr]
        try:
            await self._digest_task  # type: ignore[union-attr]
        except (asyncio.CancelledError, AttributeError):
            pass


# Patch the loop methods onto the class. Keeps the format functions
# at module scope (so tests can call them directly) without having to
# turn them into staticmethods.
AdminBotNotifier.run = _consume  # type: ignore[attr-defined]
AdminBotNotifier.shutdown = _shutdown  # type: ignore[attr-defined]
