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
            store: "SentimentStore | None" = None,
    ):
        self.thresholds = thresholds or SentimentThresholds()
        self.history_size = history_size
        self.store = store
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

        if self.store is not None:
            try:
                self.store.write(snapshot, price=price)
            except Exception as e:
                logger.warning("sentiment store write failed: %s", e)

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


class SentimentStore:
    """SQLite-backed persistence layer for sentiment snapshots.

    One row per ``(asset, timestamp)``; safe to share across multiple
    monitors. Backtesting code can replay any window via :meth:`query`.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS sentiment_snapshots (
        asset TEXT NOT NULL,
        timestamp REAL NOT NULL,
        bullish REAL NOT NULL,
        bearish REAL NOT NULL,
        price REAL,
        PRIMARY KEY (asset, timestamp)
    );
    CREATE INDEX IF NOT EXISTS idx_sentiment_asset_ts
        ON sentiment_snapshots (asset, timestamp);
    """

    def __init__(self, path: str = ":memory:"):
        import sqlite3

        self.path = str(path)
        self._conn = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self._conn.executescript(self._SCHEMA)
        self._closed = False

    def write(
            self, snapshot: SentimentSnapshot, price: float | None = None
    ) -> None:
        if self._closed:
            # Use-after-close is silently a no-op rather than a crash.
            # The sentiment monitor task may briefly outlive the store
            # during shutdown; we'd rather drop a sample than tear down
            # the live websocket consumer with a sqlite ProgrammingError.
            logger.debug("SentimentStore.write() skipped — store is closed")
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO sentiment_snapshots "
            "(asset, timestamp, bullish, bearish, price) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                snapshot.asset,
                snapshot.timestamp,
                snapshot.bullish,
                snapshot.bearish,
                price,
            ),
        )

    def query(
            self,
            asset: str,
            start: float | None = None,
            end: float | None = None,
            limit: int | None = None,
    ) -> list[SentimentSnapshot]:
        if self._closed:
            return []
        sql = (
            "SELECT asset, timestamp, bullish, bearish "
            "FROM sentiment_snapshots WHERE asset = ?"
        )
        params: list[Any] = [asset]
        if start is not None:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end is not None:
            sql += " AND timestamp <= ?"
            params.append(end)
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [
            SentimentSnapshot(
                asset=r[0], timestamp=r[1], bullish=r[2], bearish=r[3]
            )
            for r in rows
        ]

    def query_latest(
            self, asset: str, limit: int
    ) -> list[SentimentSnapshot]:
        """Return the most-recent ``limit`` rows for ``asset`` in
        chronological (ASC timestamp) order.

        ``query(limit=N)`` returns the oldest N rows, which is rarely
        what an analyzer wants — use this helper for "latest window".
        """
        if self._closed:
            return []
        rows = self._conn.execute(
            "SELECT asset, timestamp, bullish, bearish "
            "FROM sentiment_snapshots WHERE asset = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (asset, int(limit)),
        ).fetchall()
        rows.reverse()
        return [
            SentimentSnapshot(
                asset=r[0], timestamp=r[1], bullish=r[2], bearish=r[3]
            )
            for r in rows
        ]

    def assets(self) -> list[str]:
        if self._closed:
            return []
        rows = self._conn.execute(
            "SELECT DISTINCT asset FROM sentiment_snapshots"
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._conn.close()


class SentimentCorrelationAnalyzer:
    """Compute pairwise sentiment correlations across multiple assets.

    Reads recent samples from a :class:`SentimentMonitor` (in-memory) or
    :class:`SentimentStore` (persisted) and returns Pearson correlations
    of the signed bias time series, plus a divergence helper that
    surfaces decoupled asset pairs.
    """

    def __init__(
            self,
            monitor: "SentimentMonitor | None" = None,
            store: "SentimentStore | None" = None,
    ):
        if monitor is None and store is None:
            raise ValueError("Pass either monitor or store.")
        self.monitor = monitor
        self.store = store

    def correlation_matrix(
            self,
            assets: list[str],
            window: int = 60,
    ) -> dict[tuple[str, str], float]:
        """Return Pearson correlations for every distinct asset pair.

        Time series are aligned by index (most recent ``window``
        samples). Pairs without enough overlap are omitted from the
        result.
        """
        series = self._collect_series(assets, window)
        out: dict[tuple[str, str], float] = {}
        for i, a in enumerate(assets):
            for b in assets[i + 1:]:
                xs = series.get(a)
                ys = series.get(b)
                if not xs or not ys:
                    continue
                length = min(len(xs), len(ys))
                if length < 2:
                    continue
                corr = _pearson(xs[-length:], ys[-length:])
                if corr is not None:
                    out[(a, b)] = corr
        return out

    def find_divergences(
            self,
            assets: list[str],
            window: int = 60,
            threshold: float = 0.0,
    ) -> list[tuple[str, str, float]]:
        """Return ``(asset_a, asset_b, correlation)`` triples whose
        correlation is at or below ``threshold`` — i.e. the assets'
        sentiments are moving in opposite directions."""
        matrix = self.correlation_matrix(assets, window=window)
        return sorted(
            [
                (a, b, c) for (a, b), c in matrix.items()
                if c <= threshold
            ],
            key=lambda x: x[2],
        )

    def _collect_series(
            self, assets: list[str], window: int
    ) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for asset in assets:
            snaps: list[SentimentSnapshot] = []
            if self.monitor is not None:
                snaps = self.monitor.history(asset)[-window:]
            elif self.store is not None:
                # query_latest gives the most-recent ``window`` rows
                # in ASC order — matches what the monitor returns.
                snaps = self.store.query_latest(asset, limit=window)
            if snaps:
                out[asset] = [s.bias for s in snaps]
        return out
