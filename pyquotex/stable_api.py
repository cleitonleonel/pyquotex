import time
import logging
import asyncio
from datetime import datetime
from typing import Dict
from . import expiration
from .api import QuotexAPI
from .utils.services import truncate
from .utils.processor import (
    calculate_candles,
    process_candles_v2,
    merge_candles,
    process_tick,
    aggregate_candle
)
from .config import (
    load_session,
    update_session,
    resource_path,
    credentials
)
from .utils.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)

# Default timeout (seconds) for async polling loops
DEFAULT_TIMEOUT = 30


class Quotex:

    def __init__(
            self,
            email: str = None,
            password: str = None,
            host: str = "qxbroker.com",
            lang: str = "pt",
            user_agent: str = "Quotex/1.0",
            root_path: str = ".",
            user_data_dir: str = "browser",
            asset_default: str = "EURUSD",
            period_default: int = 60,
            proxies: Dict[str, str] = None
    ):
        self.size = [
            5,
            10,
            15,
            30,
            60,
            120,
            300,
            600,
            900,
            1800,
            3600,
            7200,
            14400,
            86400,
        ]
        self.email = email
        self.password = password
        self.host = host
        self.lang = lang
        self.proxies = proxies
        self.resource_path = root_path
        self.user_data_dir = user_data_dir
        self.asset_default = asset_default
        self.period_default = period_default
        self.subscribe_candle = []
        self.subscribe_candle_all_size = []
        self.subscribe_mood = []
        self.account_is_demo = 1
        self.suspend = 0.2
        self.codes_asset = {}
        self.api = None
        self.duration = None
        self.websocket_client = None
        self.websocket_thread = None
        self.debug_ws_enable = False
        self.resource_path = resource_path(root_path)
        if not email or not password:
            self.email, self.password = credentials()
        session = load_session(self.email, user_agent)
        self.session_data = session
        self.proxies = proxies

    @property
    def websocket(self):
        """Property to get websocket.
        :returns: The instance of: class:`WebSocket <websocket.WebSocket>`.
        """
        return self.websocket_client.wss

    @staticmethod
    async def _check_connect(state):
        """Check connection using the per-instance state object."""
        await asyncio.sleep(2)
        if state.check_accepted_connection == 1:
            return True
        return False

    async def check_connect(self):
        """Check connection using the current API's state."""
        if self.api is None:
            return False
        return await self._check_connect(self.api.state)

    def _capture_event_loop(self):
        """Capture and store the current event loop for use in WebSocket thread."""
        try:
            loop = asyncio.get_running_loop()
            if self.api and loop:
                self.api.event_loop = loop
        except RuntimeError:
            pass

    def set_session(self, user_agent: str, cookies: str = None, ssid: str = None):
        session = {
            "cookies": cookies,
            "token": ssid,
            "user_agent": user_agent
        }
        self.session_data = update_session(self.email, session)

    async def re_subscribe_stream(self):
        try:
            for ac in self.subscribe_candle:
                sp = ac.split(",")
                await self.start_candles_one_stream(sp[0], sp[1])
        except Exception as e:
            logger.warning("Failed to re-subscribe candle stream: %s", e)
        try:
            for ac in self.subscribe_candle_all_size:
                await self.start_candles_all_size_stream(ac)
        except Exception as e:
            logger.warning("Failed to re-subscribe all_size stream: %s", e)
        try:
            for ac in self.subscribe_mood:
                await self.start_mood_stream(ac)
        except Exception as e:
            logger.warning("Failed to re-subscribe mood stream: %s", e)

    async def get_instruments(self, timeout=DEFAULT_TIMEOUT):
        """Get instruments using true event-driven approach (no polling)."""
        self._capture_event_loop()  # Ensure event loop is captured

        if not self.api or not await self.check_connect():
            return []

        if self.api.instruments:
            return self.api.instruments

        try:
            # Wait for WebSocket event signaling instruments arrival
            await self.api.event_registry.wait_event('instruments_ready', timeout=timeout)
            return self.api.instruments or []
        except TimeoutError:
            logger.debug(f"Event timeout waiting for instruments, checking if data arrived...")
            # Event timeout - but data might still have arrived
            await asyncio.sleep(0.1)
            if self.api.instruments:
                logger.debug("Instruments data arrived after timeout")
                return self.api.instruments
            logger.error(f"Timeout waiting for instruments after {timeout}s")
            return []

    def get_all_asset_name(self):
        if self.api.instruments:
            return [[i[1], i[2].replace("\n", "")] for i in self.api.instruments]

    async def get_available_asset(self, asset_name: str, force_open: bool = False):
        _, asset_open = await self.check_asset_open(asset_name)
        if force_open and (not asset_open or not asset_open[2]):
            condition_otc = "otc" not in asset_name
            refactor_asset = asset_name.replace("_otc", "")
            asset_name = f"{asset_name}_otc" if condition_otc else refactor_asset
            _, asset_open = await self.check_asset_open(asset_name)

        return asset_name, asset_open

    async def check_asset_open(self, asset_name: str):
        instruments = await self.get_instruments()
        for i in instruments:
            if asset_name == i[1]:
                self.api.current_asset = asset_name
                return i, (i[0], i[2].replace("\n", ""), i[14])

        return [None, [None, None, None]]

    async def get_all_assets(self):
        instruments = await self.get_instruments()
        for i in instruments:
            if i[0] != "":
                self.codes_asset[i[1]] = i[0]

        return self.codes_asset

    async def get_candles(self, asset, end_from_time, offset, period, progressive=False, timeout=DEFAULT_TIMEOUT):
        if end_from_time is None:
            end_from_time = time.time()
        index = expiration.get_timestamp()
        self.api.candles.candles_data = None
        self.start_candles_stream(asset, period)
        self.api.get_candles(asset, index, end_from_time, offset, period)

        self._capture_event_loop()  # Ensure event loop is captured before waiting

        try:
            # Wait for WebSocket event signaling candles arrival (true event-driven, no polling)
            await self.api.event_registry.wait_event('candles_ready', timeout=timeout)
        except TimeoutError:
            logger.debug(f"Event timeout waiting for candles, checking if data arrived...")
            # Event timeout - but data might still have arrived, give it a moment
            await asyncio.sleep(0.1)
            if self.api.candles.candles_data is None:
                logger.error(f"Timeout waiting for candles after {timeout}s")
                return None

        candles = self.prepare_candles(asset, period)

        if progressive:
            return self.api.historical_candles.get("data", {})

        return candles

    async def get_history_line(self, asset, end_from_time, offset, timeout=DEFAULT_TIMEOUT):
        if end_from_time is None:
            end_from_time = time.time()
        index = expiration.get_timestamp()
        self.api.current_asset = asset
        self.api.historical_candles = None
        self.start_candles_stream(asset)
        self.api.get_history_line(self.codes_asset[asset], index, end_from_time, offset)
        start_time = time.time()
        while True:
            while await self.check_connect() and self.api.historical_candles is None:
                if time.time() - start_time > timeout:
                    logger.error(f"Timeout waiting for get_history_line data for {asset}.")
                    return None
                await asyncio.sleep(0.2)
            if self.api.historical_candles is not None:
                break
            if time.time() - start_time > timeout:
                logger.error(f"Timeout waiting for get_history_line data for {asset}.")
                return None
        return self.api.historical_candles

    async def get_candle_v2(self, asset, period, timeout=DEFAULT_TIMEOUT):
        self.api.candle_v2_data[asset] = None
        self.start_candles_stream(asset, period)
        start_time = time.time()
        while self.api.candle_v2_data[asset] is None:
            if time.time() - start_time > timeout:
                logger.error(f"Timeout waiting for get_candle_v2 data for {asset}.")
                return None
            await asyncio.sleep(0.2)
        candles = self.prepare_candles(asset, period)
        return candles

    def prepare_candles(self, asset: str, period: int):
        """
        Prepare candles data for a specified asset.

        Args:
            asset (str): Asset name.
            period (int): Period for fetching candles.

        Returns:
            list: List of prepared candles data.
        """
        candles_data = calculate_candles(self.api.candles.candles_data, period)
        candles_v2_data = process_candles_v2(self.api.candle_v2_data, asset, candles_data)
        new_candles = merge_candles(candles_v2_data)

        return new_candles

    async def connect(self):
        self.api = QuotexAPI(
            self.host,
            self.email,
            self.password,
            self.lang,
            resource_path=self.resource_path,
            user_data_dir=self.user_data_dir,
            proxies=self.proxies
        )

        await self.close()
        self.api.trace_ws = self.debug_ws_enable
        self.api.session_data = self.session_data
        self.api.current_asset = self.asset_default
        self.api.current_period = self.period_default
        self.api.state.SSID = self.session_data.get("token")

        if not self.session_data.get("token"):
            check, reason = await self.api.authenticate()
            if not check:
                return check, reason

        check, reason = await self.api.connect(self.account_is_demo)
        if not await self.check_connect():
            logger.error("Websocket failed to connect or connection was rejected.")
            self.session_data = {}
            return False, "Websocket connection rejected."

        return check, reason

    async def reconnect(self):
        await self.api.authenticate()

    def set_account_mode(self, balance_mode="PRACTICE"):
        """Set active account `real` or `practice`"""
        if balance_mode.upper() == "REAL":
            self.account_is_demo = 0
        elif balance_mode.upper() == "PRACTICE":
            self.account_is_demo = 1
        else:
            raise ValueError(f"Invalid balance mode '{balance_mode}'. Use 'REAL' or 'PRACTICE'.")

    async def change_account(self, balance_mode: str):
        """Change active account `real` or `practice`"""
        self.account_is_demo = 0 if balance_mode.upper() == "REAL" else 1
        self.api.change_account(self.account_is_demo)

    def change_time_offset(self, time_offset):
        return self.api.change_time_offset(time_offset)

    async def edit_practice_balance(self, amount=None, timeout=DEFAULT_TIMEOUT):
        self.api.training_balance_edit_request = None
        self.api.edit_training_balance(amount)
        start = time.time()
        while self.api.training_balance_edit_request is None:
            if time.time() - start > timeout:
                raise TimeoutError("Timeout waiting for practice balance edit response.")
            await asyncio.sleep(0.2)
        return self.api.training_balance_edit_request

    async def get_balance(self, timeout=DEFAULT_TIMEOUT):
        """Get account balance using true event-driven approach (no polling)."""
        self._capture_event_loop()  # Ensure event loop is captured

        if not self.api or not await self.check_connect():
            raise RuntimeError("Not connected to Quotex")

        if self.api.account_balance is not None:
            balance = self.api.account_balance.get("demoBalance") \
                if self.api.account_type > 0 else self.api.account_balance.get("liveBalance")
            return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

        try:
            # Wait for WebSocket event signaling balance arrival (true event-driven, no polling)
            await self.api.event_registry.wait_event('balance_ready', timeout=timeout)
        except TimeoutError:
            logger.debug(f"Event timeout waiting for balance, checking if data arrived...")
            # Event timeout - but data might still have arrived
            await asyncio.sleep(0.1)
            if self.api.account_balance is None:
                logger.error(f"Timeout waiting for balance after {timeout}s")
                raise

        balance = self.api.account_balance.get("demoBalance") \
            if self.api.account_type > 0 else self.api.account_balance.get("liveBalance")
        return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

    async def calculate_indicator(
            self, asset: str,
            indicator: str,
            params: dict = None,
            history_size: int = 3600,
            timeframe: int = 60
    ) -> dict:
        """
        Calcula indicadores técnicos para um ativo dado.

        Args:
            asset (str): Nome do ativo (ex: "EURUSD")
            indicator (str): Nome do indicador
            params (dict): Parâmetros específicos do indicador
            history_size (int): Tamanho do histórico em segundos
            timeframe (int): Temporalidade em segundos
        """
        valid_timeframes = [60, 300, 900, 1800, 3600, 7200, 14400, 86400]
        if timeframe not in valid_timeframes:
            return {"error": f"Timeframe inválido. Valores permitidos: {valid_timeframes}"}

        adjusted_history = max(history_size, timeframe * 50)

        candles = await self.get_candles(asset, time.time(), adjusted_history, timeframe)

        if not candles:
            return {"error": f"Não há dados disponíveis para o ativo {asset}"}

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
                    "timestamps": timestamps[-len(values):] if values else []
                }

            elif indicator == "MACD":
                fast_period = params.get("fast_period", 12)
                slow_period = params.get("slow_period", 26)
                signal_period = params.get("signal_period", 9)
                macd_data = indicators.calculate_macd(prices, fast_period, slow_period, signal_period)
                macd_data["timeframe"] = timeframe
                macd_data["timestamps"] = timestamps[-len(macd_data["macd"]):] if macd_data["macd"] else []
                return macd_data

            elif indicator == "SMA":
                period = params.get("period", 20)
                values = indicators.calculate_sma(prices, period)
                return {
                    "sma": values,
                    "current": values[-1] if values else None,
                    "history_size": len(values),
                    "timeframe": timeframe,
                    "timestamps": timestamps[-len(values):] if values else []
                }

            elif indicator == "EMA":
                period = params.get("period", 20)
                values = indicators.calculate_ema(prices, period)
                return {
                    "ema": values,
                    "current": values[-1] if values else None,
                    "history_size": len(values),
                    "timeframe": timeframe,
                    "timestamps": timestamps[-len(values):] if values else []
                }

            elif indicator == "BOLLINGER":
                period = params.get("period", 20)
                num_std = params.get("std", 2)
                bb_data = indicators.calculate_bollinger_bands(prices, period, num_std)
                bb_data["timeframe"] = timeframe
                bb_data["timestamps"] = timestamps[-len(bb_data["middle"]):] if bb_data["middle"] else []
                return bb_data

            elif indicator == "STOCHASTIC":
                k_period = params.get("k_period", 14)
                d_period = params.get("d_period", 3)
                stoch_data = indicators.calculate_stochastic(prices, highs, lows, k_period, d_period)
                stoch_data["timeframe"] = timeframe
                stoch_data["timestamps"] = timestamps[-len(stoch_data["k"]):] if stoch_data["k"] else []
                return stoch_data

            elif indicator == "ATR":
                period = params.get("period", 14)
                values = indicators.calculate_atr(highs, lows, prices, period)
                return {
                    "atr": values,
                    "current": values[-1] if values else None,
                    "history_size": len(values),
                    "timeframe": timeframe,
                    "timestamps": timestamps[-len(values):] if values else []
                }

            elif indicator == "ADX":
                period = params.get("period", 14)
                adx_data = indicators.calculate_adx(highs, lows, prices, period)
                adx_data["timeframe"] = timeframe
                adx_data["timestamps"] = timestamps[-len(adx_data["adx"]):] if adx_data["adx"] else []
                return adx_data

            elif indicator == "ICHIMOKU":
                tenkan_period = params.get("tenkan_period", 9)
                kijun_period = params.get("kijun_period", 26)
                senkou_b_period = params.get("senkou_b_period", 52)
                ichimoku_data = indicators.calculate_ichimoku(highs, lows, tenkan_period, kijun_period, senkou_b_period)
                ichimoku_data["timeframe"] = timeframe
                ichimoku_data["timestamps"] = timestamps[-len(ichimoku_data["tenkan"]):] if ichimoku_data[
                    "tenkan"] else []
                return ichimoku_data

            else:
                return {"error": f"Indicador '{indicator}' não suportado"}

        except Exception as e:
            return {"error": f"Erro calculando o indicador: {str(e)}"}

    async def subscribe_indicator(
            self, asset: str,
            indicator: str,
            params: dict = None,
            callback=None,
            timeframe: int = 60
    ):
        """
        Inscreve em atualizações em tempo real de um indicador.

        Args:
            asset (str): Nome do ativo
            indicator (str): Nome do indicador
            params (dict): Parâmetros do indicador
            callback (callable): Função que será chamada com cada atualização
            timeframe (int): Temporalidade em segundos
        """
        if not callback:
            raise ValueError("Deve fornecer uma função callback")

        valid_timeframes = [60, 300, 900, 1800, 3600, 7200, 14400, 86400]
        if timeframe not in valid_timeframes:
            raise ValueError(f"Timeframe inválido. Valores permitidos: {valid_timeframes}")

        try:
            self.start_candles_stream(asset, timeframe)

            while await self.check_connect():
                try:
                    real_time_candles = await self.get_realtime_candles(asset, timeframe)

                    if real_time_candles:
                        candles_list = sorted(real_time_candles.items(), key=lambda x: x[0])

                        prices = [float(candle[1]["close"]) for candle in candles_list]
                        highs = [float(candle[1]["high"]) for candle in candles_list]
                        lows = [float(candle[1]["low"]) for candle in candles_list]

                        min_periods = {
                            "RSI": 14,
                            "MACD": 26,
                            "BOLLINGER": 20,
                            "STOCHASTIC": 14,
                            "ADX": 14,
                            "ATR": 14,
                            "SMA": 20,
                            "EMA": 20,
                            "ICHIMOKU": 52
                        }

                        required_periods = min_periods.get(indicator.upper(), 14)
                        if len(prices) < required_periods:
                            historical_candles = await self.get_candles(
                                asset,
                                time.time(),
                                timeframe * required_periods * 2,
                                timeframe
                            )
                            if historical_candles:
                                prices = [float(candle["close"]) for candle in historical_candles] + prices
                                highs = [float(candle["high"]) for candle in historical_candles] + highs
                                lows = [float(candle["low"]) for candle in historical_candles] + lows

                        ti = TechnicalIndicators()
                        indicator_upper = indicator.upper()

                        result = {
                            "time": candles_list[-1][0],
                            "timeframe": timeframe,
                            "asset": asset
                        }

                        if indicator_upper == "RSI":
                            period = params.get("period", 14)
                            values = ti.calculate_rsi(prices, period)
                            result["value"] = values[-1] if values else None
                            result["all_values"] = values
                            result["indicator"] = "RSI"

                        elif indicator_upper == "MACD":
                            fast_period = params.get("fast_period", 12)
                            slow_period = params.get("slow_period", 26)
                            signal_period = params.get("signal_period", 9)
                            macd_data = ti.calculate_macd(prices, fast_period, slow_period, signal_period)
                            result["value"] = macd_data["current"]
                            result["all_values"] = macd_data
                            result["indicator"] = "MACD"

                        elif indicator_upper == "BOLLINGER":
                            period = params.get("period", 20)
                            num_std = params.get("std", 2)
                            bb_data = ti.calculate_bollinger_bands(prices, period, num_std)
                            result["value"] = bb_data["current"]
                            result["all_values"] = bb_data

                        elif indicator_upper == "STOCHASTIC":
                            k_period = params.get("k_period", 14)
                            d_period = params.get("d_period", 3)
                            stoch_data = ti.calculate_stochastic(prices, highs, lows, k_period, d_period)
                            result["value"] = stoch_data["current"]
                            result["all_values"] = stoch_data

                        elif indicator_upper == "ADX":
                            period = params.get("period", 14)
                            adx_data = ti.calculate_adx(highs, lows, prices, period)
                            result["value"] = adx_data["current"]
                            result["all_values"] = adx_data

                        elif indicator_upper == "ATR":
                            period = params.get("period", 14)
                            values = ti.calculate_atr(highs, lows, prices, period)
                            result["value"] = values[-1] if values else None
                            result["all_values"] = values

                        elif indicator_upper == "ICHIMOKU":
                            tenkan_period = params.get("tenkan_period", 9)
                            kijun_period = params.get("kijun_period", 26)
                            senkou_b_period = params.get("senkou_b_period", 52)
                            ichimoku_data = ti.calculate_ichimoku(highs, lows, tenkan_period, kijun_period,
                                                                  senkou_b_period)
                            result["value"] = ichimoku_data["current"]
                            result["all_values"] = ichimoku_data

                        else:
                            result["error"] = f"Indicador '{indicator}' não suportado para tempo real"

                        await callback(result)

                    await asyncio.sleep(1)

                except Exception as e:
                    logger.warning("Error in indicator subscription loop: %s", e)
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error in indicator subscription: %s", e)
        finally:
            try:
                self.stop_candles_stream(asset)
            except Exception:
                pass

    async def get_profile(self):
        return await self.api.get_profile()

    async def get_server_time(self):
        user_settings = await self.get_profile()
        offset_zone = user_settings.offset
        self.api.timesync.server_timestamp = expiration.get_server_timer(offset_zone)
        return self.api.timesync.server_timestamp

    async def get_history(self):
        """Get the trader's history based on account type.

        Returns:
            The trading history from the API.
        """
        account_type = "demo" if self.account_is_demo else "live"
        return await self.api.get_trader_history(account_type, page_number=1)

    async def buy(self, amount: float, asset: str, direction: str, duration: int, time_mode: str = "TIME"):
        """
        Buy Binary option

        Args:
            amount (float): Amount to buy.
            asset (str): Asset to buy.
            direction (str): Direction to buy.
            duration (int): Duration to buy.
            time_mode (str): Time mode to buy.

        Returns:
            The buy result.

        """
        self.api.buy_id = None
        request_id = expiration.get_timestamp()
        is_fast_option = time_mode.upper() == "TIME"
        self.start_candles_stream(asset, duration)
        await self.get_server_time()
        self.api.buy(amount, asset, direction, duration, request_id, is_fast_option)

        self._capture_event_loop()  # Ensure event loop is captured before waiting

        timeout = duration + 5 if duration else 30

        try:
            # Wait for WebSocket event signaling buy confirmation (true event-driven, no polling)
            await self.api.event_registry.wait_event('buy_confirmed', timeout=timeout)
        except TimeoutError as e:
            logger.error(str(e))
            return False, None

        if self.api.state.check_websocket_if_error:
            return False, self.api.state.websocket_error_reason

        return True, self.api.buy_successful

    async def open_pending(self, amount: float, asset: str, direction: str, duration: int, open_time: str = None):
        self.api.pending_id = None
        user_settings = await self.get_profile()
        offset_zone = user_settings.offset
        open_time = expiration.get_next_timeframe(
            int(time.time()),
            offset_zone,
            duration,
            open_time
        )
        self.api.open_pending(amount, asset, direction, duration, open_time)
        start = time.time()
        while await self.check_connect() and self.api.pending_id is None:
            if time.time() - start > 30:
                logger.error("Timeout pending order.")
                return False, "Timeout waiting for pending ID"
            await asyncio.sleep(0.2)
            if self.api.state.check_websocket_if_error:
                return False, self.api.state.websocket_error_reason
        else:
            status_buy = True
            self.api.instruments_follow(amount, asset, direction, duration, open_time)

        return status_buy, self.api.pending_successful

    async def sell_option(self, options_ids, timeout=DEFAULT_TIMEOUT):
        """Sell asset Quotex"""
        self.api.sell_option(options_ids)
        self.api.sold_options_respond = None
        start = time.time()
        while self.api.sold_options_respond is None:
            if time.time() - start > timeout:
                raise TimeoutError("Timeout waiting for sell option response.")
            await asyncio.sleep(0.2)
        return self.api.sold_options_respond

    def get_payment(self):
        """Payment Quotex server"""
        assets_data = {}
        for i in self.api.instruments:
            assets_data[i[2].replace("\n", "")] = {
                "turbo_payment": i[18],
                "payment": i[5],
                "profit": {
                    "1M": i[-9],
                    "5M": i[-8]
                },
                "open": i[14]
            }

        return assets_data

    def get_payout_by_asset(self, asset_name: str, timeframe: str = "1"):
        """Payout Quotex server"""
        assets_data = {}
        for i in self.api.instruments:
            if asset_name == i[1]:
                assets_data[i[1].replace("\n", "")] = {
                    "turbo_payment": i[18],
                    "payment": i[5],
                    "profit": {
                        "24H": i[-10],
                        "1M": i[-9],
                        "5M": i[-8]
                    },
                    "open": i[14]
                }
                break

        data = assets_data.get(asset_name)
        if timeframe == "all":
            return data.get("profit")

        return data.get("profit").get(f"{timeframe}M")

    async def start_remaing_time(self):
        now_stamp = datetime.fromtimestamp(expiration.get_timestamp())
        expiration_stamp = datetime.fromtimestamp(self.api.timesync.server_timestamp)
        remaing_time = int((expiration_stamp - now_stamp).total_seconds())
        while remaing_time >= 0:
            remaing_time -= 1
            logger.debug("Remaining %d seconds...", max(remaing_time, 0))
            await asyncio.sleep(1)

    async def check_win(self, id_number: int):
        """Check win based id"""
        task = asyncio.create_task(
            self.start_remaing_time()
        )
        start = time.time()
        while await self.check_connect():
            if time.time() - start > 86400: # Max wait 1 day safety cap
                break
            data_dict = self.api.listinfodata.get(id_number)
            if data_dict and data_dict.get("game_state") == 1:
                break
            await asyncio.sleep(0.2)
        task.cancel()
        self.api.listinfodata.delete(id_number)
        return data_dict["win"]

    def start_candles_stream(self, asset: str = "EURUSD", period: int = 0):
        """Start streaming candle data for a specified asset.

        Args:
            asset (str): The asset to stream data for.
            period (int, optional): The period for the candles. Defaults to 0.
        """
        self.api.current_asset = asset
        self.api.subscribe_realtime_candle(asset, period)
        self.api.chart_notification(asset)
        self.api.follow_candle(asset)

    async def store_settings_apply(
            self,
            asset: str = "EURUSD",
            period: int = 0,
            time_mode: str = "TIMER",
            deal: int = 5,
            percent_mode: bool = False,
            percent_deal: int = 1,
            timeout: int = DEFAULT_TIMEOUT
    ):
        """
        Applies trading settings for a specific asset and retrieves the updated investment settings.

        Args:
            asset (str): The asset for which to apply the settings.
            period (int, optional): The trading period in seconds. Defaults to 0.
            time_mode (str, optional): Whether to switch time mode. Defaults to "TIMER".
            deal (float, optional): The fixed amount for each deal. Defaults to 5.
            percent_mode (bool, optional): Whether to enable percentage-based deals. Defaults to False.
            percent_deal (float, optional): The percentage value for percentage-based deals. Defaults to 1.
            timeout (int, optional): Maximum seconds to wait. Defaults to DEFAULT_TIMEOUT.

        Returns:
            dict: The updated investment settings for the specified asset.

        Raises:
            TimeoutError: If the investment settings cannot be retrieved within timeout.
        """
        is_fast_option = False if time_mode.upper() == "TIMER" else True
        self.api.current_asset = asset
        self.api.settings_apply(
            asset,
            period,
            is_fast_option=is_fast_option,
            deal=deal,
            percent_mode=percent_mode,
            percent_deal=percent_deal
        )
        await asyncio.sleep(0.2)
        start = time.time()
        while True:
            self.api.refresh_settings()
            if self.api.settings_list:
                investments_settings = self.api.settings_list
                break
            if time.time() - start > timeout:
                raise TimeoutError("Timeout waiting for settings response.")
            await asyncio.sleep(0.2)

        return investments_settings

    def stop_candles_stream(self, asset):
        self.api.unsubscribe_realtime_candle(asset)
        self.api.unfollow_candle(asset)

    def start_signals_data(self):
        self.api.signals_subscribe()

    async def opening_closing_current_candle(self, asset: str, period: int = 0):
        candles_data = {}
        candles_tick = await self.get_realtime_candles(asset)
        logger.debug("Candles tick data: %s", candles_tick)
        aggregate = aggregate_candle(candles_tick, candles_data)
        logger.debug("Aggregated candle: %s", aggregate)
        candles_dict = list(aggregate.values())[0]
        candles_dict['opening'] = candles_dict.pop('timestamp')
        candles_dict['closing'] = candles_dict['opening'] + period
        candles_dict['remaining'] = candles_dict['closing'] - int(time.time())
        return candles_dict

    async def start_realtime_price(self, asset: str, period: int = 0, timeout: int = DEFAULT_TIMEOUT):
        self.start_candles_stream(asset, period)
        start = time.time()
        while True:
            if self.api.realtime_price.get(asset):
                return self.api.realtime_price
            if time.time() - start > timeout:
                raise TimeoutError(f"Timeout waiting for realtime price data for {asset}.")
            await asyncio.sleep(0.2)

    async def start_realtime_sentiment(self, asset: str, period: int = 0, timeout: int = DEFAULT_TIMEOUT):
        self.start_candles_stream(asset, period)
        start = time.time()
        while True:
            if self.api.realtime_sentiment.get(asset):
                return self.api.realtime_sentiment[asset]
            if time.time() - start > timeout:
                raise TimeoutError(f"Timeout waiting for realtime sentiment data for {asset}.")
            await asyncio.sleep(0.2)

    async def start_realtime_candle(self, asset: str, period: int = 0, timeout: int = DEFAULT_TIMEOUT):
        self.start_candles_stream(asset, period)
        data = {}
        start = time.time()
        while True:
            if self.api.realtime_candles.get(asset):
                tick = self.api.realtime_candles
                return process_tick(tick, period, data)
            if time.time() - start > timeout:
                raise TimeoutError(f"Timeout waiting for realtime candle data for {asset}.")
            await asyncio.sleep(0.2)

    async def get_realtime_candles(self, asset: str):
        """Retrieve real-time candle data for a specified asset.

        Args:
            asset (str): The asset to get candle data for.

        Returns:
            dict: A dictionary of real-time candle data.
        """
        return self.api.realtime_candles.get(asset, {})

    async def get_realtime_sentiment(self, asset: str):
        return self.api.realtime_sentiment.get(asset, {})

    async def get_realtime_price(self, asset: str):
        return self.api.realtime_price.get(asset, {})

    def get_signal_data(self):
        return self.api.signal_data

    def get_profit(self):
        return self.api.profit_in_operation or 0

    async def get_result(self, operation_id: str):
        """Check if the trade is a win based on its ID.

        Args:
            operation_id (str): The ID of the trade to check.
        Returns:
            str: win if the trade is a win, loss otherwise.
            float: The profit from operations; returns 0 if no profit is recorded.
        """
        data_history = await self.get_history()
        for item in data_history:
            if item.get("ticket") == operation_id:
                profit = float(item.get("profitAmount", 0))
                status = "win" if profit > 0 else "loss"
                return status, item

        return None, "OperationID Not Found."

    async def start_candles_one_stream(self, asset, size):
        if not (str(asset + "," + str(size)) in self.subscribe_candle):
            self.subscribe_candle.append((asset + "," + str(size)))
        start = time.time()
        self.api.candle_generated_check[str(asset)][int(size)] = {}
        while True:
            if time.time() - start > 20:
                logger.error(
                    '**error** start_candles_one_stream late for 20 sec')
                return False
            try:
                if self.api.candle_generated_check[str(asset)][int(size)]:
                    return True
            except (KeyError, TypeError):
                pass
            try:
                self.api.follow_candle(self.codes_asset[asset])
            except Exception as e:
                logger.error('**error** start_candles_stream reconnect: %s', e)
                await self.connect()
            await asyncio.sleep(0.2)

    async def start_candles_all_size_stream(self, asset):
        self.api.candle_generated_all_size_check[str(asset)] = {}
        if not (str(asset) in self.subscribe_candle_all_size):
            self.subscribe_candle_all_size.append(str(asset))
        start = time.time()
        while await self.check_connect():
            if time.time() - start > 20:
                logger.error(f'**error** fail {asset} start_candles_all_size_stream late for 10 sec')
                return False
            try:
                if self.api.candle_generated_all_size_check[str(asset)]:
                    return True
            except (KeyError, TypeError):
                pass
            try:
                self.api.subscribe_all_size(self.codes_asset[asset])
            except Exception as e:
                logger.error(
                    '**error** start_candles_all_size_stream reconnect: %s', e)
                await self.connect()
            await asyncio.sleep(0.2)

    async def start_mood_stream(self, asset, instrument="turbo-option"):
        if asset not in self.subscribe_mood:
            self.subscribe_mood.append(asset)
        while True:
            self.api.subscribe_Traders_mood(
                asset[asset], instrument)
            try:
                self.api.traders_mood[self.codes_asset[asset]] = self.codes_asset[asset]
                break
            finally:
                await asyncio.sleep(0.2)

    async def close(self):
        return await self.api.close()
