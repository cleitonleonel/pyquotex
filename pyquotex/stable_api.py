import time
import logging
import asyncio
from datetime import datetime
from . import expiration
from . import global_value
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


class Quotex:

    def __init__(
            self,
            email=None,
            password=None,
            lang="pt",
            user_agent="Quotex/1.0",
            root_path=".",
            user_data_dir="browser",
            asset_default="EURUSD",
            period_default=60
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
        self.lang = lang
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
        session = load_session(user_agent)
        self.session_data = session
        if not email or not password:
            self.email, self.password = credentials()

    @property
    def websocket(self):
        """Property to get websocket.
        :returns: The instance of :class:`WebSocket <websocket.WebSocket>`.
        """
        return self.websocket_client.wss

    @staticmethod
    async def check_connect():
        await asyncio.sleep(2)
        if global_value.check_accepted_connection == 1:
            return True

        return False

    def set_session(self, user_agent: str, cookies: str = None, ssid: str = None):
        session = {
            "cookies": cookies,
            "token": ssid,
            "user_agent": user_agent
        }
        self.session_data = update_session(session)

    async def re_subscribe_stream(self):
        try:
            for ac in self.subscribe_candle:
                sp = ac.split(",")
                await self.start_candles_one_stream(sp[0], sp[1])
        except:
            pass
        try:
            for ac in self.subscribe_candle_all_size:
                await self.start_candles_all_size_stream(ac)
        except:
            pass
        try:
            for ac in self.subscribe_mood:
                await self.start_mood_stream(ac)
        except:
            pass

    async def get_instruments(self):
        while self.check_connect and self.api.instruments is None:
            await asyncio.sleep(0.2)
        return self.api.instruments or []

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

    async def get_candles(self, asset, end_from_time, offset, period, progressive=False):
        if end_from_time is None:
            end_from_time = time.time()
        index = expiration.get_timestamp()
        self.api.candles.candles_data = None
        self.start_candles_stream(asset, period)
        self.api.get_candles(asset, index, end_from_time, offset, period)
        while True:
            while self.check_connect and self.api.candles.candles_data is None:
                await asyncio.sleep(0.1)
            if self.api.candles.candles_data is not None:
                break

        candles = self.prepare_candles(asset, period)

        if progressive:
            return self.api.historical_candles.get("data", {})

        return candles

    async def get_history_line(self, asset, end_from_time, offset):
        if end_from_time is None:
            end_from_time = time.time()
        index = expiration.get_timestamp()
        self.api.current_asset = asset
        self.api.historical_candles = None
        self.start_candles_stream(asset)
        self.api.get_history_line(self.codes_asset[asset], index, end_from_time, offset)
        while True:
            while self.check_connect and self.api.historical_candles is None:
                await asyncio.sleep(0.2)
            if self.api.historical_candles is not None:
                break
        return self.api.historical_candles

    async def get_candle_v2(self, asset, period):
        self.api.candle_v2_data[asset] = None
        self.start_candles_stream(asset, period)
        while self.api.candle_v2_data[asset] is None:
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
            "qxbroker.com",
            self.email,
            self.password,
            self.lang,
            resource_path=self.resource_path,
            user_data_dir=self.user_data_dir
        )
        await self.close()
        self.api.trace_ws = self.debug_ws_enable
        self.api.session_data = self.session_data
        self.api.current_asset = self.asset_default
        self.api.current_period = self.period_default
        global_value.SSID = self.session_data.get("token")

        if not self.session_data.get("token"):
            await self.api.authenticate()

        check, reason = await self.api.connect(self.account_is_demo)

        if not await self.check_connect():
            logger.debug("Reconnecting on websocket")
            return await self.connect()

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
            logger.error("ERROR doesn't have this mode")
            exit(1)

    async def change_account(self, balance_mode: str):
        """Change active account `real` or `practice`"""
        self.account_is_demo = 0 if balance_mode.upper() == "REAL" else 1
        self.api.change_account(self.account_is_demo)

    def change_time_offset(self, time_offset):
        return self.api.change_time_offset(time_offset)

    async def edit_practice_balance(self, amount=None):
        self.api.training_balance_edit_request = None
        self.api.edit_training_balance(amount)
        while self.api.training_balance_edit_request is None:
            await asyncio.sleep(0.2)
        return self.api.training_balance_edit_request

    async def get_balance(self):
        while self.api.account_balance is None:
            await asyncio.sleep(0.2)
        balance = self.api.account_balance.get("demoBalance") \
            if self.api.account_type > 0 else self.api.account_balance.get("liveBalance")
        return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

    # Agregar al archivo stable_api.py dentro de la clase Quotex

    async def calculate_indicator(
            self, asset: str,
            indicator: str,
            params: dict = None,
            history_size: int = 3600,
            timeframe: int = 60
    ) -> dict:
        """
        Calcula indicadores técnicos para un activo dado

        Args:
            asset (str): Nombre del activo (ej: "EURUSD")
            indicator (str): Nombre del indicador
            params (dict): Parámetros específicos del indicador
            history_size (int): Tamaño del histórico en segundos
            timeframe (int): Temporalidad en segundos. Valores posibles:
                - 60: 1 minuto
                - 300: 5 minutos
                - 900: 15 minutos
                - 1800: 30 minutos
                - 3600: 1 hora
                - 7200: 2 horas
                - 14400: 4 horas
                - 86400: 1 día
        """
        # Validar timeframe
        valid_timeframes = [60, 300, 900, 1800, 3600, 7200, 14400, 86400]
        if timeframe not in valid_timeframes:
            return {"error": f"Timeframe no válido. Valores permitidos: {valid_timeframes}"}

        # Ajustar history_size para asegurar suficientes velas según el timeframe
        adjusted_history = max(history_size, timeframe * 50)  # Asegurar al menos 50 velas

        candles = await self.get_candles(asset, time.time(), adjusted_history, timeframe)

        if not candles:
            return {"error": f"No hay datos disponibles para el activo {asset}"}

        prices = [float(candle["close"]) for candle in candles]
        highs = [float(candle["high"]) for candle in candles]
        lows = [float(candle["low"]) for candle in candles]
        timestamps = [candle["time"] for candle in candles]

        indicators = TechnicalIndicators()
        indicator = indicator.upper()

        try:
            # RSI
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

            # MACD
            elif indicator == "MACD":
                fast_period = params.get("fast_period", 12)
                slow_period = params.get("slow_period", 26)
                signal_period = params.get("signal_period", 9)
                macd_data = indicators.calculate_macd(prices, fast_period, slow_period, signal_period)
                macd_data["timeframe"] = timeframe
                macd_data["timestamps"] = timestamps[-len(macd_data["macd"]):] if macd_data["macd"] else []
                return macd_data

            # SMA
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

            # EMA
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

            # BOLLINGER
            elif indicator == "BOLLINGER":
                period = params.get("period", 20)
                num_std = params.get("std", 2)
                bb_data = indicators.calculate_bollinger_bands(prices, period, num_std)
                bb_data["timeframe"] = timeframe
                bb_data["timestamps"] = timestamps[-len(bb_data["middle"]):] if bb_data["middle"] else []
                return bb_data

            # STOCHASTIC
            elif indicator == "STOCHASTIC":
                k_period = params.get("k_period", 14)
                d_period = params.get("d_period", 3)
                stoch_data = indicators.calculate_stochastic(prices, highs, lows, k_period, d_period)
                stoch_data["timeframe"] = timeframe
                stoch_data["timestamps"] = timestamps[-len(stoch_data["k"]):] if stoch_data["k"] else []
                return stoch_data

            # ATR
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

            # ADX
            elif indicator == "ADX":
                period = params.get("period", 14)
                adx_data = indicators.calculate_adx(highs, lows, prices, period)
                adx_data["timeframe"] = timeframe
                adx_data["timestamps"] = timestamps[-len(adx_data["adx"]):] if adx_data["adx"] else []
                return adx_data

            # ICHIMOKU
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
                return {"error": f"Indicador '{indicator}' no soportado"}

        except Exception as e:
            return {"error": f"Error calculando el indicador: {str(e)}"}

    async def subscribe_indicator(
            self, asset: str,
            indicator: str,
            params: dict = None,
            callback=None,
            timeframe: int = 60
    ):
        """
        Suscribe a actualizaciones en tiempo real de un indicador

        Args:
            asset (str): Nombre del activo
            indicator (str): Nombre del indicador
            params (dict): Parámetros del indicador
            callback (callable): Función que se llamará con cada actualización
            timeframe (int): Temporalidad en segundos
        """
        if not callback:
            raise ValueError("Debe proporcionar una función callback")

        # Validar timeframe
        valid_timeframes = [60, 300, 900, 1800, 3600, 7200, 14400, 86400]
        if timeframe not in valid_timeframes:
            raise ValueError(f"Timeframe no válido. Valores permitidos: {valid_timeframes}")

        try:
            # Iniciar stream de velas
            self.start_candles_stream(asset, timeframe)

            while True:
                try:
                    # Obtener velas en tiempo real
                    real_time_candles = await self.get_realtime_candles(asset, timeframe)

                    if real_time_candles:
                        # Convertir el diccionario a lista ordenada por tiempo
                        candles_list = sorted(real_time_candles.items(), key=lambda x: x[0])

                        # Extraer datos de las velas
                        prices = [float(candle[1]["close"]) for candle in candles_list]
                        highs = [float(candle[1]["high"]) for candle in candles_list]
                        lows = [float(candle[1]["low"]) for candle in candles_list]

                        # Asegurar que tenemos suficientes datos
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
                            # Si no hay suficientes datos, obtener histórico
                            historical_candles = await self.get_candles(
                                asset,
                                time.time(),
                                timeframe * required_periods * 2,  # Doble del período requerido
                                timeframe
                            )
                            if historical_candles:
                                # Combinar datos históricos con tiempo real
                                prices = [float(candle["close"]) for candle in historical_candles] + prices
                                highs = [float(candle["high"]) for candle in historical_candles] + highs
                                lows = [float(candle["low"]) for candle in historical_candles] + lows

                        indicators = TechnicalIndicators()
                        indicator = indicator.upper()

                        # Calcular el indicador con los datos actualizados
                        result = {
                            "time": candles_list[-1][0],
                            "timeframe": timeframe,
                            "asset": asset
                        }

                        if indicator == "RSI":
                            period = params.get("period", 14)
                            values = indicators.calculate_rsi(prices, period)
                            result["value"] = values[-1] if values else None
                            result["all_values"] = values
                            result["indicator"] = "RSI"

                        elif indicator == "MACD":
                            fast_period = params.get("fast_period", 12)
                            slow_period = params.get("slow_period", 26)
                            signal_period = params.get("signal_period", 9)
                            macd_data = indicators.calculate_macd(prices, fast_period, slow_period, signal_period)
                            result["value"] = macd_data["current"]
                            result["all_values"] = macd_data
                            result["indicator"] = "MACD"

                        # BOLLINGER
                        elif indicator == "BOLLINGER":
                            period = params.get("period", 20)
                            num_std = params.get("std", 2)
                            bb_data = indicators.calculate_bollinger_bands(prices, period, num_std)
                            result["value"] = bb_data["current"]
                            result["all_values"] = bb_data

                        # STOCHASTIC
                        elif indicator == "STOCHASTIC":
                            k_period = params.get("k_period", 14)
                            d_period = params.get("d_period", 3)
                            stoch_data = indicators.calculate_stochastic(prices, highs, lows, k_period, d_period)
                            result["value"] = stoch_data["current"]
                            result["all_values"] = stoch_data

                        # ADX
                        elif indicator == "ADX":
                            period = params.get("period", 14)
                            adx_data = indicators.calculate_adx(highs, lows, prices, period)
                            result["value"] = adx_data["current"]
                            result["all_values"] = adx_data

                        # ATR
                        elif indicator == "ATR":
                            period = params.get("period", 14)
                            values = indicators.calculate_atr(highs, lows, prices, period)
                            result["value"] = values[-1] if values else None
                            result["all_values"] = values

                        # ICHIMOKU
                        elif indicator == "ICHIMOKU":
                            tenkan_period = params.get("tenkan_period", 9)
                            kijun_period = params.get("kijun_period", 26)
                            senkou_b_period = params.get("senkou_b_period", 52)
                            ichimoku_data = indicators.calculate_ichimoku(highs, lows, tenkan_period, kijun_period,
                                                                          senkou_b_period)
                            result["value"] = ichimoku_data["current"]
                            result["all_values"] = ichimoku_data

                        else:
                            result["error"] = f"Indicador '{indicator}' no soportado para tiempo real"

                        # Llamar al callback con el resultado
                        await callback(result)

                    await asyncio.sleep(1)  # Esperar 1 segundo entre actualizaciones

                except Exception as e:
                    print(f"Error en la suscripción: {str(e)}")
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error en la suscripción: {str(e)}")
        finally:
            # Limpiar suscripciones al salir
            try:
                self.stop_candles_stream(asset)
            except:
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

        count = 0.1
        while self.api.buy_id is None:
            count += 0.1
            if count > duration:
                status_buy = False
                break
            await asyncio.sleep(0.2)
            if global_value.check_websocket_if_error:
                return False, global_value.websocket_error_reason
        else:
            status_buy = True

        return status_buy, self.api.buy_successful

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
        count = 0.1
        while self.api.pending_id is None:
            count += 0.1
            if count > duration:
                status_buy = False
                break
            await asyncio.sleep(0.2)
            if global_value.check_websocket_if_error:
                return False, global_value.websocket_error_reason
        else:
            status_buy = True
            self.api.instruments_follow(amount, asset, direction, duration, open_time)

        return status_buy, self.api.pending_successful

    async def sell_option(self, options_ids):
        """Sell asset Quotex"""
        self.api.sell_option(options_ids)
        self.api.sold_options_respond = None
        while self.api.sold_options_respond is None:
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

    # Function suggested by https://t.me/Suppor_Mk in the message on telegram https://t.me/c/2215782682/1/2990
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
            print(f"\rRemaining {remaing_time if remaing_time > 0 else 0} seconds...", end="")
            await asyncio.sleep(1)

    async def check_win(self, id_number: int):
        """Check win based id"""
        task = asyncio.create_task(
            self.start_remaing_time()
        )
        while True:
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
            percent_deal: int = 1
    ):
        """
        Applies trading settings for a specific asset and retrieves the updated investment settings.

        This function sets up trading parameters for the specified asset, including the period,
        deal amount, and percentage mode if applicable. It then waits for the updated investment
        settings to be available and returns them.

        Args:
            asset (str): The asset for which to apply the settings.
            period (int, optional): The trading period in seconds. Defaults to 0.
            time_mode (bool, optional): Whether to switch time mode. Defaults to False.
            deal (float, optional): The fixed amount for each deal. Defaults to 5.
            percent_mode (bool, optional): Whether to enable percentage-based deals. Defaults to False.
            percent_deal (float, optional): The percentage value for percentage-based deals. Defaults to 1.

        Returns:
            dict: The updated investment settings for the specified asset.

        Raises:
            ValueError: If the investment settings cannot be retrieved after multiple attempts.

        Notes:
            - This function continuously refreshes the settings until they are available.
            - A sleep interval is used to prevent excessive API calls.
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
        while True:
            self.api.refresh_settings()
            if self.api.settings_list:
                investments_settings = self.api.settings_list
                break
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
        print(candles_tick)
        aggregate = aggregate_candle(candles_tick, candles_data)
        print(aggregate)
        candles_dict = list(aggregate.values())[0]
        candles_dict['opening'] = candles_dict.pop('timestamp')
        candles_dict['closing'] = candles_dict['opening'] + period
        candles_dict['remaining'] = candles_dict['closing'] - int(time.time())
        return candles_dict


    async def start_realtime_price(self, asset: str, period: int = 0):
        self.start_candles_stream(asset, period)
        while True:
            if self.api.realtime_price.get(asset):
                return self.api.realtime_price
            await asyncio.sleep(0.2)

    async def start_realtime_sentiment(self, asset: str, period: int = 0):
        self.start_candles_stream(asset, period)
        while True:
            if self.api.realtime_sentiment.get(asset):
                return self.api.realtime_sentiment[asset]
            await asyncio.sleep(0.2)

    async def start_realtime_candle(self, asset: str, period: int = 0):
        self.start_candles_stream(asset, period)
        data = {}
        while True:
            print("Tá agarrado....")
            if self.api.realtime_candles.get(asset):
                tick = self.api.realtime_candles
                return process_tick(tick, period, data)
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
            except:
                pass
            try:
                self.api.follow_candle(self.codes_asset[asset])
            except:
                logger.error('**error** start_candles_stream reconnect')
                await self.connect()
            await asyncio.sleep(0.2)

    async def start_candles_all_size_stream(self, asset):
        self.api.candle_generated_all_size_check[str(asset)] = {}
        if not (str(asset) in self.subscribe_candle_all_size):
            self.subscribe_candle_all_size.append(str(asset))
        start = time.time()
        while True:
            if time.time() - start > 20:
                logger.error(f'**error** fail {asset} start_candles_all_size_stream late for 10 sec')
                return False
            try:
                if self.api.candle_generated_all_size_check[str(asset)]:
                    return True
            except:
                pass
            try:
                self.api.subscribe_all_size(self.codes_asset[asset])
            except:
                logger.error(
                    '**error** start_candles_all_size_stream reconnect')
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
