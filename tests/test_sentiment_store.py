"""Tests for SentimentStore persistence and SentimentCorrelationAnalyzer."""
import asyncio

import pytest

from pyquotex.utils.sentiment import (
    SentimentCorrelationAnalyzer,
    SentimentMonitor,
    SentimentSnapshot,
    SentimentStore,
)


def test_store_write_and_query():
    store = SentimentStore(":memory:")
    snap = SentimentSnapshot(
        asset="EURUSD", bullish=0.6, bearish=0.4, timestamp=1000.0
    )
    store.write(snap, price=1.1)
    rows = store.query("EURUSD")
    assert len(rows) == 1
    assert rows[0].bullish == 0.6
    assert rows[0].timestamp == 1000.0
    store.close()


def test_store_filters_by_time_range():
    store = SentimentStore(":memory:")
    for ts in (100, 200, 300, 400):
        store.write(SentimentSnapshot("X", 0.5, 0.5, ts))
    rows = store.query("X", start=200, end=300)
    assert [r.timestamp for r in rows] == [200, 300]
    store.close()


def test_store_replaces_duplicate_primary_key():
    store = SentimentStore(":memory:")
    store.write(SentimentSnapshot("X", 0.5, 0.5, 100.0))
    store.write(SentimentSnapshot("X", 0.9, 0.1, 100.0))  # same (asset, ts)
    rows = store.query("X")
    assert len(rows) == 1
    assert rows[0].bullish == 0.9
    store.close()


def test_store_assets_lists_distinct():
    store = SentimentStore(":memory:")
    store.write(SentimentSnapshot("A", 0.5, 0.5, 1.0))
    store.write(SentimentSnapshot("B", 0.5, 0.5, 1.0))
    store.write(SentimentSnapshot("A", 0.6, 0.4, 2.0))
    assert sorted(store.assets()) == ["A", "B"]
    store.close()


def test_query_latest_returns_most_recent_in_asc_order():
    store = SentimentStore(":memory:")
    for ts in range(100):
        store.write(SentimentSnapshot("X", 0.5, 0.5, float(ts)))
    rows = store.query_latest("X", limit=5)
    # 5 most recent samples (ts 95..99) in ASC order.
    assert [r.timestamp for r in rows] == [95.0, 96.0, 97.0, 98.0, 99.0]
    store.close()


def test_store_write_after_close_is_silent_noop():
    """A live SentimentMonitor task may briefly outlive its store
    during shutdown. Writing to a closed store must be a logged
    no-op rather than a sqlite ProgrammingError that tears down the
    websocket consumer."""
    store = SentimentStore(":memory:")
    store.close()

    # Both write and the read methods must handle the closed state.
    store.write(SentimentSnapshot("X", 0.5, 0.5, 1.0))
    assert store.query("X") == []
    assert store.query_latest("X", limit=10) == []
    assert store.assets() == []
    # Idempotent close.
    store.close()


def test_correlation_via_store_uses_latest_window():
    store = SentimentStore(":memory:")
    # First 50 rows: A and B perfectly aligned (corr ≈ +1).
    for i in range(50):
        store.write(SentimentSnapshot("A", 0.5 + i * 0.01, 0.5 - i * 0.01, i))
        store.write(SentimentSnapshot("B", 0.5 + i * 0.01, 0.5 - i * 0.01, i))
    # Last 20 rows: B inverted (corr should be ≈ -1 over the latest window).
    for j in range(20):
        ts = 100 + j
        store.write(SentimentSnapshot("A", 0.5 + j * 0.01, 0.5 - j * 0.01, ts))
        store.write(SentimentSnapshot("B", 0.5 - j * 0.01, 0.5 + j * 0.01, ts))
    analyzer = SentimentCorrelationAnalyzer(store=store)
    matrix = analyzer.correlation_matrix(["A", "B"], window=20)
    assert matrix[("A", "B")] < -0.9, (
        "analyzer must read the latest window, not the oldest rows"
    )
    store.close()


@pytest.mark.asyncio
async def test_monitor_writes_to_store():
    store = SentimentStore(":memory:")
    mon = SentimentMonitor(store=store)
    for i in range(5):
        await mon.feed("EURUSD", 0.6, 0.4, price=1.0 + i, timestamp=1000 + i)
    rows = store.query("EURUSD")
    assert len(rows) == 5
    store.close()


@pytest.mark.asyncio
async def test_correlation_matrix_negative_for_inverse_series():
    mon = SentimentMonitor()
    # EURUSD ramps up; GBPUSD ramps down
    for i in range(20):
        await mon.feed(
            "EURUSD", 0.5 + i * 0.02, 0.5 - i * 0.02, timestamp=i
        )
        await mon.feed(
            "GBPUSD", 0.5 - i * 0.02, 0.5 + i * 0.02, timestamp=i
        )
    analyzer = SentimentCorrelationAnalyzer(monitor=mon)
    matrix = analyzer.correlation_matrix(["EURUSD", "GBPUSD"], window=15)
    assert ("EURUSD", "GBPUSD") in matrix
    assert matrix[("EURUSD", "GBPUSD")] < -0.9


@pytest.mark.asyncio
async def test_correlation_matrix_positive_for_aligned_series():
    mon = SentimentMonitor()
    for i in range(20):
        await mon.feed("A", 0.5 + i * 0.02, 0.5 - i * 0.02, timestamp=i)
        await mon.feed("B", 0.5 + i * 0.02, 0.5 - i * 0.02, timestamp=i)
    analyzer = SentimentCorrelationAnalyzer(monitor=mon)
    matrix = analyzer.correlation_matrix(["A", "B"], window=15)
    assert matrix[("A", "B")] > 0.9


@pytest.mark.asyncio
async def test_find_divergences_returns_only_anti_correlated():
    mon = SentimentMonitor()
    for i in range(20):
        await mon.feed("A", 0.5 + i * 0.02, 0.5 - i * 0.02, timestamp=i)
        await mon.feed("B", 0.5 - i * 0.02, 0.5 + i * 0.02, timestamp=i)
        await mon.feed("C", 0.5 + i * 0.02, 0.5 - i * 0.02, timestamp=i)
    analyzer = SentimentCorrelationAnalyzer(monitor=mon)
    divs = analyzer.find_divergences(["A", "B", "C"], window=15, threshold=-0.5)
    pairs = [(a, b) for a, b, _ in divs]
    assert ("A", "B") in pairs
    assert ("A", "C") not in pairs


def test_analyzer_requires_monitor_or_store():
    with pytest.raises(ValueError):
        SentimentCorrelationAnalyzer()


def test_analyzer_reads_from_store():
    store = SentimentStore(":memory:")
    for i in range(15):
        store.write(SentimentSnapshot("A", 0.5 + i * 0.02, 0.5 - i * 0.02, i))
        store.write(SentimentSnapshot("B", 0.5 - i * 0.02, 0.5 + i * 0.02, i))
    analyzer = SentimentCorrelationAnalyzer(store=store)
    matrix = analyzer.correlation_matrix(["A", "B"], window=15)
    assert matrix[("A", "B")] < -0.9
    store.close()
