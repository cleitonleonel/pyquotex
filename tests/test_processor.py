import pytest
from pyquotex.utils.processor import get_color, process_candles, process_candles_v2


def test_get_color_green():
    """Test get_color returns 'green' when close >= open."""
    candle = {
        "open": 1.1000,
        "close": 1.1050
    }
    assert get_color(candle) == "green"

def test_get_color_red():
    """Test get_color returns 'red' when close < open."""
    candle = {
        "open": 1.1050,
        "close": 1.1000
    }
    assert get_color(candle) == "red"

def test_get_color_invalid():
    """Test get_color handles missing keys gracefully."""
    candle1 = {"open": 1.1000}
    candle2 = {"close": 1.1050}
    candle3 = {}
    
    with pytest.raises(KeyError):
        get_color(candle1)
        
    with pytest.raises(KeyError):
        get_color(candle2)
        
    with pytest.raises(KeyError):
        get_color(candle3)


def test_process_candles_grouping():
    """Test that process_candles can group smaller period candles."""
    
    # A list of 1-second ticks
    raw_candles = [
        {"time": 1000000, "price": 1.0, "amount": 1},
        {"time": 1000001, "price": 1.5, "amount": 1},
        {"time": 1000002, "price": 0.5, "amount": 1},
        {"time": 1000003, "price": 1.2, "amount": 1},
        
        {"time": 1000004, "price": 2.0, "amount": 1},
        {"time": 1000005, "price": 2.5, "amount": 1},
        {"time": 1000006, "price": 1.5, "amount": 1},
        {"time": 1000007, "price": 2.2, "amount": 1},
        {"time": 1000008, "price": 2.0, "amount": 1}, # Forces the previous candle to close
    ]
    
    # Process into 4-second period candles
    processed = process_candles(raw_candles, 4)
    
    assert len(processed) == 2
    
    # First candle
    assert processed[0]['open'] == 1.0
    assert processed[0]['close'] == 1.2
    assert processed[0]['high'] == 1.5
    assert processed[0]['low'] == 0.5
    assert processed[0]['ticks'] == 4
    
    # Second candle
    assert processed[1]['open'] == 2.0
    assert processed[1]['close'] == 2.2
    assert processed[1]['high'] == 2.5
    assert processed[1]['low'] == 1.5
    assert processed[1]['ticks'] == 4


def test_process_candles_v2_none_history():
    """Test process_candles_v2 returns data when history is None."""
    data = [
        {'time': 1000, 'open': 1.0, 'close': 1.1, 'high': 1.2, 'low': 0.9},
        {'time': 1001, 'open': 1.1, 'close': 1.2, 'high': 1.3, 'low': 1.0}
    ]
    result = process_candles_v2(None, 'EURUSD', data)
    assert result == data


def test_process_candles_v2_non_dict_history():
    """Test process_candles_v2 returns data when history is not a dict."""
    data = [
        {'time': 1000, 'open': 1.0, 'close': 1.1, 'high': 1.2, 'low': 0.9},
    ]

    # Test with list history
    result = process_candles_v2([], 'EURUSD', data)
    assert result == data

    # Test with string history
    result = process_candles_v2("invalid", 'EURUSD', data)
    assert result == data


def test_process_candles_v2_empty_inputs():
    """Test process_candles_v2 handles empty history and data."""
    history = {
        'EURUSD': {'candles': []}
    }

    # Both None
    result = process_candles_v2(history, 'EURUSD', None)
    assert result == []

    # Empty data
    result = process_candles_v2(history, 'EURUSD', [])
    assert result == []


def test_process_candles_v2_duplicate_deduplication():
    """Test process_candles_v2 removes duplicate candles by time."""
    # Historical candles
    historical = [
        {'time': 1000, 'open': 1.0, 'close': 1.1, 'high': 1.2, 'low': 0.9},
        {'time': 1001, 'open': 1.1, 'close': 1.2, 'high': 1.3, 'low': 1.0}
    ]

    # Realtime data with duplicate time entry
    realtime = [
        {'time': 1000, 'open': 1.05, 'close': 1.15, 'high': 1.25, 'low': 0.95},  # duplicate of first historical
        {'time': 1002, 'open': 1.2, 'close': 1.3, 'high': 1.4, 'low': 1.1}
    ]

    history = {
        'EURUSD': {'candles': [{'dummy': 'header'}] + historical}  # Header is skipped
    }

    result = process_candles_v2(history, 'EURUSD', realtime)

    # Should have deduplicated - time 1000 appears twice, should keep only one
    # We expect 3 unique times: 1000, 1001, 1002
    times = [c['time'] for c in result]
    assert len(times) == len(set(times)), "Duplicate times found after deduplication"
    assert 1000 in times
    assert 1001 in times
    assert 1002 in times


def test_process_candles_v2_missing_asset_key():
    """Test process_candles_v2 handles missing asset in history."""
    history = {
        'GBPUSD': {'candles': []}  # Different asset
    }
    data = [
        {'time': 1000, 'open': 1.0, 'close': 1.1, 'high': 1.2, 'low': 0.9},
    ]

    result = process_candles_v2(history, 'EURUSD', data)  # Request different asset
    assert result == data


def test_process_candles_v2_candles_after_header_skip():
    """Test process_candles_v2 properly skips the [1:] header candle."""
    historical = [
        {'time': 999, 'open': 0.9, 'close': 0.95},  # This is header, should be skipped
        {'time': 1000, 'open': 1.0, 'close': 1.1, 'high': 1.2, 'low': 0.9},
        {'time': 1001, 'open': 1.1, 'close': 1.2, 'high': 1.3, 'low': 1.0}
    ]

    history = {
        'EURUSD': {'candles': historical}
    }

    result = process_candles_v2(history, 'EURUSD', [])

    # Should skip the first candle (header), so times should be 1000 and 1001
    times = [c['time'] for c in result]
    assert 999 not in times, "Header candle should be skipped"
    assert 1000 in times
    assert 1001 in times


def test_process_candles_v2_malformed_candles():
    """Test process_candles_v2 handles mixed valid and malformed candles."""
    # Use None for history to skip the early return from "not history" check
    data = [
        {'open': 1.0, 'close': 1.1},  # Missing 'time'
        {'time': 1000, 'open': 1.1, 'close': 1.2},  # Valid
    ]

    result = process_candles_v2(None, 'EURUSD', data)
    # When history is None, should return data as-is
    assert len(result) == 2

    # Now test with valid history dict to verify dedup filters properly
    history = {
        'EURUSD': {'candles': [{'dummy': 'header'}]}
    }
    result = process_candles_v2(history, 'EURUSD', data)
    # With valid history dict, dedup removes candles without 'time' key
    assert all('time' in c for c in result), "Dedup should filter out candles without time key"
