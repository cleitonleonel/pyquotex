"""Event-driven wait primitives that replace asyncio.sleep polling.

A WaitableSlot is a typed one-shot (re-armable) signal: a consumer awaits
.wait(), and the producer (typically the WS message handler) calls .set(value).

wait_until() exists for cases where the desired state cannot be signaled
from the WS handler. It still uses short polling internally but enforces
a hard timeout.
"""

from __future__ import annotations

import asyncio
import random
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

DEFAULT_TIMEOUT: float = 10.0


class WaitableSlot(Generic[T]):
    """Typed slot a consumer awaits and the producer fills via .set()."""

    __slots__ = ("_value", "_event")

    def __init__(self) -> None:
        self._value: T | None = None
        self._event = asyncio.Event()

    def set(self, value: T) -> None:
        """Store the value and wake any awaiting consumers.

        Raises ValueError if value is None — use clear() to reset the slot.
        """
        if value is None:
            raise ValueError("WaitableSlot.set() does not accept None; use clear() to reset")
        self._value = value
        self._event.set()

    def clear(self) -> None:
        """Reset the slot so subsequent waits block again."""
        self._value = None
        self._event.clear()

    def is_set(self) -> bool:
        """Return True if the slot currently holds a value."""
        return self._event.is_set()

    # NOTE: raises asyncio.TimeoutError on timeout. Phase 2 consumers wrap
    # this in pyquotex.exceptions.QuotexTimeoutError at their call sites so
    # the asyncio coupling stays internal to this module.
    async def wait(self, timeout: float = DEFAULT_TIMEOUT) -> T:
        """Block until set or raise asyncio.TimeoutError on timeout."""
        await asyncio.wait_for(self._event.wait(), timeout=timeout)
        return self._value  # type: ignore[return-value]


# NOTE: raises asyncio.TimeoutError on timeout. Consumers should wrap it
# in pyquotex.exceptions.QuotexTimeoutError at their call sites.
async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll_interval: float = 0.05,
) -> None:
    """Poll predicate() until truthy or raise asyncio.TimeoutError."""

    async def _loop() -> None:
        while not predicate():
            await asyncio.sleep(poll_interval)

    await asyncio.wait_for(_loop(), timeout=timeout)


async def backoff_sleep(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.1,
) -> None:
    """Sleep for an exponentially increasing duration with jitter.

    Used for retry loops where the previous attempt failed. `attempt` is
    zero-indexed (0, 1, 2, ...). Delay grows as base * 2**attempt, capped
    at `cap` seconds, with multiplicative jitter of ±jitter (0.1 = ±10%).
    """
    delay = min(cap, base * (2 ** attempt))
    delay = delay * (1.0 + random.uniform(-jitter, jitter))
    await asyncio.sleep(max(0.0, delay))


class SlotRegistry:
    """Container of named and keyed WaitableSlots used by QuotexAPI.

    Named slots are pre-created for one-off events (balance update, auth
    status change, etc.). Keyed slots are dynamic per-request waits keyed
    by request_id / operation_id; they are created lazily and released
    once the consumer has read the value.
    """

    def __init__(self) -> None:
        # Named slots
        self.balance: WaitableSlot[dict] = WaitableSlot()
        self.balance_update: WaitableSlot[dict] = WaitableSlot()
        self.candle_v2_ready: WaitableSlot[str] = WaitableSlot()
        self.historical_ready: WaitableSlot[str] = WaitableSlot()
        self.pending_confirm: WaitableSlot[dict] = WaitableSlot()
        self.buy_confirm: WaitableSlot[dict] = WaitableSlot()
        self.sold_option_confirm: WaitableSlot[dict] = WaitableSlot()
        self.training_balance_edit: WaitableSlot[dict] = WaitableSlot()
        self.auth_status: WaitableSlot[bool] = WaitableSlot()

        # Keyed slots (created on demand)
        self._order_confirm: dict[str, WaitableSlot[dict]] = {}
        self._win_result: dict[str, WaitableSlot[dict]] = {}
        self._candle_v2: dict[str, WaitableSlot[dict]] = {}

    def order_confirm(self, request_id: str) -> WaitableSlot[dict]:
        slot = self._order_confirm.get(request_id)
        if slot is None:
            slot = WaitableSlot()
            self._order_confirm[request_id] = slot
        return slot

    def release_order_confirm(self, request_id: str) -> None:
        self._order_confirm.pop(request_id, None)

    def win_result(self, operation_id: str) -> WaitableSlot[dict]:
        slot = self._win_result.get(operation_id)
        if slot is None:
            slot = WaitableSlot()
            self._win_result[operation_id] = slot
        return slot

    def release_win_result(self, operation_id: str) -> None:
        self._win_result.pop(operation_id, None)

    def candle_v2(self, asset: str) -> WaitableSlot[dict]:
        """Get or create a per-asset slot fired by the candle_v2_data handler."""
        slot = self._candle_v2.get(asset)
        if slot is None:
            slot = WaitableSlot()
            self._candle_v2[asset] = slot
        return slot

    def release_candle_v2(self, asset: str) -> None:
        """Release the per-asset candle_v2 slot so a new wait creates a fresh one."""
        self._candle_v2.pop(asset, None)
