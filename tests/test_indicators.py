from pyquotex.utils.indicators import TechnicalIndicators


def test_sma_calculation():
    prices = [10, 11, 12, 13, 14, 15]
    period = 3
    # SMA 3: (10+11+12)/3=11, (11+12+13)/3=12, (12+13+14)/3=13, (13+14+15)/3=14
    expected = [11.0, 12.0, 13.0, 14.0]
    result = TechnicalIndicators.calculate_sma(prices, period)
    assert result == expected


def test_ema_calculation():
    prices = [10, 11, 12, 13, 14, 15]
    period = 3
    # multiplier = 2 / (3+1) = 0.5
    # EMA1 = (10+11+12)/3 = 11.0
    # EMA2 = (13 * 0.5) + (11.0 * 0.5) = 12.0
    # EMA3 = (14 * 0.5) + (12.0 * 0.5) = 13.0
    # EMA4 = (15 * 0.5) + (13.0 * 0.5) = 14.0
    expected = [11.0, 12.0, 13.0, 14.0]
    result = TechnicalIndicators.calculate_ema(prices, period)
    assert result == expected


def test_rsi_calculation():
    prices = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    period = 14
    result = TechnicalIndicators.calculate_rsi(prices, period)
    assert len(result) > 0
    assert 0 <= result[-1] <= 100


def test_macd_calculation():
    prices = [i for i in range(50)]  # Increasing prices
    result = TechnicalIndicators.calculate_macd(prices)
    assert "macd" in result
    assert "current" in result
    assert result["current"]["macd"] is not None


def test_bollinger_bands():
    prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
    result = TechnicalIndicators.calculate_bollinger_bands(prices, period=20)
    assert "upper" in result
    assert "lower" in result
    assert result["current"]["upper"] > result["current"]["middle"]
    assert result["current"]["middle"] > result["current"]["lower"]


def test_atr_calculation():
    highs = [15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]
    lows = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
    closes = [14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28]
    result = TechnicalIndicators.calculate_atr(highs, lows, closes, period=14)
    assert len(result) > 0
    assert result[-1] > 0
