"""In-process fan-out broker for live dashboard events.

A single bus per app, owned by the FastAPI lifespan. Producers
(trade executor, future risk-gate hooks) call :meth:`publish`
synchronously; consumers (the WebSocket router, tests) iterate
:meth:`subscribe` to receive events as they happen.

Lossy on purpose: each subscriber gets a bounded queue and a slow
client (paused tab, network stall) drops events rather than wedging
the executor. Trade-row events also persist to SQLite, so a missed
WS message is recoverable via ``GET /pipeline/trades`` — the bus is
purely a "live update" channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Single fan-out event. ``payload`` is JSON-ready."""

    type: str             # "trade.upserted" — leaving room for finer-grained types later
    payload: dict[str, Any]


class TradeEventBus:
    """Async fan-out broker. Fire-and-forget for producers."""

    def __init__(self, *, max_queue: int = 200) -> None:
        self._max_queue = max_queue
        self._subscribers: set[asyncio.Queue[TradeEvent]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast ``event`` to every active subscriber.

        Sync on purpose: the executor calls this on the hot path and
        shouldn't yield. Drops events to subscribers whose queue is
        full — the dashboard can re-fetch the persisted trade rows if
        it cares.
        """
        if not self._subscribers:
            return
        event = TradeEvent(type=event_type, payload=payload)
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning(
                    "event_bus.dropped",
                    event_type=event_type,
                    queue_size=queue.qsize(),
                )

    async def subscribe(self) -> AsyncIterator[TradeEvent]:
        """Yield events until the consumer cancels iteration.

        The queue is registered for the lifetime of the iterator and
        cleaned up when the caller breaks or the task is cancelled.
        """
        queue: asyncio.Queue[TradeEvent] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.discard(queue)
