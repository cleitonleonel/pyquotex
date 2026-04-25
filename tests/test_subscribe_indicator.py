import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyquotex.stable_api import Quotex


@pytest.mark.asyncio
async def test_subscribe_indicator_logic():
    # 1. Setup Mock Client
    client = Quotex("email", "pass")
    client.api = MagicMock()
    client.api.event_registry = AsyncMock()
    client.api.candle_generated_check = {"EURUSD": {}}

    # Mock connection check
    client.check_connect = AsyncMock(side_effect=[True, True, False])  # Run twice then exit

    # Mock history fetching
    mock_history = [
        {"time": 100, "open": 1.1, "close": 1.2, "high": 1.3, "low": 1.0}
        for _ in range(30)
    ]
    client.get_candles = AsyncMock(return_value=mock_history)
    client.start_candles_stream = AsyncMock()
    client.stop_candles_stream = AsyncMock()

    # 2. Mock Event Data (New Candle)
    new_candle_event = {
        "index": 200, "open": 1.2, "close": 1.4, "high": 1.5, "low": 1.1
    }
    client.api.event_registry.wait_event = AsyncMock(return_value=new_candle_event)

    # 3. Define Callback
    callback_results = []

    async def my_callback(data):
        callback_results.append(data)

    # 4. Run Test
    # We use a timeout because subscribe_indicator has an infinite loop
    try:
        await asyncio.wait_for(
            client.subscribe_indicator(
                "EURUSD", "RSI", {"period": 14}, my_callback, timeframe=60
            ),
            timeout=2
        )
    except (asyncio.TimeoutError, Exception):
        pass

    # 5. Verify Results
    assert client.start_candles_stream.called
    assert len(callback_results) > 0
    result = callback_results[0]
    assert result["asset"] == "EURUSD"
    assert result["indicator"] == "RSI"
    assert "value" in result
    assert result["time"] == 200  # Should be the index from new_candle_event

    print("Integration test for subscribe_indicator passed!")


if __name__ == "__main__":
    asyncio.run(test_subscribe_indicator_logic())
