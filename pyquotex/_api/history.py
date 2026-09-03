"""History/candle-related methods extracted from Quotex.

This mixin is composed into Quotex via multiple inheritance. It uses
self.api, self.codes_asset, etc. — all set up in Quotex.__init__ inside
pyquotex/stable_api.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from pyquotex import expiration
from pyquotex._api._constants import DEFAULT_TIMEOUT, _request_counter
from pyquotex.utils import json_utils as json
from pyquotex.utils.cache import TTLCache
from pyquotex.utils.processor import (
    calculate_candles,
    merge_candles,
    process_candles_v2,
)

# Per-process cache of recent get_candles() responses. Keyed by
# (asset, period, offset, candle_bucket). TTL stays well under the candle
# period so live data is never served stale. Off by default; opt in by
# passing use_cache=True.
_CANDLE_CACHE: TTLCache[tuple[str, int, int, int], list[dict[str, Any]]] = (
    TTLCache(maxsize=128, ttl=10.0)
)

logger = logging.getLogger(__name__)


class HistoryMixin:
    """Methods related to candle data, historical queries, and trade history."""

    async def get_candles(
            self,
            asset: str,
            end_from_time: float | None,
            offset: int,
            period: int,
            progressive: bool = False,
            timeout: int = DEFAULT_TIMEOUT,
            use_cache: bool = False,
    ) -> list[dict[str, Any]] | None:
        """Retrieve candles for a specific asset.

        Parameters
        ----------
        use_cache:
            When ``True``, identical ``(asset, period, offset, candle_bucket)``
            requests within the cache TTL (~10s) are served from memory.
            ``candle_bucket`` floors ``end_from_time`` to the candle period
            so live data is never served stale.
        """
        if self.api is None:
            return None

        if end_from_time is None:
            end_from_time = time.time()

        cache_key: tuple[str, int, int, int] | None = None
        if use_cache and not progressive and period > 0:
            bucket = int(end_from_time // period)
            cache_key = (asset, period, offset, bucket)
            cached = _CANDLE_CACHE.get(cache_key)
            if cached is not None:
                return cached

        index = expiration.get_timestamp()
        self.api.candles.candles_data = None

        # Clear event state before requesting data to prevent
        # race with WS response
        await self.api.event_registry.clear_event(f'candles_ready_{asset}')

        await self.start_candles_stream(asset, period)
        await self.api.get_candles(asset, index, end_from_time, offset, period)

        try:
            # Wait for WebSocket event signaling candles' arrival
            history_data = await self.api.event_registry.wait_event(
                f'candles_ready_{asset}', timeout=timeout
            )
        except TimeoutError:
            logger.error(
                "Timeout waiting for candles for %s after %ds",
                asset, timeout
            )
            return None

        # Pass the asset-specific history directly to avoid
        # multi-asset state races
        candles = self.prepare_candles(asset, period, history_data)

        if progressive:
            return self.api.historical_candles.get("data", {})

        if cache_key is not None and candles:
            # TTL is the minimum between period and the cache default so
            # the candle bucket never serves data after the candle closes.
            _CANDLE_CACHE.set(cache_key, candles, ttl=min(_CANDLE_CACHE.ttl, period))

        return candles

    async def _fetch_historical_batch(
            self,
            asset: str,
            fetch_time: int,
            offset: int,
            period: int,
            index: int,
            timeout: int
    ) -> dict[str, Any] | None:
        """Low-level batch fetcher for a specific time point and index."""
        if self.api is None:
            return None

        payload = {
            "asset": asset,
            "index": index,
            "time": fetch_time,
            "offset": offset,
            "period": period
        }
        ws_msg = f'42["history/load",{json.dumps_str(payload)}]'

        # Clear specific event to ensure fresh wait
        event_name = f'candles_ready_{asset}_{index}'
        await self.api.event_registry.clear_event(event_name)

        await self.api.send_websocket_request(ws_msg)

        try:
            return await self.api.event_registry.wait_event(
                event_name, timeout=timeout
            )
        except TimeoutError:
            logger.warning(
                "Batch fetch timeout at %d (index %d) for %s",
                fetch_time, index, asset
            )
            return None

    def _parse_historical_candles(
            self, raw_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Standardizes raw candle data into a uniform list of dicts."""
        raw_candles = raw_data.get("data", []) or raw_data.get("candles", [])
        if not raw_candles:
            return []

        parsed = []
        for c in raw_candles:
            if isinstance(c, list) and len(c) >= 5:
                parsed.append({
                    "time": int(c[0]),
                    "open": float(c[1]),
                    "close": float(c[2]),
                    "high": float(c[3]),
                    "low": float(c[4])
                })
            elif isinstance(c, dict) and "time" in c:
                parsed.append(c)
        return parsed

    # https://t.me/pyquotex/1/16064
    # https://github.com/usmanch96/quotex-historical-data
    async def get_historical_candles(
            self,
            asset: str,
            amount_of_seconds: int,
            period: int,
            timeout: int = DEFAULT_TIMEOUT,
            max_workers: int = 5,
            progress_callback: Callable[[int, int, int, str], None] | None = None
    ) -> list[dict[str, Any]]:
        """
        Retrieves extensive historical candle data using a hybrid parallel-sequential approach.
        Divides the total time range into blocks assigned to parallel workers.
        Each worker fetches its block sequentially to ensure no gaps.
        """
        all_candles: dict[int, dict[str, Any]] = {}
        current_time = int(time.time())
        target_start_time = current_time - amount_of_seconds

        # Divide total range into large blocks for each worker
        block_size = amount_of_seconds // max_workers
        chunk_seconds = period * 200  # Request size per batch
        semaphore = asyncio.Semaphore(max_workers)

        async def worker(start_t: int, end_t: int, worker_id: int) -> list[dict[str, Any]]:
            worker_candles = {}
            worker_label = f"Worker-{worker_id}"
            async with semaphore:
                oldest_t = start_t
                while oldest_t > end_t:
                    # Use a monotonically-increasing counter so that parallel
                    # workers and back-to-back iterations within the same worker
                    # never produce the same index — prevents event-registry
                    # key collisions where one worker steals another's response.
                    index = next(_request_counter)

                    batch_data = await self._fetch_historical_batch(
                        asset, oldest_t, chunk_seconds, period, index, timeout
                    )

                    if not batch_data:
                        # Gap or error, jump back to try continuing
                        oldest_t -= chunk_seconds
                        continue

                    new_batch = self._parse_historical_candles(batch_data)
                    if not new_batch:
                        oldest_t -= chunk_seconds
                        continue

                    # Process and find new boundary
                    batch_times = []
                    for c in new_batch:
                        ts = c['time']
                        if ts >= end_t and ts <= start_t:
                            worker_candles[ts] = c
                            batch_times.append(ts)

                    if not batch_times:
                        oldest_t -= chunk_seconds
                        continue

                    batch_times.sort()
                    new_oldest = batch_times[0]

                    if progress_callback:
                        # Report progress based on how much of the block is covered
                        progress_callback(
                            start_t - new_oldest,
                            start_t - end_t,
                            len(worker_candles),
                            worker_label
                        )

                    if new_oldest >= oldest_t:
                        oldest_t -= chunk_seconds
                    else:
                        oldest_t = new_oldest

                    # Small throttle
                    await asyncio.sleep(0.1)

            return list(worker_candles.values())

        await self.start_candles_stream(asset, period)

        # Launch workers for each block
        tasks = []
        for i in range(max_workers):
            s = current_time - (i * block_size)
            e = max(target_start_time, s - block_size)
            tasks.append(worker(s, e, i))

        results = await asyncio.gather(*tasks)

        # Merge results and deduplicate
        for batch in results:
            for c in batch:
                all_candles[c['time']] = c

        return sorted(all_candles.values(), key=lambda x: x['time'])

    async def get_candles_deep(
            self, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Deprecated alias for get_historical_candles."""
        logger.warning(
            "get_candles_deep is deprecated, "
            "use get_historical_candles instead."
        )
        return await self.get_historical_candles(*args, **kwargs)

    async def get_history_line(
            self,
            asset: str,
            end_from_time: float,
            offset: int,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any] | None:
        """Retrieves historical price line data for an asset."""
        if self.api is None:
            return None

        index = expiration.get_timestamp()
        self.api.current_asset = asset
        # Reset to None (not {}) so the poll loop below can detect arrival.
        # An empty dict is a valid response; None is the sentinel for
        # "not yet received".
        self.api.historical_candles = None
        await self.start_candles_stream(asset)
        await self.api.get_history_line(
            self.codes_asset[asset], index, end_from_time, offset
        )
        # TODO(refactor/architecture Phase 2.7): polling here cannot be migrated
        # to SlotRegistry until we identify the WS event that should populate
        # self.api.historical_candles. No producer exists in
        # pyquotex/api.py:_on_message, so this method currently only exits via
        # the timeout branch below. Same situation as edit_practice_balance
        # and sell_option — investigate WS traffic when calling get_history_line
        # to find the correct producer event.
        start_time = time.time()
        while await self.check_connect() and self.api.historical_candles is None:
            if time.time() - start_time > timeout:
                logger.error(
                    "Timeout waiting for history line data for %s.",
                    asset
                )
                return None
            await asyncio.sleep(0.2)
        return self.api.historical_candles

    async def get_candle_v2(
            self, asset: str, period: int, timeout: int = DEFAULT_TIMEOUT
    ) -> list[dict[str, Any]] | None:
        """Retrieves candles using the v2 API path."""
        if self.api is None:
            return None

        # Reset the slot AND the data dict — both serve as sentinels.
        self.api.candle_v2_data[asset] = None
        self.api.slots.release_candle_v2(asset)
        await self.start_candles_stream(asset, period)
        try:
            await self.api.slots.candle_v2(asset).wait(timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                "Timeout waiting for get_candle_v2 data for %s.",
                asset
            )
            return None
        candles = self.prepare_candles(asset, period)
        return candles

    def prepare_candles(
            self,
            asset: str,
            period: int,
            history: list[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Prepare candles data for a specified asset."""
        if self.api is None:
            return []

        # Use provided history if available (from event response),
        # otherwise fallback to shared state
        history_data = (
            history if history is not None else self.api.candles.candles_data
        )
        candles_data = calculate_candles(history_data, period)
        candles_v2_data = process_candles_v2(
            self.api.candle_v2_data, asset, candles_data
        )
        new_candles = merge_candles(candles_v2_data)

        return new_candles

    async def get_trader_history(
            self, account_type: int, page_number: int
    ) -> dict[str, Any]:
        """Retrieves trade history for a specific account and page."""
        if self.api:
            return await self.api.get_trader_history(account_type, page_number)
        return {}
