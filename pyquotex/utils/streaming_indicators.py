"""Incremental (streaming) technical indicators.

The batch versions in :mod:`pyquotex.utils.indicators` are still useful
for one-off computations on a full candle history, but they recompute
the entire series on every tick — ``O(n)`` per update. When you are
subscribing to a live candle stream you want ``O(1)`` per update.

These classes keep a fixed-size :class:`collections.deque` of the last
``period`` samples and a running aggregate (sum / sum-of-squares /
Wilder-smoothed gain & loss / EMA) so each call to :meth:`update` is
constant-time.

Usage
-----

>>> sma = StreamingSMA(period=20)
>>> for price in tick_stream:
...     value = sma.update(price)
...     if value is not None:
...         print(value)  # warmed up
"""

from __future__ import annotations

import statistics
from collections import deque
from typing import Iterable


class StreamingSMA:
    """Simple Moving Average, ``O(1)`` per update via running sum."""

    __slots__ = ("_period", "_window", "_sum")

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self._period = period
        self._window: deque[float] = deque(maxlen=period)
        self._sum: float = 0.0

    @property
    def period(self) -> int:
        return self._period

    @property
    def ready(self) -> bool:
        return len(self._window) == self._period

    def update(self, price: float) -> float | None:
        """Push a new price; return the new SMA value (or ``None`` if warming up)."""
        if len(self._window) == self._period:
            self._sum -= self._window[0]
        self._window.append(price)
        self._sum += price
        if not self.ready:
            return None
        return self._sum / self._period

    def seed(self, prices: Iterable[float]) -> float | None:
        """Replay a batch of prices to warm up. Returns the latest value."""
        result: float | None = None
        for p in prices:
            result = self.update(p)
        return result


class StreamingEMA:
    """Exponential Moving Average with the classic ``2/(n+1)`` smoothing."""

    __slots__ = ("_period", "_alpha", "_value", "_warmup", "_target")

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self._period = period
        self._alpha = 2.0 / (period + 1.0)
        self._value: float | None = None
        self._warmup: float = 0.0
        self._target: int = period  # SMA-seeded warm-up

    @property
    def period(self) -> int:
        return self._period

    @property
    def ready(self) -> bool:
        return self._value is not None

    @property
    def value(self) -> float | None:
        return self._value

    def update(self, price: float) -> float | None:
        if self._value is None:
            self._warmup += price
            self._target -= 1
            if self._target == 0:
                self._value = self._warmup / self._period
            return self._value
        self._value = price * self._alpha + self._value * (1.0 - self._alpha)
        return self._value

    def seed(self, prices: Iterable[float]) -> float | None:
        result: float | None = None
        for p in prices:
            result = self.update(p)
        return result


class StreamingRSI:
    """Wilder-smoothed RSI, ``O(1)`` per update."""

    __slots__ = (
        "_period",
        "_prev",
        "_avg_gain",
        "_avg_loss",
        "_count",
        "_warm_gain",
        "_warm_loss",
    )

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self._period = period
        self._prev: float | None = None
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._count = 0
        self._warm_gain = 0.0
        self._warm_loss = 0.0

    @property
    def ready(self) -> bool:
        return self._avg_gain is not None

    def update(self, price: float) -> float | None:
        if self._prev is None:
            self._prev = price
            return None

        delta = price - self._prev
        self._prev = price
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0

        if self._avg_gain is None:
            self._warm_gain += gain
            self._warm_loss += loss
            self._count += 1
            if self._count >= self._period:
                self._avg_gain = self._warm_gain / self._period
                self._avg_loss = self._warm_loss / self._period
            else:
                return None
        else:
            p = self._period
            self._avg_gain = (self._avg_gain * (p - 1) + gain) / p
            self._avg_loss = (self._avg_loss * (p - 1) + loss) / p

        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss  # type: ignore[operator]
        return 100.0 - (100.0 / (1.0 + rs))

    def seed(self, prices: Iterable[float]) -> float | None:
        result: float | None = None
        for p in prices:
            result = self.update(p)
        return result


class StreamingBollinger:
    """Bollinger Bands with rolling pstdev computed from the SMA window."""

    __slots__ = ("_period", "_num_std", "_window", "_sum")

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        if period < 2:
            raise ValueError("period must be >= 2")
        self._period = period
        self._num_std = num_std
        self._window: deque[float] = deque(maxlen=period)
        self._sum: float = 0.0

    @property
    def ready(self) -> bool:
        return len(self._window) == self._period

    def update(self, price: float) -> tuple[float, float, float] | None:
        """Return ``(upper, middle, lower)`` once warmed up."""
        if len(self._window) == self._period:
            self._sum -= self._window[0]
        self._window.append(price)
        self._sum += price
        if not self.ready:
            return None
        middle = self._sum / self._period
        std = statistics.pstdev(self._window)
        return (
            middle + self._num_std * std,
            middle,
            middle - self._num_std * std,
        )

    def seed(self, prices: Iterable[float]) -> tuple[float, float, float] | None:
        result: tuple[float, float, float] | None = None
        for p in prices:
            result = self.update(p)
        return result


__all__ = [
    "StreamingBollinger",
    "StreamingEMA",
    "StreamingRSI",
    "StreamingSMA",
]
