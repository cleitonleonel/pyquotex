import numpy as np
from typing import List, Dict, Union, Tuple


class TechnicalIndicators:
    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> List[float]:
        """Calcula la Media Móvil Simple (SMA)"""
        if len(prices) < period:
            return []

        sma_values = []
        for i in range(len(prices) - period + 1):
            sma = sum(prices[i:(i + period)]) / period
            sma_values.append(round(sma, 2))
        return sma_values

    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> List[float]:
        """Calcula la Media Móvil Exponencial (EMA)"""
        if len(prices) < period:
            return []

        multiplier = 2 / (period + 1)
        ema_values = [sum(prices[:period]) / period]

        for price in prices[period:]:
            ema = (price * multiplier) + (ema_values[-1] * (1 - multiplier))
            ema_values.append(round(ema, 2))
        return ema_values

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
        """Calcula el Índice de Fuerza Relativa (RSI)"""
        if len(prices) < period + 1:
            return []

        deltas = np.diff(prices)
        gain = np.where(deltas > 0, deltas, 0)
        loss = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.concatenate(([np.mean(gain[:period])], gain[period:]))
        avg_loss = np.concatenate(([np.mean(loss[:period])], loss[period:]))

        for i in range(1, len(avg_gain)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[period + i - 1]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[period + i - 1]) / period

        rs = avg_gain / np.where(avg_loss == 0, 0.00001, avg_loss)
        rsi = 100 - (100 / (1 + rs))
        return [round(x, 2) for x in rsi.tolist()]

    @staticmethod
    def calculate_macd(prices: List[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> \
    Dict[str, List[float]]:
        """Calcula el MACD (Moving Average Convergence Divergence)"""
        if len(prices) < slow_period:
            return {"macd": [], "signal": [], "histogram": []}

        fast_ema = TechnicalIndicators.calculate_ema(prices, fast_period)
        slow_ema = TechnicalIndicators.calculate_ema(prices, slow_period)

        macd_line = []
        for i in range(len(slow_ema)):
            macd = fast_ema[i + (len(fast_ema) - len(slow_ema))] - slow_ema[i]
            macd_line.append(round(macd, 2))

        signal_line = TechnicalIndicators.calculate_ema(macd_line, signal_period)

        histogram = []
        for i in range(len(signal_line)):
            hist = macd_line[i + (len(macd_line) - len(signal_line))] - signal_line[i]
            histogram.append(round(hist, 2))

        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
            "current": {
                "macd": macd_line[-1] if macd_line else None,
                "signal": signal_line[-1] if signal_line else None,
                "histogram": histogram[-1] if histogram else None
            }
        }

    @staticmethod
    def calculate_bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2) -> Dict[str, List[float]]:
        """Calcula las Bandas de Bollinger"""
        if len(prices) < period:
            return {"upper": [], "middle": [], "lower": []}

        sma = TechnicalIndicators.calculate_sma(prices, period)
        std = []

        for i in range(len(prices) - period + 1):
            window = prices[i:(i + period)]
            std.append(np.std(window))

        upper_band = [sma[i] + (std[i] * num_std) for i in range(len(sma))]
        lower_band = [sma[i] - (std[i] * num_std) for i in range(len(sma))]

        return {
            "upper": [round(x, 2) for x in upper_band],
            "middle": [round(x, 2) for x in sma],
            "lower": [round(x, 2) for x in lower_band],
            "current": {
                "upper": upper_band[-1] if upper_band else None,
                "middle": sma[-1] if sma else None,
                "lower": lower_band[-1] if lower_band else None
            }
        }

    @staticmethod
    def calculate_stochastic(prices: List[float], highs: List[float], lows: List[float], k_period: int = 14,
                             d_period: int = 3) -> Dict[str, List[float]]:
        """Calcula el Oscilador Estocástico"""
        if len(prices) < k_period:
            return {"k": [], "d": []}

        k_values = []

        for i in range(len(prices) - k_period + 1):
            window_high = max(highs[i:i + k_period])
            window_low = min(lows[i:i + k_period])

            if window_high == window_low:
                k = 100
            else:
                k = ((prices[i + k_period - 1] - window_low) / (window_high - window_low)) * 100
            k_values.append(round(k, 2))

        d_values = TechnicalIndicators.calculate_sma(k_values, d_period)

        return {
            "k": k_values,
            "d": d_values,
            "current": {
                "k": k_values[-1] if k_values else None,
                "d": d_values[-1] if d_values else None
            }
        }

    @staticmethod
    def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
        """Calcula el Average True Range (ATR)"""
        if len(highs) < period:
            return []

        true_ranges = []
        for i in range(1, len(highs)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i - 1]

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)

            true_range = max(tr1, tr2, tr3)
            true_ranges.append(true_range)

        atr_values = [sum(true_ranges[:period]) / period]

        for i in range(period, len(true_ranges)):
            atr = (atr_values[-1] * (period - 1) + true_ranges[i]) / period
            atr_values.append(round(atr, 2))

        return atr_values

    @staticmethod
    def calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Dict[
        str, List[float]]:
        """Calcula el Average Directional Index (ADX)"""
        if len(highs) < period + 1:
            return {"adx": [], "plus_di": [], "minus_di": []}

        # Calcular True Range
        tr = []
        plus_dm = []
        minus_dm = []

        for i in range(1, len(highs)):
            high = highs[i]
            low = lows[i]
            prev_high = highs[i - 1]
            prev_low = lows[i - 1]
            prev_close = closes[i - 1]

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            tr.append(max(tr1, tr2, tr3))

            plus_dm1 = high - prev_high
            minus_dm1 = prev_low - low

            if plus_dm1 > minus_dm1 and plus_dm1 > 0:
                plus_dm.append(plus_dm1)
            else:
                plus_dm.append(0)

            if minus_dm1 > plus_dm1 and minus_dm1 > 0:
                minus_dm.append(minus_dm1)
            else:
                minus_dm.append(0)

        # Calcular los promedios
        tr_avg = [sum(tr[:period]) / period]
        plus_di_avg = [sum(plus_dm[:period]) / period * 100 / tr_avg[0]]
        minus_di_avg = [sum(minus_dm[:period]) / period * 100 / tr_avg[0]]

        for i in range(period, len(tr)):
            tr_avg.append((tr_avg[-1] * (period - 1) + tr[i]) / period)
            plus_di = (plus_di_avg[-1] * (period - 1) + plus_dm[i]) / period
            minus_di = (minus_di_avg[-1] * (period - 1) + minus_dm[i]) / period

            plus_di_avg.append(plus_di * 100 / tr_avg[-1])
            minus_di_avg.append(minus_di * 100 / tr_avg[-1])

        # Calcular ADX
        dx_values = []
        for i in range(len(plus_di_avg)):
            dx = abs(plus_di_avg[i] - minus_di_avg[i]) / (plus_di_avg[i] + minus_di_avg[i]) * 100
            dx_values.append(dx)

        adx_values = [sum(dx_values[:period]) / period]
        for i in range(period, len(dx_values)):
            adx = (adx_values[-1] * (period - 1) + dx_values[i]) / period
            adx_values.append(round(adx, 2))

        return {
            "adx": adx_values,
            "plus_di": [round(x, 2) for x in plus_di_avg],
            "minus_di": [round(x, 2) for x in minus_di_avg],
            "current": {
                "adx": adx_values[-1] if adx_values else None,
                "plus_di": plus_di_avg[-1] if plus_di_avg else None,
                "minus_di": minus_di_avg[-1] if minus_di_avg else None
            }
        }

    @staticmethod
    def calculate_ichimoku(highs: List[float], lows: List[float],
                           tenkan_period: int = 9,
                           kijun_period: int = 26,
                           senkou_b_period: int = 52) -> Dict[str, List[float]]:
        """Calcula el Ichimoku Cloud"""
        if len(highs) < senkou_b_period:
            return {
                "tenkan": [],
                "kijun": [],
                "senkou_a": [],
                "senkou_b": [],
                "chikou": []
            }

        def donchian(high_prices: List[float], low_prices: List[float], period: int) -> List[float]:
            result = []
            for i in range(len(high_prices) - period + 1):
                highest = max(high_prices[i:i + period])
                lowest = min(low_prices[i:i + period])
                result.append((highest + lowest) / 2)
            return result

        # Cálculo de las líneas
        tenkan = donchian(highs, lows, tenkan_period)
        kijun = donchian(highs, lows, kijun_period)
        senkou_b = donchian(highs, lows, senkou_b_period)

        # Senkou Span A (Promedio de Tenkan y Kijun)
        senkou_a = []
        for i in range(min(len(tenkan), len(kijun))):
            senkou_a.append((tenkan[i] + kijun[i]) / 2)

        # Chikou Span (Precio de cierre desplazado 26 períodos hacia atrás)
        chikou = lows[kijun_period:]

        return {
            "tenkan": [round(x, 2) for x in tenkan],
            "kijun": [round(x, 2) for x in kijun],
            "senkou_a": [round(x, 2) for x in senkou_a],
            "senkou_b": [round(x, 2) for x in senkou_b],
            "chikou": [round(x, 2) for x in chikou],
            "current": {
                "tenkan": tenkan[-1] if tenkan else None,
                "kijun": kijun[-1] if kijun else None,
                "senkou_a": senkou_a[-1] if senkou_a else None,
                "senkou_b": senkou_b[-1] if senkou_b else None,
                "chikou": chikou[-1] if chikou else None
            }
        }