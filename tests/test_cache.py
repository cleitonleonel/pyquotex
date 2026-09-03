"""Tests for ``pyquotex.utils.cache.TTLCache``."""

import time

import pytest

from pyquotex.utils.cache import TTLCache


@pytest.mark.unit
def test_set_and_get() -> None:
    c: TTLCache[str, int] = TTLCache(maxsize=4, ttl=1.0)
    c.set("a", 1)
    assert c.get("a") == 1


@pytest.mark.unit
def test_lru_eviction_on_overflow() -> None:
    c: TTLCache[str, int] = TTLCache(maxsize=2, ttl=60)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") is None  # evicted as least-recent
    assert c.get("b") == 2
    assert c.get("c") == 3


@pytest.mark.unit
def test_get_moves_to_end() -> None:
    c: TTLCache[str, int] = TTLCache(maxsize=2, ttl=60)
    c.set("a", 1)
    c.set("b", 2)
    _ = c.get("a")
    c.set("c", 3)  # should evict 'b', not 'a' since 'a' was accessed
    assert c.get("a") == 1
    assert c.get("b") is None


@pytest.mark.unit
def test_lazy_expiration() -> None:
    c: TTLCache[str, int] = TTLCache(maxsize=4, ttl=0.05)
    c.set("a", 1)
    time.sleep(0.07)
    assert c.get("a") is None


@pytest.mark.unit
def test_invalidate_and_clear() -> None:
    c: TTLCache[str, int] = TTLCache(maxsize=4, ttl=60)
    c.set("a", 1)
    c.set("b", 2)
    c.invalidate("a")
    assert c.get("a") is None
    c.clear()
    assert c.get("b") is None
    assert len(c) == 0


@pytest.mark.unit
def test_rejects_bad_params() -> None:
    with pytest.raises(ValueError):
        TTLCache(maxsize=0, ttl=10)
    with pytest.raises(ValueError):
        TTLCache(maxsize=4, ttl=0)
