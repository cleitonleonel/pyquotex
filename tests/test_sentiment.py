"""Unit tests for SentimentMonitor."""
import asyncio

import pytest

from pyquotex.utils.sentiment import (
    SentimentMonitor,
    SentimentSignal,
    SentimentSnapshot,
    SentimentThresholds,
)


@pytest.mark.asyncio
async def test_feed_records_history_and_latest():
    mon = SentimentMonitor()
    await mon.feed("EURUSD", 0.6, 0.4)
    await mon.feed("EURUSD", 0.7, 0.3)
    history = mon.history("EURUSD")
    assert len(history) == 2
    assert mon.latest("EURUSD").bullish == 0.7


def test_snapshot_bias_signed_in_unit_range():
    s = SentimentSnapshot(asset="X", bullish=0.8, bearish=0.2, timestamp=0)
    assert s.bias == pytest.approx(0.6)
    s = SentimentSnapshot(asset="X", bullish=0.2, bearish=0.8, timestamp=0)
    assert s.bias == pytest.approx(-0.6)
    s = SentimentSnapshot(asset="X", bullish=0, bearish=0, timestamp=0)
    assert s.bias == 0.0


def test_feed_raw_normalizes_percent():
    mon = SentimentMonitor()
    bull, bear = mon.feed_raw("X", {"bullish": 75, "bearish": 25})
    assert bull == 0.75
    assert bear == 0.25


def test_feed_raw_alt_keys():
    mon = SentimentMonitor()
    bull, bear = mon.feed_raw("X", {"sentimentBuy": 0.8, "sentimentSell": 0.2})
    assert bull == 0.8
    assert bear == 0.2


def test_feed_raw_buy_sell_alias():
    mon = SentimentMonitor()
    bull, bear = mon.feed_raw("X", {"buy": 60, "sell": 40})
    assert bull == 0.6
    assert bear == 0.4


@pytest.mark.asyncio
async def test_extreme_bullish_signal_emitted():
    mon = SentimentMonitor(
        thresholds=SentimentThresholds(extreme_bullish=0.5)
    )
    fired: list[SentimentSignal] = []
    mon.on_signal(lambda s: fired.append(s))
    await mon.feed("EURUSD", 0.95, 0.05)
    assert len(fired) == 1
    assert fired[0].kind == "extreme_bullish"
    assert fired[0].asset == "EURUSD"
    assert fired[0].detail["bias"] > 0.5


@pytest.mark.asyncio
async def test_extreme_bearish_signal_emitted():
    mon = SentimentMonitor(
        thresholds=SentimentThresholds(extreme_bearish=-0.5)
    )
    fired: list[str] = []
    mon.on_signal(lambda s: fired.append(s.kind))
    await mon.feed("EURUSD", 0.05, 0.95)
    assert "extreme_bearish" in fired


@pytest.mark.asyncio
async def test_spike_signal_via_zscore():
    mon = SentimentMonitor(
        thresholds=SentimentThresholds(
            extreme_bullish=2.0,  # disable extremes
            extreme_bearish=-2.0,
            spike_zscore=2.0,
        )
    )
    fired: list[str] = []
    mon.on_signal(lambda s: fired.append(s.kind))
    # A few samples near 0.5 build a tight baseline
    for _ in range(10):
        await mon.feed("X", 0.50, 0.50)
        await mon.feed("X", 0.52, 0.48)
    # Sudden swing → spike
    await mon.feed("X", 0.95, 0.05)
    assert "spike" in fired


@pytest.mark.asyncio
async def test_divergence_signal_when_bias_rises_with_falling_price():
    mon = SentimentMonitor(
        thresholds=SentimentThresholds(
            extreme_bullish=2.0,
            extreme_bearish=-2.0,
            spike_zscore=10.0,
            divergence_window=10,
            divergence_threshold=-0.5,
        )
    )
    fired: list[str] = []
    mon.on_signal(lambda s: fired.append(s.kind))
    biases = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55]
    prices = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91]
    for b, p in zip(biases, prices):
        await mon.feed("BTCUSD", (1 + b) / 2, (1 - b) / 2, price=p)
    assert "divergence" in fired


@pytest.mark.asyncio
async def test_cooldown_debounces_repeated_signals():
    mon = SentimentMonitor(
        thresholds=SentimentThresholds(extreme_bullish=0.5)
    )
    fired: list[str] = []
    mon.on_signal(lambda s: fired.append(s.kind))
    for _ in range(5):
        await mon.feed("X", 0.95, 0.05)
    assert fired.count("extreme_bullish") == 1


@pytest.mark.asyncio
async def test_async_callback_awaited():
    mon = SentimentMonitor(
        thresholds=SentimentThresholds(extreme_bullish=0.5)
    )
    seen = asyncio.Event()

    async def on_signal(_):
        seen.set()

    mon.on_signal(on_signal)
    await mon.feed("X", 0.95, 0.05)
    assert seen.is_set()


def test_history_size_caps_buffer():
    mon = SentimentMonitor(history_size=3)
    asyncio.run(_push_many(mon))
    assert len(mon.history("X")) == 3


async def _push_many(mon: SentimentMonitor) -> None:
    for _ in range(10):
        await mon.feed("X", 0.5, 0.5)
