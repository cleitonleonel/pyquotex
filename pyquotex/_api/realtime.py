"""Real-time streaming and indicator methods extracted from Quotex.

This mixin is composed into Quotex via multiple inheritance. It uses
self.api, self.codes_asset, etc. — all set up in Quotex.__init__ inside
pyquotex/stable_api.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from pyquotex._api._constants import DEFAULT_TIMEOUT
from pyquotex.utils.indicators import TechnicalIndicators
from pyquotex.utils.processor import (
    aggregate_candle,
    process_tick,
)

logger = logging.getLogger(__name__)


class RealtimeMixin:
    """Real-time streaming and indicator methods."""

    async def calculate_indicator(
            self,
            asset: str,
            indicator: str,
            params: dict[str, Any] | None = None,
            history_size: int = 3600,
            timeframe: int = 60
    ) -> dict[str, Any]:
        """Calcula indicadores técnicos para um ativo dado."""
        if params is None:
            params = {}

        valid_timeframes = [60, 300, 900, 1800, 3600, 7200, 14400, 86400]
        if timeframe not in valid_timeframes:
            return {
                "error": (
                    f"Timeframe inválido. "
                    f"Valores permitidos: {valid_timeframes}"
                )
            }

        adjusted_history = max(history_size, timeframe * 50)

        candles = await self.get_candles(
            asset, time.time(), adjusted_history, timeframe
        )

        if not candles:
            return {
                "error": f"Não há dados disponíveis para o ativo {asset}"
            }

        prices = [float(candle["close"]) for candle in candles]
        highs = [float(candle["high"]) for candle in candles]
        lows = [float(candle["low"]) for candle in candles]
        timestamps = [candle["time"] for candle in candles]

        indicators = TechnicalIndicators()
        indicator = indicator.upper()

        try:
            if indicator == "RSI":
                period = params.get("period", 14)
                values = indicators.calculate_rsi(prices, period)
                return {
                    "rsi": values,
                    "current": values[-1] if values else None,
                    "history_size": len(values),
                    "timeframe": timeframe,
                    "timestamps": (
                        timestamps[-len(values):] if values else []
                    )
                }

            elif indicator == "MACD":
                fast_period = params.get("fast_period", 12)
                slow_period = params.get("slow_period", 26)
                signal_period = params.get("signal_period", 9)
                macd_data = indicators.calculate_macd(
                    prices, fast_period, slow_period, signal_period
                )
                macd_data["timeframe"] = timeframe
                macd_data["timestamps"] = (
                    timestamps[-len(macd_data["macd"]):]
                    if macd_data["macd"]
                    else []
                )
                return macd_data

            elif indicator == "SMA":
                period = params.get("period", 20)
                values = indicators.calculate_sma(prices, period)
                return {
                    "sma": values,
                    "current": values[-1] if values else None,
                    "history_size": len(values),
                    "timeframe": timeframe,
                    "timestamps": (
                        timestamps[-len(values):] if values else []
                    )
                }

            elif indicator == "EMA":
                period = params.get("period", 20)
                values = indicators.calculate_ema(prices, period)
                return {
                    "ema": values,
                    "current": values[-1] if values else None,
                    "history_size": len(values),
                    "timeframe": timeframe,
                    "timestamps": (
                        timestamps[-len(values):] if values else []
                    )
                }

            elif indicator == "BOLLINGER":
                period = params.get("period", 20)
                num_std = params.get("std", 2)
                bb_data = indicators.calculate_bollinger_bands(
                    prices, period, num_std
                )
                bb_data["timeframe"] = timeframe
                bb_data["timestamps"] = (
                    timestamps[-len(bb_data["middle"]):]
                    if bb_data["middle"]
                    else []
                )
                return bb_data

            elif indicator == "STOCHASTIC":
                k_period = params.get("k_period", 14)
                d_period = params.get("d_period", 3)
                stoch_data = indicators.calculate_stochastic(
                    prices, highs, lows, k_period, d_period
                )
                stoch_data["timeframe"] = timeframe
                stoch_data["timestamps"] = (
                    timestamps[-len(stoch_data["k"]):]
                    if stoch_data["k"]
                    else []
                )
                return stoch_data

            elif indicator == "ATR":
                period = params.get("period", 14)
                values = indicators.calculate_atr(highs, lows, prices, period)
                return {
                    "atr": values,
                    "current": values[-1] if values else None,
                    "history_size": len(values),
                    "timeframe": timeframe,
                    "timestamps": (
                        timestamps[-len(values):] if values else []
                    )
                }

            elif indicator == "ADX":
                period = params.get("period", 14)
                adx_data = indicators.calculate_adx(
                    highs, lows, prices, period
                )
                adx_data["timeframe"] = timeframe
                adx_data["timestamps"] = (
                    timestamps[-len(adx_data["adx"]):]
                    if adx_data["adx"]
                    else []
                )
                return adx_data

            elif indicator == "ICHIMOKU":
                tenkan_period = params.get("tenkan_period", 9)
                kijun_period = params.get("kijun_period", 26)
                senkou_b_period = params.get("senkou_b_period", 52)
                ichimoku_data = indicators.calculate_ichimoku(
                    highs, lows, tenkan_period, kijun_period, senkou_b_period
                )
                ichimoku_data["timeframe"] = timeframe
                ichimoku_data["timestamps"] = (
                    timestamps[-len(ichimoku_data["tenkan"]):]
                    if ichimoku_data["tenkan"]
                    else []
                )
                return ichimoku_data

            else:
                return {"error": f"Indicador '{indicator}' não suportado"}

        except Exception as e:
            return {"error": f"Erro calculando o indicador: {str(e)}"}

    async def subscribe_indicator(
            self,
            asset: str,
            indicator: str,
            params: dict[str, Any] | None = None,
            callback: Callable[[dict[str, Any]], Any] | None = None,
            timeframe: int = 60
    ) -> None:
        """
        Subscribes to real-time indicator updates with high performance.

        Features:
        - Event-driven: Recalculates only when a new candle is generated.
        - Efficient: Pre-loads history and maintains local data buffers.
        - Robust: Properly handles all indicator parameters and edge cases.
        """
        if params is None:
            params = {}
        if not callback:
            raise ValueError("Callback function must be provided")

        indicator_upper = indicator.upper()
        min_periods = {
            "RSI": 14, "MACD": 26, "BOLLINGER": 20, "STOCHASTIC": 14,
            "ADX": 14, "ATR": 14, "SMA": 20, "EMA": 20, "ICHIMOKU": 52
        }
        required_periods = min_periods.get(indicator_upper, 20)

        try:
            await self.start_candles_stream(asset, timeframe)

            # 1. Initial Data Loading
            # Fetch history to satisfy the indicator's window
            history = await self.get_candles(
                asset,
                time.time(),
                timeframe * (required_periods + 20),
                timeframe
            )

            if not history:
                logger.warning("No history found for %s, waiting...", asset)
                history = []

            # Maintain local buffers to avoid repeated sorting/conversions
            prices = [float(c["close"]) for c in history]
            highs = [float(c["high"]) for c in history]
            lows = [float(c["low"]) for c in history]
            last_ts = history[-1]["time"] if history else 0

            ti = TechnicalIndicators()
            event_name = f"candle_generated_{asset}_{timeframe}"

            while await self.check_connect():
                try:
                    # 2. Wait for New Candle Event
                    try:
                        # Wait for the next candle closure
                        msg_data = await self.api.event_registry.wait_event(
                            event_name, timeout=timeframe + 10
                        )
                    except TimeoutError:
                        # Check if data arrived but event was missed
                        msg_data = self.api.candle_generated_check[
                            str(asset)
                        ].get(timeframe)

                    if not msg_data:
                        await asyncio.sleep(1)
                        continue

                    current_ts = msg_data.get("index", 0)
                    if current_ts <= last_ts:
                        await asyncio.sleep(1)
                        continue

                    # 3. Update Buffers with New Closed Candle
                    prices.append(float(msg_data["close"]))
                    highs.append(float(msg_data["high"]))
                    lows.append(float(msg_data["low"]))
                    last_ts = current_ts

                    # Cap buffers to prevent memory leaks (e.g., 500 candles)
                    if len(prices) > 500:
                        prices = prices[-500:]
                        highs = highs[-500:]
                        lows = lows[-500:]

                    if len(prices) < required_periods:
                        continue

                    # 4. Calculate Indicator
                    result: dict[str, Any] = {
                        "time": last_ts,
                        "timeframe": timeframe,
                        "asset": asset,
                        "indicator": indicator_upper
                    }

                    if indicator_upper == "RSI":
                        period = params.get("period", 14)
                        vals = ti.calculate_rsi(prices, period)
                        result["value"] = vals[-1] if vals else None
                        result["all_values"] = vals

                    elif indicator_upper == "MACD":
                        fast = params.get("fast_period", 12)
                        slow = params.get("slow_period", 26)
                        sig = params.get("signal_period", 9)
                        result.update(ti.calculate_macd(prices, fast, slow, sig))

                    elif indicator_upper == "BOLLINGER":
                        period = params.get("period", 20)
                        std = params.get("std", 2)
                        result.update(
                            ti.calculate_bollinger_bands(prices, period, std)
                        )

                    elif indicator_upper == "STOCHASTIC":
                        k = params.get("k_period", 14)
                        d = params.get("d_period", 3)
                        result.update(
                            ti.calculate_stochastic(prices, highs, lows, k, d)
                        )

                    elif indicator_upper == "SMA":
                        period = params.get("period", 20)
                        vals = ti.calculate_sma(prices, period)
                        result["value"] = vals[-1] if vals else None
                        result["all_values"] = vals

                    elif indicator_upper == "EMA":
                        period = params.get("period", 20)
                        vals = ti.calculate_ema(prices, period)
                        result["value"] = vals[-1] if vals else None
                        result["all_values"] = vals

                    elif indicator_upper == "ADX":
                        period = params.get("period", 14)
                        result.update(
                            ti.calculate_adx(highs, lows, prices, period)
                        )

                    elif indicator_upper == "ATR":
                        period = params.get("period", 14)
                        vals = ti.calculate_atr(highs, lows, prices, period)
                        result["value"] = vals[-1] if vals else None
                        result["all_values"] = vals

                    elif indicator_upper == "ICHIMOKU":
                        t = params.get("tenkan", 9)
                        k = params.get("kijun", 26)
                        s = params.get("senkou", 52)
                        result.update(
                            ti.calculate_ichimoku(highs, lows, t, k, s)
                        )

                    else:
                        result["error"] = f"Indicator {indicator} not supported"

                    # 5. Trigger Callback
                    await callback(result)

                except Exception as e:
                    logger.warning("Error in indicator loop: %s", e)
                    await asyncio.sleep(1)

        finally:
            try:
                await self.stop_candles_stream(asset)
            except Exception:
                pass

    async def start_candles_stream(
            self, asset: str = "EURUSD", period: int = 0
    ) -> None:
        """Start streaming candle data for a specified asset."""
        if self.api:
            self.api.current_asset = asset
            await self.api.subscribe_realtime_candle(asset, period)
            await self.api.chart_notification(asset)
            await self.api.follow_candle(asset)
            self.api._track_subscription("candle", asset, period)

    async def stop_candles_stream(self, asset: str) -> None:
        """Stops streaming candle data for a specified asset."""
        if self.api:
            await self.api.unsubscribe_realtime_candle(asset)
            await self.api.unfollow_candle(asset)
            self.api._forget_subscription("candle", asset)

    async def start_signals_data(self) -> None:
        """Subscribes to the global trading signals stream."""
        if self.api:
            await self.api.signals_subscribe()

    async def opening_closing_current_candle(
            self, asset: str, period: int = 0
    ) -> dict[str, Any]:
        """Calculates the opening, closing, and remaining time for the
        current candle."""
        candles_data: dict[int, Any] = {}
        candles_tick = await self.get_realtime_candles(asset)
        logger.debug("Candles tick data: %s", candles_tick)
        # aggregate_candle expects dict[int, Any] for tick
        # This part might need adjustment depending on what
        # get_realtime_candles returns
        aggregate = aggregate_candle(
            candles_tick if isinstance(candles_tick, dict) else {},
            candles_data
        )
        logger.debug("Aggregated candle: %s", aggregate)
        if not aggregate:
            return {}
        candles_dict = list(aggregate.values())[0]
        candles_dict['opening'] = candles_dict.pop('timestamp')
        candles_dict['closing'] = candles_dict['opening'] + period
        candles_dict['remaining'] = candles_dict['closing'] - int(time.time())
        return candles_dict

    async def start_realtime_price(
            self,
            asset: str,
            period: int = 0,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Starts following real-time price for an asset."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        await self.start_candles_stream(asset, period)
        start = time.time()
        while True:
            if self.api.realtime_price.get(asset):
                return self.api.realtime_price
            if time.time() - start > timeout:
                raise TimeoutError(
                    f"Timeout waiting for realtime price data for {asset}."
                )
            await asyncio.sleep(0.2)

    async def start_realtime_sentiment(
            self,
            asset: str,
            period: int = 0,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Starts following real-time trader sentiment for an asset."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        await self.start_candles_stream(asset, period)
        start = time.time()
        while True:
            if self.api.realtime_sentiment.get(asset):
                return self.api.realtime_sentiment[asset]
            if time.time() - start > timeout:
                raise TimeoutError(
                    f"Timeout waiting for realtime sentiment data for {asset}."
                )
            await asyncio.sleep(0.2)

    async def start_realtime_candle(
            self,
            asset: str,
            period: int = 0,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[int, Any]:
        """Starts following and processing real-time candle ticks for
        an asset."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        await self.start_candles_stream(asset, period)
        data: dict[int, Any] = {}
        start = time.time()
        while True:
            candle_data = self.api.realtime_candles.get(asset)
            if candle_data:
                if isinstance(candle_data, list) and len(candle_data) >= 4:
                    return process_tick(candle_data, period, data)
                return data
            if time.time() - start > timeout:
                raise TimeoutError(
                    f"Timeout waiting for realtime candle data for {asset}."
                )
            await asyncio.sleep(0.2)

    async def get_realtime_candles(
            self, asset: str
    ) -> list[Any] | dict[Any, Any]:
        """Retrieves current real-time price history for an asset from
        shared state."""
        if self.api:
            return self.api.realtime_candles.get(asset, [])
        return []

    async def get_realtime_sentiment(self, asset: str) -> dict[str, Any]:
        """Retrieves current sentiment data for an asset from shared state."""
        if self.api:
            return self.api.realtime_sentiment.get(asset, {})
        return {}

    async def get_realtime_price(self, asset: str) -> list[dict[str, Any]]:
        """Retrieves current real-time price history for an asset from
        shared state."""
        if self.api:
            # Convert deque to list for compatibility with existing strategies
            return list(self.api.realtime_price.get(asset, []))
        return []

    def get_signal_data(self) -> dict[str, Any]:
        """Retrieves the list of active signals received via signals stream."""
        if self.api:
            return self.api.signal_data
        return {}

    async def start_candles_one_stream(self, asset: str, size: int) -> bool:
        """Internal helper to start a single candle stream."""
        if self.api is None:
            return False

        if not (str(asset + "," + str(size)) in self.subscribe_candle):
            self.subscribe_candle.append((asset + "," + str(size)))
        start = time.time()
        # This part assumes api has these attributes, might need check
        if not hasattr(self.api, "candle_generated_check"):
            return False

        self.api.candle_generated_check[str(asset)][int(size)] = {}
        # Send the subscribe request exactly once before polling.
        # Calling follow_candle() inside the loop would spam the server
        # with up to 100 subscribe messages (20 s / 0.2 s) before data
        # arrives — a ban/rate-limit risk explicitly warned about in README.
        try:
            await self.api.follow_candle(self.codes_asset[asset])
        except Exception as e:
            logger.error('**error** start_candles_stream reconnect: %s', e)
            await self.connect()
        while True:
            if time.time() - start > 20:
                logger.error(
                    '**error** start_candles_one_stream late for 20 sec'
                )
                return False
            try:
                if self.api.candle_generated_check[str(asset)][int(size)]:
                    return True
            except (KeyError, TypeError):
                pass
            await asyncio.sleep(0.2)

    async def start_candles_all_size_stream(self, asset: str) -> bool:
        """Internal helper to subscribe to all candle sizes for an asset."""
        if self.api is None:
            return False

        if not hasattr(self.api, "candle_generated_all_size_check"):
            return False

        self.api.candle_generated_all_size_check[str(asset)] = {}
        if not (str(asset) in self.subscribe_candle_all_size):
            self.subscribe_candle_all_size.append(str(asset))
        self.api._track_subscription("candle_all_size", asset)
        start = time.time()
        while await self.check_connect():
            if self.api is None: break
            if time.time() - start > 20:
                logger.error(
                    f'**error** fail {asset} '
                    'start_candles_all_size_stream late for 10 sec'
                )
                return False
            try:
                if self.api.candle_generated_all_size_check[str(asset)]:
                    return True
            except (KeyError, TypeError):
                pass
            try:
                # Assuming api has subscribe_all_size
                if hasattr(self.api, "subscribe_all_size"):
                    self.api.subscribe_all_size(self.codes_asset[asset])
            except Exception as e:
                logger.error(
                    '**error** start_candles_all_size_stream reconnect: %s', e
                )
                await self.connect()
            await asyncio.sleep(0.2)
        return False

    async def start_mood_stream(
            self, asset: str, instrument: str = "turbo-option"
    ) -> None:
        """Internal helper to start the mood (sentiment) stream."""
        if self.api is None:
            return

        if asset not in self.subscribe_mood:
            self.subscribe_mood.append(asset)
        self.api._track_subscription("mood", asset, instrument=instrument)
        while True:
            if self.api is None: break
            if hasattr(self.api, "subscribe_Traders_mood"):
                self.api.subscribe_Traders_mood(asset, instrument)
            try:
                if hasattr(self.api, "traders_mood"):
                    asset_code = self.codes_asset[asset]
                    self.api.traders_mood[asset_code] = asset_code
                break
            finally:
                await asyncio.sleep(0.2)
