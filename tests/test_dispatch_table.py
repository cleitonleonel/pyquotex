"""Tests for the dispatch-table refactor of ``QuotexAPI._on_message``.

These exercise the control-event handlers directly, without involving the
WebSocket or HTTP layers.
"""

import pytest

from pyquotex.api import QuotexAPI
from pyquotex.global_value import AuthStatus


def _make_api() -> QuotexAPI:
    return QuotexAPI(
        host="qxbroker.com",
        username="x",
        password="x",
        lang="en",
        proxies=None,
        resource_path=".",
        user_data_dir="browser",
        on_otp_callback=None,
    )


@pytest.mark.unit
def test_control_handlers_registered() -> None:
    api = _make_api()
    for event in (
        "s_authorization",
        "instruments/list",
        "trader/history",
        "balance",
        "candle-generated",
        "sentiment",
    ):
        assert event in api._control_handlers


@pytest.mark.asyncio
async def test_balance_handler_sets_slot_and_state() -> None:
    api = _make_api()
    payload = {"demoBalance": 100.0, "liveBalance": 1.0}
    await api._control_handlers["balance"](payload)
    assert api.account_balance == payload
    assert api.slots.balance.is_set()


@pytest.mark.asyncio
async def test_auth_handler_flips_state() -> None:
    api = _make_api()
    await api._control_handlers["s_authorization"](None)
    assert api.state.auth_status == AuthStatus.AUTHENTICATED


@pytest.mark.asyncio
async def test_instruments_list_handler_caches_list() -> None:
    api = _make_api()
    rows = [[1, "EURUSD", "EUR/USD"]]
    await api._control_handlers["instruments/list"](rows)
    assert api.instruments == rows


@pytest.mark.asyncio
async def test_instruments_list_handler_handles_placeholder() -> None:
    api = _make_api()
    placeholder = {"_placeholder": True, "num": 0}
    await api._control_handlers["instruments/list"](placeholder)
    assert "instruments/list" in api._temp_status


@pytest.mark.asyncio
async def test_sentiment_handler_indexes_by_asset() -> None:
    api = _make_api()
    payload = {"asset": "EURUSD", "value": 0.6}
    await api._control_handlers["sentiment"](payload)
    assert api.traders_mood["EURUSD"] == payload
    assert api.realtime_sentiment["EURUSD"] == payload


@pytest.mark.asyncio
async def test_candle_generated_handler_caches_by_asset_period() -> None:
    api = _make_api()
    payload = {"asset": "EURUSD", "period": 60, "close": 1.1}
    await api._control_handlers["candle-generated"](payload)
    assert api.candle_generated_check["EURUSD"][60] == payload
    assert api.candle_generated_all_size_check["EURUSD"] == payload
