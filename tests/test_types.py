"""Tests for the new public dataclasses in ``pyquotex.types``."""

import pytest

from pyquotex.qxtypes import (
    AssetInfo,
    Balance,
    Candle,
    ProfileInfo,
    ReconnectPolicy,
    Subscription,
    TradeResult,
)


@pytest.mark.unit
class TestCandle:
    def test_from_dict_full(self) -> None:
        c = Candle.from_dict(
            {"time": 1, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5, "volume": 100}
        )
        assert (c.time, c.open, c.high, c.low, c.close, c.volume) == (1, 2.0, 3.0, 1.5, 2.5, 100.0)

    def test_from_array_orders_match_broker(self) -> None:
        # broker order: [t, o, c, h, l]
        c = Candle.from_array([10, 1.0, 1.4, 1.5, 0.8])
        assert c.time == 10
        assert c.open == 1.0
        assert c.close == 1.4
        assert c.high == 1.5
        assert c.low == 0.8

    def test_from_array_rejects_short(self) -> None:
        with pytest.raises(ValueError):
            Candle.from_array([1, 2, 3])

    @pytest.mark.parametrize(
        "open_,close,expected",
        [(1.0, 1.5, "green"), (1.5, 1.0, "red"), (1.0, 1.0, "doji")],
    )
    def test_color(self, open_: float, close: float, expected: str) -> None:
        c = Candle(time=0, open=open_, high=2, low=0.5, close=close)
        assert c.color == expected

    def test_is_frozen(self) -> None:
        c = Candle(time=0, open=1, high=1, low=1, close=1)
        with pytest.raises(Exception):
            c.time = 99  # type: ignore[misc]


@pytest.mark.unit
def test_trade_result_from_dict_infers_status() -> None:
    win = TradeResult.from_dict({"id": "t1", "profit": 5.0, "asset": "EURUSD"})
    assert win.status == "win"
    loss = TradeResult.from_dict({"ticket": "t2", "profit": -2.0})
    assert loss.status == "loss"
    draw = TradeResult.from_dict({"id": "t3", "profit": 0})
    assert draw.status == "draw"


@pytest.mark.unit
def test_balance_from_dict() -> None:
    b = Balance.from_dict({"demoBalance": 10000.0, "liveBalance": 50.0, "currencyCode": "USD"})
    assert b.demo == 10000.0
    assert b.live == 50.0
    assert b.currency_code == "USD"


@pytest.mark.unit
def test_profile_info_from_profile_object() -> None:
    class P:
        nick_name = "alice"
        profile_id = 42
        demo_balance = 100.0
        live_balance = 0.0
        currency_code = "USD"
        currency_symbol = "$"
        country_name = "BR"
        offset = 0

    p = ProfileInfo.from_profile(P())
    assert p.nickname == "alice"
    assert p.profile_id == 42
    assert p.demo_balance == 100.0


@pytest.mark.unit
def test_asset_info_from_row() -> None:
    row = [1, "EURUSD", "EUR/USD\n", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, True]
    a = AssetInfo.from_instrument_row(row)
    assert a.id == 1
    assert a.symbol == "EURUSD"
    assert a.name == "EUR/USD"
    assert a.is_open is True


@pytest.mark.unit
def test_reconnect_policy_defaults_sensible() -> None:
    p = ReconnectPolicy()
    assert p.enabled is True
    assert p.max_attempts == 0  # infinite by default
    assert p.base_delay >= 0
    assert p.max_delay >= p.base_delay
    assert p.stale_timeout > 0


@pytest.mark.unit
def test_subscription_mutability() -> None:
    s = Subscription(kind="candle", asset="EURUSD", period=60)
    s.extra["foo"] = "bar"  # Subscription is intentionally mutable
    assert s.extra == {"foo": "bar"}
