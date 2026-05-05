"""Per-asset WebSocket fan-out for live streams.

The web API has a single shared :class:`Quotex` client but may have
many WebSocket subscribers. ``StreamRelay`` is the fan-out layer:

* Each ``(asset, kind)`` gets a single background task that watches
  the broker state and pushes deltas onto every subscriber's queue.
* New subscribers get their own bounded queue and an async iterator.
* When the last subscriber for an asset disconnects, the background
  task stops and the upstream subscription is dropped (best-effort).

Backpressure: subscriber queues are bounded (``queue_max``). If a slow
client falls behind, we drop the oldest tick rather than blocking
the producer — the strategy on the other end gets a price-tick
firehose, missing one isn't fatal.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from pyquotex.stable_api import Quotex

logger = logging.getLogger(__name__)


class StreamRelay:
    def __init__(
            self,
            client: "Quotex",
            poll_interval: float = 0.1,
            queue_max: int = 100,
    ):
        self.client = client
        self.poll_interval = poll_interval
        self.queue_max = queue_max
        # (kind, asset) -> set of subscriber queues
        self._subs: dict[tuple[str, str], set[asyncio.Queue]] = defaultdict(set)
        # (kind, asset) -> background task
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # ----- public subscription helpers -----

    async def subscribe_prices(self, asset: str) -> AsyncIterator[dict[str, Any]]:
        """Yield every new price tick for ``asset`` until the consumer
        stops iterating (e.g. WebSocket closes)."""
        async for tick in self._subscribe(("prices", asset), self._run_prices):
            yield tick

    async def subscribe_sentiment(self, asset: str) -> AsyncIterator[dict[str, Any]]:
        """Yield every new sentiment update for ``asset``."""
        async for snap in self._subscribe(("sentiment", asset), self._run_sentiment):
            yield snap

    # ----- internals -----

    async def _subscribe(
            self,
            key: tuple[str, str],
            runner,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_max)
        async with self._lock:
            self._subs[key].add(queue)
            if key not in self._tasks or self._tasks[key].done():
                self._tasks[key] = asyncio.create_task(runner(key[1]))
        try:
            while True:
                item = await queue.get()
                if item is None:  # sentinel to close the iterator
                    return
                yield item
        finally:
            async with self._lock:
                self._subs[key].discard(queue)
                if not self._subs[key]:
                    task = self._tasks.pop(key, None)
                    if task and not task.done():
                        task.cancel()
                    # Best-effort upstream cleanup
                    try:
                        if key[0] == "prices":
                            await self.client.stop_candles_stream(key[1])
                    except Exception as e:
                        logger.debug("stream cleanup failed for %s: %s", key, e)

    def _broadcast(self, key: tuple[str, str], item: dict[str, Any]) -> None:
        for q in list(self._subs.get(key, ())):
            if q.full():
                # Drop oldest to make room — keep the relay non-blocking.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    async def _run_prices(self, asset: str) -> None:
        """Poll the per-asset price deque and broadcast new ticks."""
        try:
            await self.client.start_realtime_price(asset)
        except Exception as e:
            logger.warning("start_realtime_price(%s) failed: %s", asset, e)
            self._broadcast(
                ("prices", asset),
                {"error": str(e), "asset": asset},
            )
            return

        last_seen_ts: int = 0
        key = ("prices", asset)
        while True:
            try:
                ticks = list(await self.client.get_realtime_price(asset))
            except Exception as e:
                logger.warning("get_realtime_price(%s) failed: %s", asset, e)
                await asyncio.sleep(self.poll_interval)
                continue

            for tick in ticks:
                ts = int(tick.get("time", 0))
                if ts <= last_seen_ts:
                    continue
                last_seen_ts = ts
                self._broadcast(key, {
                    "asset": asset,
                    "time": ts,
                    "price": float(tick.get("price", 0)),
                })

            await asyncio.sleep(self.poll_interval)

    async def _run_sentiment(self, asset: str) -> None:
        """Poll the per-asset sentiment cache and broadcast updates."""
        try:
            await self.client.start_realtime_sentiment(asset)
        except Exception as e:
            logger.warning("start_realtime_sentiment(%s) failed: %s", asset, e)
            self._broadcast(
                ("sentiment", asset),
                {"error": str(e), "asset": asset},
            )
            return

        last_payload: dict[str, Any] | None = None
        key = ("sentiment", asset)
        while True:
            try:
                snap = await self.client.get_realtime_sentiment(asset)
            except Exception as e:
                logger.warning("get_realtime_sentiment(%s) failed: %s", asset, e)
                await asyncio.sleep(self.poll_interval * 5)
                continue

            if snap and snap != last_payload:
                last_payload = dict(snap)
                bullish = snap.get("bullish") or snap.get("sentimentBuy") or snap.get("buy")
                bearish = snap.get("bearish") or snap.get("sentimentSell") or snap.get("sell")
                self._broadcast(key, {
                    "asset": asset,
                    "bullish": float(bullish) if bullish is not None else None,
                    "bearish": float(bearish) if bearish is not None else None,
                    "raw": dict(snap),
                })

            # Sentiment ticks slowly compared to prices.
            await asyncio.sleep(max(self.poll_interval, 0.5))

    async def shutdown(self) -> None:
        """Cancel every background task and signal subscribers to close."""
        async with self._lock:
            for q_set in self._subs.values():
                for q in q_set:
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass
            self._subs.clear()
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()
            self._tasks.clear()
