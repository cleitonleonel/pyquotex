import pytest
from pyquotex.utils.processor import get_color, process_candles


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
