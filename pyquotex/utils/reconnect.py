"""Auto-reconnect supervisor for the Quotex WebSocket connection.

Runs the websocket lifecycle in a loop, retries on disconnect with
exponential backoff, and replays subscriptions + account state so the
client surfaces the same shape it had before the drop.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..global_value import WebsocketStatus

logger = logging.getLogger(__name__)


@dataclass
class ReconnectPolicy:
    """Backoff and retry policy for the reconnect supervisor.

    Attributes:
        enabled: Whether to attempt reconnects at all.
        max_attempts: ``-1`` for unlimited.
        initial_delay: Seconds before the first retry.
        max_delay: Cap for the exponential backoff.
        backoff_factor: Multiplier applied each attempt.
        jitter: Random fraction (``0..1``) added to each delay.
    """

    enabled: bool = True
    max_attempts: int = -1
    initial_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    jitter: float = 0.25


@dataclass
class ReconnectStats:
    """Lightweight observability surface for reconnect activity."""

    attempts: int = 0
    successful_reconnects: int = 0
    failed_reconnects: int = 0
    last_disconnect_at: float | None = None
    last_reconnect_at: float | None = None
    last_error: str | None = None


@dataclass
class _SnapshotState:
    """State captured before a disconnect so we can replay it."""

    account_type: int | None = None
    tournament_id: int = 0
    candle_subs: list[tuple[str, int]] = field(default_factory=list)
    candle_all_size_subs: list[str] = field(default_factory=list)
    mood_subs: list[str] = field(default_factory=list)
    realtime_price_subs: list[tuple[str, int]] = field(default_factory=list)


class ReconnectSupervisor:
    """Supervises the WebSocket task and drives reconnect attempts.

    The supervisor is owned by :class:`QuotexAPI` and is started right
    after the first successful connection. It listens for
    ``status_changed`` events; when the socket drops it sleeps for the
    backoff window, then calls back into the API to start a fresh
    connection and replay the captured state.
    """

    def __init__(
            self,
            api: Any,
            policy: ReconnectPolicy | None = None,
    ):
        self.api = api
        self.policy = policy or ReconnectPolicy()
        self.stats = ReconnectStats()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._snapshot = _SnapshotState()
        self._on_reconnect: list[Callable[[], Awaitable[None] | None]] = []

    def on_reconnect(
            self, cb: Callable[[], Awaitable[None] | None]
    ) -> None:
        """Register a coroutine/function to run after each reconnect."""
        self._on_reconnect.append(cb)

    def capture(self) -> None:
        """Snapshot subscription state for replay on the next reconnect."""
        api = self.api
        self._snapshot.account_type = api.account_type
        self._snapshot.tournament_id = api.tournament_id

        # Quotex stable_api keeps subscription registries on the high-level
        # client; pull them via the back-reference if present.
        client = getattr(api, "_client_ref", None)
        if client is None:
            return

        self._snapshot.candle_subs = [
            (sp[0], int(sp[1]))
            for s in getattr(client, "subscribe_candle", [])
            if "," in s
            for sp in [s.split(",", 1)]
        ]
        self._snapshot.candle_all_size_subs = list(
            getattr(client, "subscribe_candle_all_size", [])
        )
        self._snapshot.mood_subs = list(getattr(client, "subscribe_mood", []))

    def start(self) -> None:
        """Spawn the supervisor task if not already running."""
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Graceful shutdown — disables further reconnects."""
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        try:
            while not self._stopping:
                await self._wait_for_disconnect()
                if self._stopping or not self.policy.enabled:
                    return
                self.stats.last_disconnect_at = time.time()
                await self._reconnect_with_backoff()
        except asyncio.CancelledError:
            return

    poll_interval: float = 0.05

    async def _wait_for_disconnect(self) -> None:
        """Block until the connection transitions out of CONNECTED.

        Polls the API state — the event_registry shares
        ``status_changed`` with all listeners and doesn't auto-reset,
        so polling is the simplest race-free observer.
        """
        while not self._stopping:
            status = self.api.state.status
            if status in (
                    WebsocketStatus.DISCONNECTED, WebsocketStatus.ERROR
            ):
                return
            await asyncio.sleep(self.poll_interval)

    async def _reconnect_with_backoff(self) -> None:
        attempt = 0
        delay = self.policy.initial_delay
        while not self._stopping:
            if (
                    self.policy.max_attempts >= 0
                    and attempt >= self.policy.max_attempts
            ):
                logger.error(
                    "Reconnect: gave up after %d attempts", attempt
                )
                # Final state — stop trying so we don't hot-loop on a
                # status that never recovers.
                self._stopping = True
                return
            attempt += 1
            self.stats.attempts += 1

            jitter = random.uniform(0, self.policy.jitter * delay)
            wait = min(delay + jitter, self.policy.max_delay)
            logger.warning(
                "Reconnect attempt %d in %.1fs", attempt, wait
            )
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                return

            ok = await self._attempt_reconnect()
            if ok:
                self.stats.successful_reconnects += 1
                self.stats.last_reconnect_at = time.time()
                await self._replay_state()
                await self._fire_callbacks()
                return

            self.stats.failed_reconnects += 1
            delay = min(delay * self.policy.backoff_factor, self.policy.max_delay)

    async def _attempt_reconnect(self) -> bool:
        try:
            ok, reason = await self.api.start_websocket()
            if not ok:
                self.stats.last_error = str(reason)
                return False
            await self.api.send_ssid()
            return True
        except Exception as e:
            logger.warning("Reconnect attempt failed: %s", e)
            self.stats.last_error = str(e)
            return False

    async def _replay_state(self) -> None:
        try:
            if self._snapshot.account_type is not None:
                await self.api.change_account(
                    self._snapshot.account_type,
                    tournament_id=self._snapshot.tournament_id,
                )
        except Exception as e:
            logger.warning("Failed to replay account_type: %s", e)

        client = getattr(self.api, "_client_ref", None)
        if client is None:
            return

        for asset, size in self._snapshot.candle_subs:
            try:
                await client.start_candles_one_stream(asset, size)
            except Exception as e:
                logger.warning(
                    "Failed to replay candle sub %s,%s: %s", asset, size, e
                )
        for asset in self._snapshot.candle_all_size_subs:
            try:
                await client.start_candles_all_size_stream(asset)
            except Exception as e:
                logger.warning(
                    "Failed to replay all_size sub %s: %s", asset, e
                )
        for asset in self._snapshot.mood_subs:
            try:
                await client.start_mood_stream(asset)
            except Exception as e:
                logger.warning(
                    "Failed to replay mood sub %s: %s", asset, e
                )

    async def _fire_callbacks(self) -> None:
        for cb in self._on_reconnect:
            try:
                result = cb()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("Reconnect callback failed: %s", e)
