import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable

import orjson

from . import expiration
from .api import QuotexAPI
from .config import (
    load_session,
    update_session,
    resource_path
)
from .utils.indicators import TechnicalIndicators
from .utils.processor import (
    calculate_candles,
    process_candles_v2,
    merge_candles,
    process_tick,
    aggregate_candle
)
from .utils.services import truncate

logger = logging.getLogger(__name__)

# Default timeout (seconds) for async polling loops
DEFAULT_TIMEOUT = 30


class Quotex:

    def __init__(
            self,
            email: str,
            password: str,
            host: str = "qxbroker.com",
            lang: str = "pt",
            user_agent: str = "Quotex/1.0",
            root_path: str = ".",
            user_data_dir: str = "browser",
            asset_default: str = "EURUSD",
            period_default: int = 60,
            proxies: dict[str, str] | None = None,
            on_otp_callback: Callable | None = None
    ):
        """
        Initializes the Quotex stable API wrapper.

        Args:
            email (str): User email.
            password (str): User password.
            host (str): Broker hostname. Defaults to "qxbroker.com".
            lang (str): Language code. Defaults to "pt".
            user_agent (str): Browser User-Agent. Defaults to "Quotex/1.0".
            root_path (str): Root directory for local storage. Defaults to ".".
            user_data_dir (str): Directory for browser profile data.
                Defaults to "browser".
            asset_default (str): Default asset to use. Defaults to "EURUSD".
            period_default (int): Default candle period in seconds.
                Defaults to 60.
            proxies (dict, optional): Proxy configuration.
            on_otp_callback (callable, optional): Callback for 2FA/OTP input.
        """
        self.size = [
            5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
            3600, 7200, 14400, 86400
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
        self.subscribe_candle: list[str] = []
        self.subscribe_candle_all_size: list[str] = []
        self.subscribe_mood: list[str] = []
        self.account_is_demo: int = 1
        self.suspend: float = 0.2
        self.codes_asset: dict[str, str] = {}
        self.api: QuotexAPI | None = None
        self.duration: int | None = None
        self.websocket_client: Any = None
        self.websocket_thread: Any = None
        self.debug_ws_enable: bool = False
        self.resource_path = resource_path(root_path)
        session = load_session(self.email, user_agent)
        self.session_data = session
        self.on_otp_callback = on_otp_callback

    @property
    def websocket(self) -> Any:
        """Property to get websocket.
        :returns: The active WebSocket instance.
        """
        return self.api.websocket if self.api else None

    @staticmethod
    async def _check_connect(state: Any) -> bool:
        """Check connection using the per-instance state object."""
        await asyncio.sleep(2)
        if state.check_accepted_connection == 1:
            return True
        return False

    async def check_connect(self) -> bool:
        """Check connection using the current API's state."""
        if self.api is None:
            return False
        return await self._check_connect(self.api.state)

    def set_session(
            self,
            user_agent: str,
            cookies: str | None = None,
            ssid: str | None = None
    ) -> None:
        """
        Manually sets the session data.

        Args:
            user_agent (str): The User-Agent string.
            cookies (str, optional): The raw cookie string.
            ssid (str, optional): The SSID token.
        """
        session = {
            "cookies": cookies,
            "token": ssid,
            "user_agent": user_agent
        }
        self.session_data = update_session(self.email, session)

    async def re_subscribe_stream(self) -> None:
        """Re-subscribes to all active candle and mood streams."""
        try:
            for ac in self.subscribe_candle:
                sp = ac.split(",")
                await self.start_candles_one_stream(sp[0], int(sp[1]))
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

    async def get_instruments(
            self, timeout: int = DEFAULT_TIMEOUT
    ) -> list[Any]:
        """Get instruments using a true event-driven approach."""
        if not self.api or not await self.check_connect():
            return []

        if self.api.instruments and len(self.api.instruments) > 0:
            return self.api.instruments

        try:
            # Request instruments explicitly
            await self.api.get_instruments()
            # Wait for WebSocket event signaling instruments arrival
            await self.api.event_registry.wait_event(
                'instruments_ready', timeout=timeout
            )

            if not self.api.instruments:
                # Try one last wait if empty
                await asyncio.sleep(2)

            return self.api.instruments or []
        except TimeoutError:
            logger.error(
                "Timeout waiting for instruments after %ds", timeout
            )
            return []

    def get_all_asset_name(self) -> list[list[str]] | None:
        """
        Retrieves names of all available assets.

        Returns:
            list: List of assets with ID and display name.
        """
        if self.api and self.api.instruments:
            return [
                [i[1], i[2].replace("\n", "")]
                for i in self.api.instruments
            ]
        return None

    async def get_available_asset(
            self, asset_name: str, force_open: bool = False
    ) -> tuple[str, Any]:
        """
        Retrieves detailed information for an asset if it is currently open.

        Args:
            asset_name (str): Asset name.
            force_open (bool, optional): Try to find the OTC version if closed.
                Defaults to False.

        Returns:
            tuple: (Final asset name, Asset status info).
        """
        _, asset_open = await self.check_asset_open(asset_name)
        if force_open and (not asset_open or not asset_open[2]):
            condition_otc = "otc" not in asset_name
            refactor_asset = asset_name.replace("_otc", "")
            asset_name = (
                f"{asset_name}_otc" if condition_otc else refactor_asset
            )
            _, asset_open = await self.check_asset_open(asset_name)

        return asset_name, asset_open

    async def check_asset_open(
            self, asset_name: str
    ) -> tuple[list[Any] | None, tuple[Any, Any, Any]]:
        """
        Checks if a specific asset is currently available for trading.

        Args:
            asset_name (str): The name of the asset.

        Returns:
            tuple: (Raw instrument data, Formatted status info).
        """
        instruments = await self.get_instruments()
        for i in instruments:
            if asset_name == i[1]:
                if self.api:
                    self.api.current_asset = asset_name
                return i, (i[0], i[2].replace("\n", ""), i[14])

        return None, (None, None, None)

    async def get_all_assets(self) -> dict[str, str]:
        """
        Retrieves a mapping of all asset names to their internal codes.

        Returns:
            dict: Mapping of asset names to codes.
        """
        instruments = await self.get_instruments()
        for i in instruments:
            if i[0] != "":
                self.codes_asset[i[1]] = i[0]

        return self.codes_asset

    async def get_candles(
            self,
            asset: str,
            end_from_time: float | None,
            offset: int,
            period: int,
            progressive: bool = False,
            timeout: int = DEFAULT_TIMEOUT
    ) -> list[dict[str, Any]] | None:
        """Retrieves candles for a specific asset."""
        if self.api is None:
            return None

        if end_from_time is None:
            end_from_time = time.time()

        index = expiration.get_timestamp()
        self.api.candles.candles_data = None

        # Clear event state before requesting data to prevent
        # race with WS response
        await self.api.event_registry.clear_event(f'candles_ready_{asset}')

        await self.start_candles_stream(asset, period)
        await self.api.get_candles(asset, index, end_from_time, offset, period)

        try:
            # Wait for WebSocket event signaling candles' arrival
            history_data = await self.api.event_registry.wait_event(
                f'candles_ready_{asset}', timeout=timeout
            )
        except TimeoutError:
            logger.error(
                "Timeout waiting for candles for %s after %ds",
                asset, timeout
            )
            return None

        # Pass the asset-specific history directly to avoid
        # multi-asset state races
        candles = self.prepare_candles(asset, period, history_data)

        if progressive:
            return self.api.historical_candles.get("data", {})

        return candles

    async def _fetch_historical_batch(
            self,
            asset: str,
            fetch_time: int,
            offset: int,
            period: int,
            index: int,
            timeout: int
    ) -> dict[str, Any] | None:
        """Low-level batch fetcher for a specific time point and index."""
        if self.api is None:
            return None

        payload = {
            "asset": asset,
            "index": index,
            "time": fetch_time,
            "offset": offset,
            "period": period
        }
        ws_msg = f'42["history/load",{orjson.dumps(payload).decode()}]'

        # Clear specific event to ensure fresh wait
        event_name = f'candles_ready_{asset}_{index}'
        await self.api.event_registry.clear_event(event_name)

        await self.api.send_websocket_request(ws_msg)

        try:
            return await self.api.event_registry.wait_event(
                event_name, timeout=timeout
            )
        except TimeoutError:
            logger.warning(
                "Batch fetch timeout at %d (index %d) for %s",
                fetch_time, index, asset
            )
            return None

    def _parse_historical_candles(
            self, raw_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Standardizes raw candle data into a uniform list of dicts."""
        raw_candles = raw_data.get("data", []) or raw_data.get("candles", [])
        if not raw_candles:
            return []

        parsed = []
        for c in raw_candles:
            if isinstance(c, list) and len(c) >= 5:
                parsed.append({
                    "time": int(c[0]),
                    "open": float(c[1]),
                    "close": float(c[2]),
                    "high": float(c[3]),
                    "low": float(c[4])
                })
            elif isinstance(c, dict) and "time" in c:
                parsed.append(c)
        return parsed

    async def get_historical_candles(
            self,
            asset: str,
            amount_of_seconds: int,
            period: int,
            timeout: int = DEFAULT_TIMEOUT,
            progress_callback: Callable[[int, int, int], None] | None = None
    ) -> list[dict[str, Any]]:
        """
        Retrieves extensive historical candle data by paginating backwards.
        Bypasses the broker's 200-candle limit by stitching batches together.
        """
        all_candles: dict[int, dict[str, Any]] = {}
        current_time = int(time.time())
        target_oldest_time = current_time - amount_of_seconds

        # Optimize chunk offset based on period (max ~200 candles per batch)
        chunk_offset = period * 200

        await self.start_candles_stream(asset, period)

        # 1. Fetch initial batch
        candles = await self.get_candles(
            asset, float(current_time), chunk_offset, period, timeout=timeout
        )
        if not candles:
            logger.warning(f"Failed to fetch initial candles for {asset}")
            return []

        for c in candles:
            all_candles[c['time']] = c

        oldest_time = candles[0]['time']

        # 2. Iterate backward
        while oldest_time > target_oldest_time:
            # Generate a unique browser-style index
            index = int(time.time() * 100)

            # Fetch batch
            batch_data = await self._fetch_historical_batch(
                asset, oldest_time, chunk_offset, period, index, timeout
            )
            if not batch_data:
                # Gap or error, jump back to try continuing
                oldest_time -= chunk_offset
                continue

            new_batch = self._parse_historical_candles(batch_data)
            if not new_batch:
                # Potential market closure gap
                oldest_time -= chunk_offset
                continue

            # Merge and find new boundary
            batch_times = []
            for c in new_batch:
                ts = c['time']
                all_candles[ts] = c
                batch_times.append(ts)

            batch_times.sort()
            new_oldest = batch_times[0]

            if new_oldest >= oldest_time:
                # No progress made (reached end of history or stuck)
                oldest_time -= chunk_offset
            else:
                oldest_time = new_oldest

                progress_callback(
                    current_time - oldest_time,
                    amount_of_seconds,
                    len(all_candles)
                )

            # Minimal throttle to respect API
            await asyncio.sleep(0.2)

        return sorted(all_candles.values(), key=lambda x: x['time'])

    async def get_candles_deep(
            self, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Deprecated alias for get_historical_candles."""
        logger.warning(
            "get_candles_deep is deprecated, "
            "use get_historical_candles instead."
        )
        return await self.get_historical_candles(*args, **kwargs)

    async def get_history_line(
            self,
            asset: str,
            end_from_time: float,
            offset: int,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any] | None:
        """Retrieves historical price line data for an asset."""
        if self.api is None:
            return None

        index = expiration.get_timestamp()
        self.api.current_asset = asset
        self.api.historical_candles = {}
        await self.start_candles_stream(asset)
        await self.api.get_history_line(
            self.codes_asset[asset], index, end_from_time, offset
        )
        start_time = time.time()
        while True:
            while (
                    await self.check_connect()
                    and self.api.historical_candles is None
            ):
                if time.time() - start_time > timeout:
                    logger.error(
                        "Timeout waiting for history line data for %s.",
                        asset
                    )
                    return None
                await asyncio.sleep(0.2)
            if self.api.historical_candles is not None:
                break
            if time.time() - start_time > timeout:
                logger.error(
                    "Timeout waiting for history line data for %s.",
                    asset
                )
                return None
        return self.api.historical_candles

    async def get_candle_v2(
            self, asset: str, period: int, timeout: int = DEFAULT_TIMEOUT
    ) -> list[dict[str, Any]] | None:
        """Retrieves candles using the v2 API path."""
        if self.api is None:
            return None

        self.api.candle_v2_data[asset] = None
        await self.start_candles_stream(asset, period)
        start_time = time.time()
        while self.api.candle_v2_data[asset] is None:
            logger.error(
                "Timeout waiting for get_candle_v2 data for %s.",
                asset
            )
            return None
        await asyncio.sleep(0.2)
        candles = self.prepare_candles(asset, period)
        return candles

    def prepare_candles(
            self,
            asset: str,
            period: int,
            history: list[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Prepare candles data for a specified asset."""
        if self.api is None:
            return []

        # Use provided history if available (from event response),
        # otherwise fallback to shared state
        history_data = (
            history if history is not None else self.api.candles.candles_data
        )
        candles_data = calculate_candles(history_data, period)
        candles_v2_data = process_candles_v2(
            self.api.candle_v2_data, asset, candles_data
        )
        new_candles = merge_candles(candles_v2_data)

        return new_candles

    async def connect(self) -> tuple[bool, str]:
        """Establishes a connection to the Quotex API."""
        if self.api and await self.check_connect():
            return True, "Already connected"
        self.api = QuotexAPI(
            self.host,
            self.email,
            self.password,
            self.lang,
            resource_path=self.resource_path,
            user_data_dir=self.user_data_dir,
            proxies=self.proxies,
            on_otp_callback=self.on_otp_callback
        )

        self.api.trace_ws = self.debug_ws_enable
        self.api.session_data = self.session_data
        self.api.current_asset = self.asset_default
        self.api.current_period = self.period_default
        self.api.state.SSID = self.session_data.get("token")

        if not self.session_data.get("token"):
            check, reason = await self.api.authenticate()
            if not check:
                return check, reason

        check, reason = await self.api.connect(self.account_is_demo == 1)
        if not await self.check_connect():
            logger.error(
                "Websocket failed to connect or connection was rejected."
            )
            self.session_data = {}
            return False, "Websocket connection rejected."

        return check, reason

    async def reconnect(self) -> None:
        """Attempts to re-authenticate and refresh the session."""
        if self.api:
            await self.api.authenticate()

    def set_account_mode(self, balance_mode: str = "PRACTICE") -> None:
        """Set active account `real` or `practice`"""
        if balance_mode.upper() == "REAL":
            self.account_is_demo = 0
        elif balance_mode.upper() == "PRACTICE":
            self.account_is_demo = 1
        else:
            raise ValueError(
                f"Invalid balance mode '{balance_mode}'. "
                "Use 'REAL' or 'PRACTICE'."
            )

    async def change_account(self, balance_mode: str) -> None:
        """Change active account `real` or `practice`"""
        self.account_is_demo = 0 if balance_mode.upper() == "REAL" else 1
        if self.api:
            await self.api.change_account(self.account_is_demo)

    async def change_time_offset(self, time_offset: int) -> Any:
        """Updates the timezone/time offset on the server."""
        if self.api:
            return await self.api.change_time_offset(time_offset)
        return None

    async def get_trader_history(
            self, account_type: int, page_number: int
    ) -> dict[str, Any]:
        """Retrieves trade history for a specific account and page."""
        if self.api:
            return await self.api.get_trader_history(account_type, page_number)
        return {}

    async def edit_practice_balance(
            self,
            amount: float | int | None = None,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Refills the demo account balance."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        self.api.training_balance_edit_request = None
        await self.api.edit_training_balance(
            amount if amount is not None else 0
        )
        start = time.time()
        while self.api.training_balance_edit_request is None:
            if time.time() - start > timeout:
                raise TimeoutError(
                    "Timeout waiting for practice balance edit response."
                )
            await asyncio.sleep(0.2)
        return self.api.training_balance_edit_request

    async def get_balance(self, timeout: int = DEFAULT_TIMEOUT) -> float:
        """Get account balance using a true event-driven approach."""
        if not self.api or not await self.check_connect():
            raise RuntimeError("Not connected to Quotex")

        if self.api.account_balance is not None:
            if self.api.account_type and self.api.account_type > 0:
                balance = self.api.account_balance.get("demoBalance", 0)
            else:
                balance = self.api.account_balance.get("liveBalance", 0)
            return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

        try:
            # Wait for WebSocket event signaling balance arrival
            await self.api.event_registry.wait_event(
                'balance_ready', timeout=timeout
            )
        except TimeoutError:
            logger.error(f"Timeout waiting for balance after {timeout}s")
            raise

        if self.api.account_balance is None:
            return 0.0

        if self.api.account_type and self.api.account_type > 0:
            balance = self.api.account_balance.get("demoBalance", 0)
        else:
            balance = self.api.account_balance.get("liveBalance", 0)
        return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

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

    async def get_profile(self) -> Any:
        """Retrieves and parses the user profile data."""
        if self.api:
            return await self.api.get_profile()
        return None

    async def get_server_time(self) -> int:
        """Retrieves and syncs the server time."""
        if self.api is None:
            return int(time.time())

        user_settings = await self.get_profile()
        offset_zone = user_settings.offset if user_settings else 0
        self.api.timesync.server_timestamp = (
            expiration.get_server_timer(offset_zone)
        )
        return self.api.timesync.server_timestamp

    async def get_history(self) -> list[dict[str, Any]]:
        """Get the trader's history based on account type."""
        if self.api is None:
            return []

        account_type = 1 if self.account_is_demo else 0
        return await self.api.get_trader_history(account_type, page=1)

    async def buy(
            self,
            amount: float,
            asset: str,
            direction: str,
            duration: int,
            time_mode: str = "TIME"
    ) -> tuple[bool, Any]:
        """Buy Binary option"""
        if self.api is None:
            return False, "API not initialized"

        self.api.buy_id = None
        self.api.buy_successful = None
        request_id = expiration.get_timestamp()
        is_fast_option = time_mode.upper() == "TIME"

        # Clear event state before requesting buy to prevent
        # race with WS response
        await self.api.event_registry.clear_event('buy_confirmed')

        # Ensure price data is arriving and server is synced
        await self.start_realtime_price(asset, duration)
        await self.get_server_time()
        await self.api.settings_apply(asset, duration, is_fast_option)

        await self.api.buy(
            amount, asset, direction, duration, request_id, is_fast_option
        )

        timeout = duration + 5 if duration else 30

        try:
            # Wait for WebSocket event signaling buy confirmation
            event_data = await self.api.event_registry.wait_event(
                'buy_confirmed', timeout=timeout
            )
        except TimeoutError as e:
            logger.error(str(e))
            return False, "Timeout"

        if self.api.state.check_websocket_if_error:
            return False, self.api.state.websocket_error_reason

        if (
                event_data
                and isinstance(event_data, dict)
                and "error" in event_data
        ):
            return False, event_data["error"]

        return True, event_data

    async def open_pending(
            self,
            amount: float,
            asset: str,
            direction: str,
            duration: int,
            open_time: str | None = None
    ) -> tuple[bool, Any]:
        """Places a pending order to be executed at a specific future time."""
        if self.api is None:
            return False, "API not initialized"

        self.api.pending_id = None
        user_settings = await self.get_profile()
        offset_zone = user_settings.offset if user_settings else 0
        open_time_int = expiration.get_next_timeframe(
            int(time.time()),
            offset_zone,
            duration,
            open_time
        )
        await self.api.open_pending(
            amount, asset, direction, duration, open_time_int
        )
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
            await self.api.instruments_follow(
                amount, asset, direction, duration, open_time_int
            )

        return status_buy, self.api.pending_successful

    async def sell_option(
            self,
            options_ids: list[str] | str,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Sells active options back to the broker before expiration."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        await self.api.sell_option(options_ids)
        self.api.sold_options_respond = None
        start = time.time()
        while self.api.sold_options_respond is None:
            if time.time() - start > timeout:
                raise TimeoutError("Timeout waiting for sell option response.")
            await asyncio.sleep(0.2)
        return self.api.sold_options_respond

    def get_payment(self) -> dict[str, Any]:
        """Retrieves the payout/payment percentages for all instruments."""
        if self.api is None:
            return {}

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

    def get_payout_by_asset(
            self, asset_name: str, timeframe: str = "1"
    ) -> float | dict[str, Any] | None:
        """Retrieves the payout percentage for a specific asset and
        timeframe."""
        if self.api is None:
            return None

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
        if data is None:
            return None

        if timeframe == "all":
            return data.get("profit")

        profit = data.get("profit")
        if profit:
            return profit.get(f"{timeframe}M")
        return None

    async def start_remaing_time(self) -> None:
        """Debug helper to log the remaining time until the next server
        expiration."""
        if self.api is None:
            return

        now_stamp = datetime.fromtimestamp(expiration.get_timestamp())
        expiration_stamp = datetime.fromtimestamp(
            self.api.timesync.server_timestamp
        )
        remaing_time = int((expiration_stamp - now_stamp).total_seconds())
        while remaing_time >= 0:
            remaing_time -= 1
            logger.debug("Remaining %d seconds...", max(remaing_time, 0))
            await asyncio.sleep(1)

    async def check_win(
            self, order_id: str | int, duration: int = 0
    ) -> tuple[str, float]:
        """Checks if a trade operation resulted in a win based on its ID."""
        if self.api is None:
            return "loss", 0.0

        start_time = time.time()
        while await self.check_connect():
            # Safety timeout after 5 minutes
            if time.time() - start_time > 300:
                break

            data_dict = self.api.listinfodata.get(order_id)
            if data_dict and data_dict.get("game_state") == 1:
                self.api.listinfodata.delete(order_id)
                win = data_dict.get("win", "loss")
                profit = float(data_dict.get("profit", 0))
                return win, profit
            await asyncio.sleep(0.2)

        return "loss", 0.0

    async def start_candles_stream(
            self, asset: str = "EURUSD", period: int = 0
    ) -> None:
        """Start streaming candle data for a specified asset."""
        if self.api:
            self.api.current_asset = asset
            await self.api.subscribe_realtime_candle(asset, period)
            await self.api.chart_notification(asset)
            await self.api.follow_candle(asset)

    async def store_settings_apply(
            self,
            asset: str = "EURUSD",
            period: int = 0,
            time_mode: str = "TIMER",
            deal: int = 5,
            percent_mode: bool = False,
            percent_deal: int = 1,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Applies trading settings and retrieves updated settings."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        is_fast_option = False if time_mode.upper() == "TIMER" else True
        self.api.current_asset = asset
        await self.api.settings_apply(
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
            if self.api.settings_list:
                investments_settings = self.api.settings_list
                break
            if time.time() - start > timeout:
                raise TimeoutError("Timeout waiting for settings response.")
            await asyncio.sleep(0.2)

        return investments_settings

    async def stop_candles_stream(self, asset: str) -> None:
        """Stops streaming candle data for a specified asset."""
        if self.api:
            await self.api.unsubscribe_realtime_candle(asset)
            await self.api.unfollow_candle(asset)

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

    def get_profit(self) -> float:
        """Retrieves the profit amount from the current active operation."""
        if self.api:
            return self.api.profit_in_operation or 0.0
        return 0.0

    async def get_result(self, operation_id: str) -> tuple[str | None, Any]:
        """Check if the trade is a win based on its ID."""
        data_history = await self.get_history()
        for item in data_history:
            if str(item.get("ticket")) == operation_id:
                profit = float(item.get("profitAmount", 0))
                status = "win" if profit > 0 else "loss"
                return status, item

        return None, "OperationID Not Found."

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
            try:
                await self.api.follow_candle(self.codes_asset[asset])
            except Exception as e:
                logger.error('**error** start_candles_stream reconnect: %s', e)
                await self.connect()
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

    async def close(self) -> bool:
        """Closes the API connection and stops all tasks."""
        if self.api:
            return await self.api.close()
        return True
