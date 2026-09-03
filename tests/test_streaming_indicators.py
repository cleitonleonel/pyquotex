"""Tests for the incremental streaming indicators."""

import pytest

from pyquotex.utils.indicators import TechnicalIndicators
from pyquotex.utils.streaming_indicators import (
    StreamingBollinger,
    StreamingEMA,
    StreamingRSI,
    StreamingSMA,
)


@pytest.mark.unit
class TestStreamingSMA:
    def test_returns_none_until_warmed(self) -> None:
        sma = StreamingSMA(period=3)
        assert sma.update(1.0) is None
        assert sma.update(2.0) is None
        assert sma.update(3.0) == pytest.approx(2.0)
        assert sma.update(4.0) == pytest.approx(3.0)

    def test_matches_batch_implementation(self) -> None:
        prices = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        batch = TechnicalIndicators.calculate_sma(prices, 3)
        streaming = StreamingSMA(3)
        out = [streaming.update(p) for p in prices]
        non_none = [round(x, 2) for x in out if x is not None]
        assert non_none == batch

    def test_rejects_invalid_period(self) -> None:
        with pytest.raises(ValueError):
            StreamingSMA(0)


@pytest.mark.unit
class TestStreamingEMA:
    def test_warms_via_sma_seed(self) -> None:
        ema = StreamingEMA(period=3)
        ema.update(1.0)
        ema.update(2.0)
        warmed = ema.update(3.0)
        assert warmed == pytest.approx(2.0)  # SMA seed

    def test_post_warm_uses_alpha(self) -> None:
        ema = StreamingEMA(period=2)
        ema.update(1.0)
        ema.update(3.0)  # warmed: (1+3)/2 = 2
        v = ema.update(5.0)  # alpha = 2/3; new = 5*2/3 + 2*1/3
        assert v == pytest.approx(5 * 2 / 3 + 2 * 1 / 3)


@pytest.mark.unit
class TestStreamingRSI:
    def test_constant_prices_return_neutral_when_warmed(self) -> None:
        rsi = StreamingRSI(period=3)
        outs = [rsi.update(1.0) for _ in range(5)]
        # No movement → avg_loss == avg_gain == 0 → returns 100 (max signal,
        # by Wilder convention when loss == 0).
        assert outs[-1] == 100.0

    def test_monotonic_up_pushes_rsi_high(self) -> None:
        rsi = StreamingRSI(period=3)
        outs = [rsi.update(p) for p in [1.0, 2.0, 3.0, 4.0, 5.0]]
        assert outs[-1] is not None and outs[-1] > 80


@pytest.mark.unit
class TestStreamingBollinger:
    def test_returns_triplet_when_warmed(self) -> None:
        bb = StreamingBollinger(period=3, num_std=2)
        assert bb.update(1.0) is None
        assert bb.update(2.0) is None
        result = bb.update(3.0)
        assert result is not None
        upper, middle, lower = result
        assert middle == pytest.approx(2.0)
        assert upper > middle > lower
