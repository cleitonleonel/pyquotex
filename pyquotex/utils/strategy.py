import logging
from typing import Any

from .indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class TripleConfirmationStrategy:
    """
    Professional Trading Strategy: The Triple Confirmation
    Indicators:
    - EMA 20 & 50 (Trend)
    - RSI 7 (Momentum)
    - Stochastic 14, 3, 3 (Exhaustion/Entry)
    """

    def __init__(
            self,
            rsi_period: int = 7,
            ema_fast: int = 20,
            ema_slow: int = 50,
            stoch_k: int = 14,
            stoch_d: int = 3
    ) -> None:
        self.rsi_period = rsi_period
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.stoch_k = stoch_k
        self.stoch_d = stoch_d
        self.indicators = TechnicalIndicators()

    def analyze(self, candles: list[dict[str, Any]]) -> str | None:
        """
        Analyze candle data and return 'call', 'put' or None.
        Expects a list of dicts with 'open', 'close', 'high', 'low'.
        """
        if len(candles) < self.ema_slow + 5:
            return None

        closes = [float(c['close']) for c in candles]
        highs = [float(c['high']) for c in candles]
        lows = [float(c['low']) for c in candles]

        # 1. EMA Trend
        ema20 = self.indicators.calculate_ema(closes, self.ema_fast)
        ema50 = self.indicators.calculate_ema(closes, self.ema_slow)

        if not ema20 or not ema50:
            return None

        current_price = closes[-1]
        last_ema20 = ema20[-1]
        last_ema50 = ema50[-1]

        uptrend = current_price > last_ema20 > last_ema50
        downtrend = current_price < last_ema20 < last_ema50

        # 2. RSI Momentum
        rsi = self.indicators.calculate_rsi(closes, self.rsi_period)
        if not rsi:
            return None
        last_rsi = rsi[-1]

        # 3. Stochastic Entry
        stoch = self.indicators.calculate_stochastic(closes, highs, lows, self.stoch_k, self.stoch_d)
        if not stoch['d']:
            return None

        k = stoch['k']
        d = stoch['d']

        # Current and previous values for crossover detection
        k_now, k_prev = k[-1], k[-2]
        d_now, d_prev = d[-1], d[-2]

        # CALL Signal
        if uptrend and last_rsi > 50:
            # Bullish Crossover below 20 (oversold in uptrend)
            if k_prev < d_prev and d_now < k_now < 30:
                return "call"

        # PUT Signal
        if downtrend and last_rsi < 50:
            # Bearish Crossover above 80 (overbought in downtrend)
            if k_prev > d_prev and d_now > k_now > 70:
                return "put"

        return None
