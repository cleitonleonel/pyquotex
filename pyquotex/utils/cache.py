"""Small async-friendly TTL+LRU caches used by mixins.

The point of these caches is to absorb burst-y duplicate requests from
strategies that call ``get_candles`` once per tick with the same
arguments. We intentionally keep the TTL short (default: one candle
period) so live data is never served stale.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """LRU cache with per-entry TTL.

    Not thread-safe but cooperative-async-safe: a single event loop only
    interleaves at ``await`` points, and every operation here is sync.
    """

    __slots__ = ("_maxsize", "_ttl", "_store")

    def __init__(self, maxsize: int = 64, ttl: float = 30.0) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: OrderedDict[K, tuple[float, V]] = OrderedDict()

    @property
    def ttl(self) -> float:
        return self._ttl

    def get(self, key: K) -> V | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            # Lazy expiration
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: K, value: V, ttl: float | None = None) -> None:
        effective_ttl = self._ttl if ttl is None else ttl
        expires_at = time.monotonic() + effective_ttl
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (expires_at, value)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def invalidate(self, key: K) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None


__all__ = ["TTLCache"]
