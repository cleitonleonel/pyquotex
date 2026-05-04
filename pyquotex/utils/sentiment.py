"""Advanced sentiment monitoring for trader mood streams.

The open-source build only exposes a raw ``sentiment`` snapshot per
asset. This module wraps that stream with rolling history, statistical
anomaly detection, threshold callbacks, and price/sentiment divergence
analysis.
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

SentimentCallback = Callable[["SentimentSignal"], Awaitable[None] | None]


@dataclass
class SentimentSnapshot:
    """A single sentiment reading for an asset."""

    asset: str
    bullish: float
    bearish: float
    timestamp: float

    @property
    def bias(self) -> float:
        """Signed bias in ``[-1, 1]``: +1 fully bullish, -1 fully bearish."""
        total = self.bullish + self.bearish
        if total <= 0:
            return 0.0
        return (self.bullish - self.bearish) / total


@dataclass
class SentimentSignal:
    """A higher-level event derived from sentiment history."""

    asset: str
    kind: str  # "extreme_bullish", "extreme_bearish", "spike", "divergence"
    snapshot: SentimentSnapshot
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class SentimentThresholds:
    """Thresholds that trigger ``SentimentSignal`` events.

    Attributes:
        extreme_bullish: Bias above this value emits ``extreme_bullish``.
        extreme_bearish: Bias below this value emits ``extreme_bearish``.
        spike_zscore: Absolute z-score of bias change to emit ``spike``.
        divergence_window: Samples used for price/sentiment correlation.
        divergence_threshold: Negative correlation below this emits
            ``divergence``.
    """

    extreme_bullish: float = 0.6
    extreme_bearish: float = -0.6
    spike_zscore: float = 2.5
    divergence_window: int = 20
    divergence_threshold: float = -0.5


class SentimentMonitor:
    """Maintain rolling sentiment state per asset and emit signals."""

    def __init__(
            self,
            thresholds: SentimentThresholds | None = None,
            history_size: int = 256,
    ):
        self.thresholds = thresholds or SentimentThresholds()
        self.history_size = history_size
        self._history: dict[str, deque[SentimentSnapshot]] = defaultdict(
            lambda: deque(maxlen=self.history_size)
        )
        self._prices: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.history_size)
        )
        self._listeners: list[SentimentCallback] = []
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._cooldown_seconds: float = 5.0

    def on_signal(self, callback: SentimentCallback) -> None:
        """Register a callback fired for every ``SentimentSignal``."""
        self._listeners.append(callback)

    def history(self, asset: str) -> list[SentimentSnapshot]:
        return list(self._history.get(asset, ()))

    def latest(self, asset: str) -> SentimentSnapshot | None:
        items = self._history.get(asset)
        return items[-1] if items else None

    async def feed(
            self,
            asset: str,
            bullish: float,
            bearish: float,
            price: float | None = None,
            timestamp: float | None = None,
    ) -> list[SentimentSignal]:
        """Ingest a sentiment sample and return any emitted signals."""
        snapshot = SentimentSnapshot(
            asset=asset,
            bullish=float(bullish),
            bearish=float(bearish),
            timestamp=timestamp if timestamp is not None else time.time(),
        )
        self._history[asset].append(snapshot)
        if price is not None:
            self._prices[asset].append(float(price))

        signals: list[SentimentSignal] = []
        signals.extend(self._check_extremes(snapshot))
        spike = self._check_spike(asset, snapshot)
        if spike:
            signals.append(spike)
        divergence = self._check_divergence(asset, snapshot)
        if divergence:
            signals.append(divergence)

        for sig in signals:
            await self._dispatch(sig)
        return signals

    def feed_raw(
            self, asset: str, raw: dict[str, Any]
    ) -> tuple[float, float]:
        """Normalize a raw sentiment payload from the websocket.

        Returns the parsed ``(bullish, bearish)`` percentages.
        """
        bullish = (
            raw.get("bullish")
            or raw.get("sentimentBuy")
            or raw.get("buy")
            or 0
        )
        bearish = (
            raw.get("bearish")
            or raw.get("sentimentSell")
            or raw.get("sell")
            or 0
        )
        if isinstance(bullish, (int, float)) and bullish > 1:
            bullish = bullish / 100.0
        if isinstance(bearish, (int, float)) and bearish > 1:
            bearish = bearish / 100.0
        return float(bullish), float(bearish)

    def _check_extremes(
            self, snap: SentimentSnapshot
    ) -> list[SentimentSignal]:
        out: list[SentimentSignal] = []
        bias = snap.bias
        if bias >= self.thresholds.extreme_bullish:
            out.append(SentimentSignal(
                asset=snap.asset,
                kind="extreme_bullish",
                snapshot=snap,
                detail={"bias": bias},
            ))
        elif bias <= self.thresholds.extreme_bearish:
            out.append(SentimentSignal(
                asset=snap.asset,
                kind="extreme_bearish",
                snapshot=snap,
                detail={"bias": bias},
            ))
        return out

    def _check_spike(
            self, asset: str, snap: SentimentSnapshot
    ) -> SentimentSignal | None:
        history = list(self._history[asset])
        if len(history) < 5:
            return None
        biases = [s.bias for s in history[:-1]]
        try:
            mean = statistics.fmean(biases)
            stdev = statistics.pstdev(biases)
        except statistics.StatisticsError:
            return None
        if stdev == 0 or math.isnan(stdev):
            return None
        z = (snap.bias - mean) / stdev
        if abs(z) < self.thresholds.spike_zscore:
            return None
        return SentimentSignal(
            asset=asset,
            kind="spike",
            snapshot=snap,
            detail={"zscore": z, "mean": mean, "stdev": stdev},
        )

    def _check_divergence(
            self, asset: str, snap: SentimentSnapshot
    ) -> SentimentSignal | None:
        window = self.thresholds.divergence_window
        history = list(self._history[asset])[-window:]
        prices = list(self._prices[asset])[-window:]
        if len(history) < window or len(prices) < window:
            return None
        biases = [s.bias for s in history]
        corr = _pearson(biases, prices)
        if corr is None or corr > self.thresholds.divergence_threshold:
            return None
        return SentimentSignal(
            asset=asset,
            kind="divergence",
            snapshot=snap,
            detail={"correlation": corr, "window": window},
        )

    async def _dispatch(self, signal: SentimentSignal) -> None:
        key = (signal.asset, signal.kind)
        now = time.time()
        if now - self._cooldowns.get(key, 0.0) < self._cooldown_seconds:
            return
        self._cooldowns[key] = now
        for cb in self._listeners:
            try:
                result = cb(signal)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("sentiment listener failed: %s", e)


def _pearson(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    mean_a = statistics.fmean(a)
    mean_b = statistics.fmean(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denom_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    denom_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if denom_a == 0 or denom_b == 0:
        return None
    return num / (denom_a * denom_b)
