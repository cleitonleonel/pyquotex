"""Module for Quotex websocket."""
import asyncio
import logging
import os
import ssl
import time
from collections import defaultdict
from typing import Any, Callable

import certifi
import httpx
import orjson

from .global_value import ConnectionState
from .network.history import GetHistory
from .network.login import Login
from .network.logout import Logout
from .network.navigator import Browser
from .network.settings import Settings
from .utils.account_type import AccountType
from .utils.async_utils import EventRegistry
from .ws.channels.buy import Buy
from .ws.channels.candles import GetCandles
from .ws.channels.sell_option import SellOption
from .ws.channels.ssid import Ssid
from .ws.client import WebsocketClient
from .ws.objects.candles import Candles
from .ws.objects.listinfodata import ListInfoData
from .ws.objects.profile import Profile
from .ws.objects.timesync import TimeSync

logger = logging.getLogger(__name__)

cert_path = certifi.where()
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['WEBSOCKET_CLIENT_CA_BUNDLE'] = cert_path

ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
ssl_context.load_verify_locations(cert_path)

# Shared SSL context for httpx to avoid DeprecationWarning
ssl_verify = ssl.create_default_context(cafile=cert_path)


class QuotexAPI:
    """Class for communication with Quotex API."""

    def __init__(
            self,
            host: str,
            username: str,
            password: str,
            lang: str,
            proxies: dict[str, str] | None = None,
            resource_path: str | None = None,
            user_data_dir: str = ".",
            on_otp_callback: Callable | None = None
    ):
        """
        :param str host: The hostname or ip address of a Quotex server.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str lang: The lang of a Quotex platform.
        :param proxies: The proxies of a Quotex server.
        :param user_data_dir: The path browser user data dir.
        :param on_otp_callback: Callback function for OTP (2FA) input.
        """
        self.state = ConnectionState()
        self.on_otp_callback = on_otp_callback
        self._ws_send_lock = asyncio.Lock()

        self.socket_option_opened: dict[str, Any] = {}
        self.buy_id: str | int | None = None
        self.pending_id: str | int | None = None
        self.trace_ws: bool = False
        self.buy_expiration: int | None = None
        self.current_asset: str | None = None
        self.current_period: int | None = None
        self.buy_successful: bool | None = None
        self.pending_successful: bool | None = None
        self.account_balance: dict[str, Any] | None = None
        self.account_type: int | None = AccountType.DEMO
        self.tournament_id: int = 0
        self.instruments: list[Any] = []
        self.training_balance_edit_request: dict[str, Any] | None = None
        self.profit_in_operation: float | None = None
        self.sold_options_respond: Any = None
        self.sold_digital_options_respond: Any = None
        self.listinfodata = ListInfoData()
        self.timesync = TimeSync()
        self.candles = Candles()
        self.profile = Profile()

        self.host = host
        self.https_url = f"https://{host}"
        self.wss_url = f"wss://ws2.{host}/socket.io/?EIO=3&transport=websocket"
        self.wss_message: str | None = None
        self.websocket_client: WebsocketClient | None = None
        self._websocket_task: asyncio.Task | None = None
        self.set_ssid: Any = None
        self.object_id: Any = None
        self.token_login2fa: str | None = None
        self.is_logged: bool = False
        self._temp_status: str = ""
        self.username = username
        self.password = password
        self.resource_path = resource_path
        self.user_data_dir = user_data_dir
        self.proxies = proxies
        self.lang = lang
        self.settings_list: dict[str, Any] = {}
        self.signal_data: dict[str, Any] = {}
        self.get_candle_data: dict[str, Any] = {}
        self.historical_candles: dict[str, Any] = {}
        self.candle_v2_data: dict[str, Any] = {}
        self.realtime_price: dict[str, list[dict[str, Any]]] = (
            defaultdict(list)
        )
        self.realtime_price_data: list[Any] = []
        self.realtime_candles: dict[str, Any] = {}
        self.realtime_sentiment: dict[str, Any] = {}
        self.traders_mood: dict[str, Any] = {}
        self.candle_generated_check = defaultdict(lambda: defaultdict(dict))
        self.candle_generated_all_size_check = defaultdict(dict)
        self.top_list_leader: dict[str, Any] = {}
        self.session_data: dict[str, Any] = {}
        self.browser = Browser()
        self.browser.set_headers()
        self.settings = Settings(self)
        self.event_registry = EventRegistry()
        self._http_client = httpx.AsyncClient(
            verify=ssl_verify,
            timeout=30.0,
            follow_redirects=True,
        )
        self.profit_today: float | None = None

    async def _on_open(self) -> None:
        """Called when WebSocket connection is established."""
        logger.info("Websocket client connected.")
        self.state.check_websocket_if_connect = 1

        # Start Heartbeat task to keep connection alive and stream active
        async def heartbeat() -> None:
            while self.state.check_websocket_if_connect == 1:
                try:
                    await self.websocket.send('42["tick"]')
                except Exception:
                    break
                # Send every 5 seconds as in legacy version
                await asyncio.sleep(5)

        asyncio.create_task(heartbeat())

        await self.websocket.send('42["indicator/list"]')
        await self.websocket.send('42["drawing/load"]')
        await self.websocket.send('42["pending/list"]')
        await self.websocket.send('42["chart_notification/get"]')
        await self.websocket.send('42["instruments/get"]')

    async def _on_message(self, msg: bytes | str) -> None:
        """Called for every WebSocket message received."""
        try:
            message: Any = None
            msg_str = (
                msg.decode("utf-8", errors="ignore")
                if isinstance(msg, bytes)
                else str(msg)
            )

            if "authorization/reject" in msg_str:
                self.state.websocket_error_reason = (
                    "Websocket connection rejected."
                )
                self.state.check_websocket_if_error = True
                return
            elif "s_authorization" in msg_str:
                self.state.check_accepted_connection = 1
                self.state.check_websocket_if_connect = 1

            # Detect Socket.IO prefix
            is_control = msg_str and msg_str[0].isdigit()

            # Clean JSON extraction
            try:
                # Find start of JSON
                start_idx = -1
                for idx, char in enumerate(msg_str):
                    if char in ('[', '{'):
                        start_idx = idx
                        break

                if start_idx != -1:
                    clean_json = msg_str[start_idx:]
                    data_json = orjson.loads(clean_json)
                    message = data_json
                    data = (
                        data_json[0]
                        if (
                                isinstance(data_json, list)
                                and len(data_json) == 1
                        )
                        else data_json
                    )

                    pass
                else:
                    pass
            except Exception as e:
                logger.debug("Failed to parse raw data payload: %s", e)

            # 1. Handle Control Messages (Placeholders)
            if is_control:
                if "51-" in msg_str and "_placeholder" in msg_str:
                    self._temp_status = msg_str
                    return

                # Standard Event Processing
                if (
                        isinstance(message, list)
                        and len(message) > 1
                        and isinstance(message[0], str)
                ):
                    event = message[0]
                    data = message[1]

                    if event == "s_authorization":
                        self.state.check_accepted_connection = True
                    elif event == "instruments/list":
                        if isinstance(data, dict) and data.get("_placeholder"):
                            self._temp_status = (
                                '451-["instruments/list",'
                                f'{orjson.dumps(data).decode()}]'
                            )
                        else:
                            self.instruments = data
                            await self.event_registry.set_event(
                                'instruments_ready', data
                            )
                    elif event == "trader/history":
                        await self.event_registry.set_event(
                            'history_ready', data
                        )
                    elif event == "balance":
                        self.account_balance = data
                        await self.event_registry.set_event(
                            'balance_ready', data
                        )
                    elif event == "candle-generated":
                        asset = data.get("asset")
                        period = data.get("period")
                        if asset and period:
                            self.candle_generated_check[str(asset)][
                                int(period)
                            ] = data
                            self.candle_generated_all_size_check[
                                str(asset)
                            ] = data
                    elif event == "sentiment":
                        asset = data.get("asset")
                        if asset:
                            self.traders_mood[asset] = data
                            self.realtime_sentiment[asset] = data

            # 2. Handle Data Payloads (Placeholder fulfillment)
            elif self._temp_status and message is not None:
                data = (
                    message[0]
                    if isinstance(message, list) and len(message) == 1
                    else message
                )

                if 'instruments/list' in self._temp_status:
                    if isinstance(data, list):
                        self.instruments = data
                    elif isinstance(data, dict) and "list" in data:
                        self.instruments = data["list"]

                    if self.instruments:
                        await self.event_registry.set_event(
                            'instruments_ready', self.instruments
                        )

                elif any(
                        x in self._temp_status
                        for x in ['history/list/v2', 'history/load']
                ):
                    if isinstance(data, dict) and data.get("asset"):
                        asset = data["asset"]
                        self.candle_v2_data[asset] = data
                        await self.event_registry.set_event(
                            f'candles_ready_{asset}', data
                        )
                        if data.get("index"):
                            await self.event_registry.set_event(
                                f'candles_ready_{asset}_{data["index"]}',
                                data
                            )
                    elif isinstance(data, list):
                        # Fallback for old history format if needed
                        await self.event_registry.set_event(
                            'history_ready', data
                        )

                elif any(
                        x in self._temp_status
                        for x in [
                            'orders/open', 'orders/close', 'orders/opened',
                            'pending/create', 'pending/opened'
                        ]
                ):
                    logger.debug(
                        "Order event via placeholder! status=%s",
                        self._temp_status
                    )

                    # Handle both single dict and list of dicts
                    orders_to_process = []
                    if isinstance(data, list):
                        orders_to_process = data
                    elif isinstance(data, dict):
                        if data.get("deals"):
                            orders_to_process = data["deals"]
                        else:
                            orders_to_process = [data]

                    for order in orders_to_process:
                        order_id = order.get("id")
                        if order_id:
                            profit = order.get("profit", 0)
                            win = "win" if profit > 0 else "loss"
                            # Check if it's in a closed list or has a 
                            # close status
                            is_closed = (
                                    any(
                                        x in self._temp_status
                                        for x in ['closed', 'close']
                                    )
                                    or order.get("status") == "closed"
                            )
                            game_state = 1 if is_closed else 0

                            logger.debug(
                                "Processing order %s: win=%s, state=%s, "
                                "profit=%s",
                                order_id, win, game_state, profit
                            )
                            self.listinfodata.set(
                                win, game_state, order_id, profit
                            )
                            self.listinfodata.set(
                                win, game_state, str(order_id), profit
                            )

                    # Always set buy_confirmed if it was an open request
                    if (
                            any(x in self._temp_status for x in ['orders/open', 'pending/create'])
                            and isinstance(data, dict)
                    ):
                        if 'pending' in self._temp_status:
                            self.pending_id = data.get("id")
                            self.pending_successful = True
                            await self.event_registry.set_event(
                                'pending_confirmed', data
                            )
                        else:
                            self.buy_id = data.get("id")
                            self.buy_successful = True
                            await self.event_registry.set_event(
                                'buy_confirmed', data
                            )

                self._temp_status = ""  # Clear after consuming data

            # 3. Handle Real-time and Profile Dicts
            if isinstance(message, dict):
                if message.get("liveBalance") or message.get("demoBalance"):
                    self.account_balance = message
                    await self.event_registry.set_event(
                        'balance_ready', message
                    )
                elif message.get("deals"):
                    # Handle real-time deals update (usually closed deals)
                    for order in message["deals"]:
                        order_id = order.get("id")
                        if order_id:
                            profit = order.get("profit", 0)
                            win = "win" if profit > 0 else "loss"
                            logger.debug(
                                "Real-time deal update for %s: "
                                "win=%s, profit=%s",
                                order_id, win, profit
                            )
                            self.listinfodata.set(win, 1, order_id, profit)
                            self.listinfodata.set(
                                win, 1, str(order_id), profit
                            )
                    await self.event_registry.set_event(
                        'history_ready', message
                    )
                elif (
                        "id" in message
                        and ("asset" in message or "amount" in message)
                ):
                    # Potential order confirmation
                    self.buy_id = message.get("id")
                    await self.event_registry.set_event(
                        'buy_confirmed', message
                    )

            elif (
                    isinstance(message, list)
                    and len(message) > 1
                    and message[0] == "order"
            ):
                # Explicit order event
                data = message[1]
                order_id = data.get("id")
                self.buy_id = order_id

                # Update listinfodata for check_win
                if "profit" in data and "status" in data:
                    profit = data.get("profit", 0)
                    win = "win" if profit > 0 else "loss"
                    game_state = 1 if data.get("status") == "closed" else 0
                    self.listinfodata.set(
                        win, game_state, str(order_id), profit
                    )

                await self.event_registry.set_event('buy_confirmed', data)
                await self.event_registry.set_event(
                    f'order_closed_{order_id}', data
                )

            elif (
                    isinstance(message, list)
                    and len(message) > 0
                    and isinstance(message[0], list)
            ):
                if len(message[0]) == 4:  # Price
                    asset, ts, price = (
                        message[0][0], message[0][1], message[0][2]
                    )
                    self.timesync.server_timestamp = ts  # Sync server clock

                    # Limit realtime_price history to 1000 entries 
                    # to prevent memory bloat
                    price_list = self.realtime_price[asset]
                    price_list.append({"time": ts, "price": price})
                    if len(price_list) > 1000:
                        price_list.pop(0)

                    self.realtime_candles[asset] = message[0]

        except Exception as e:
            logger.error("Error in _on_message: %s", e)

    def _on_error(self, error: Exception | str) -> None:
        """
        Handles WebSocket errors.

        Args:
            error (Exception): The error that occurred.
        """
        logger.error(error)
        self.state.websocket_error_reason = str(error)
        self.state.check_websocket_if_error = True

    def _on_close(self, code: int, msg: str) -> None:
        """
        Handles WebSocket connection closure.

        Args:
            code (int): The closure code.
            msg (str): The closure message.
        """
        logger.info("Websocket connection closed.")
        self.state.check_websocket_if_connect = 0

    @property
    def websocket(self) -> Any:
        """
        Returns the active WebSocket instance.

        Returns:
            websockets.WebSocketClientProtocol: The active WebSocket
                connection or None.
        """
        return self.websocket_client.wss if self.websocket_client else None

    async def get_instruments(self) -> None:
        """Sends a request to the WebSocket to retrieve the list of
        available instruments."""
        if self.websocket:
            await self.websocket.send('42["instruments/get"]')

    async def authenticate(self) -> tuple[bool, str]:
        """
        Authenticates the user using the provided credentials.

        Performs HTTP login, retrieves cookies and SSID token, 
        and updates the browser session.

        Returns:
            tuple[bool, str]: (Success status, Error message or "Success").
        """
        async with self.login as login:
            status, msg = await login(
                self.username, self.password, self.user_data_dir
            )
        if status:
            self.state.SSID = self.session_data.get("token")
            self.is_logged = True
            # Sync session to browser client
            if "cookies" in self.session_data:
                cookie_str = self.session_data["cookies"]
                for item in cookie_str.split("; "):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        self.browser._client.cookies.set(
                            k, v, domain=self.host
                        )

            self.browser.headers.update({
                "User-Agent": self.session_data.get("user_agent", ""),
                "Referer": f"{self.https_url}/{self.lang}/trade"
            })
        return status, msg

    async def send_http_request_v1(
            self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Sends an HTTP request using the internal browser client (v1)."""
        # Browser.send_request uses self._client.request internally
        return await self.browser.send_request(method, url, **kwargs)

    async def send_http_request_v2(
            self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Sends an HTTP request using the internal browser client (v2)."""
        return await self.browser.send_request(method, url, **kwargs)

    async def send_websocket_request(self, data: str) -> None:
        """
        Sends a raw string request to the WebSocket.
        Uses a lock to ensure thread-safe sending.

        Args:
            data (str): The raw Socket.IO string to send.
        """
        async with self._ws_send_lock:
            if self.websocket:
                await self.websocket.send(data)

    async def check_connect(self) -> bool:
        """Checks if the WebSocket is currently connected."""
        return self.state.check_websocket_if_connect == 1

    async def settings_apply(
            self,
            asset: str,
            expiration: int,
            is_fast_option: bool = False,
            end_time: int | None = None
    ) -> None:
        """Apply asset and time settings before placing an order."""
        payload = {
            "asset": asset,
            "time": expiration,
            "isFastOption": is_fast_option,
        }
        if end_time:
            payload["endTime"] = end_time

        data = f'42["settings/apply", {orjson.dumps(payload).decode()}]'
        await self.send_websocket_request(data)

    async def subscribe_realtime_candle(self, asset: str, period: int) -> None:
        """Subscribes to real-time price updates for a specific asset
        and period."""
        payload = {"asset": asset, "period": period}
        data = f'42["instruments/update", {orjson.dumps(payload).decode()}]'
        await self.send_websocket_request(data)

    async def chart_notification(self, asset: str) -> None:
        """Requests chart notifications for a specific asset."""
        payload = {"asset": asset, "version": "1.0.0"}
        payload_json = orjson.dumps(payload).decode()
        data = f'42["chart_notification/get", {payload_json}]'
        await self.send_websocket_request(data)

    async def follow_candle(self, asset: str) -> None:
        """Starts following the depth of market for a specific asset."""
        data = f'42["depth/follow", {orjson.dumps(asset).decode()}]'
        await self.send_websocket_request(data)

    async def unfollow_candle(self, asset: str) -> None:
        """Stops following the depth of the market for a specific asset."""
        data = f'42["depth/unfollow", {orjson.dumps(asset).decode()}]'
        await self.send_websocket_request(data)

    async def signals_subscribe(self) -> None:
        """Subscribes to real-time trading signals from the platform."""
        await self.send_websocket_request('42["signal/subscribe"]')

    async def change_account(
            self,
            account_type: AccountType,
            tournament_id: int = 0
    ) -> None:
        """
        Change active trading account.

        Args:
            account_type:
                REAL or DEMO account.

            tournament_id:
                Tournament/training id.
                Default 0 disables tournament mode.
        """

        self.account_type = account_type
        self.tournament_id = tournament_id

        payload = {
            "demo": int(account_type),
            "tournamentId": tournament_id
        }

        data = f'42["account/change",{orjson.dumps(payload).decode()}]'

        await self.send_websocket_request(data)

    async def edit_training_balance(self, amount: float | int) -> None:
        """Refills the demo account balance."""
        data = f'42["demo/refill",{orjson.dumps(amount).decode()}]'
        await self.send_websocket_request(data)

    async def change_time_offset(self, time_offset: int) -> dict[str, Any]:
        """Changes the account time offset."""
        return await self.settings.set_time_offset(time_offset)

    async def unsubscribe_realtime_candle(self, asset: str) -> None:
        """Unsubscribes from real-time price updates for a specific asset."""
        payload = {"asset": asset}
        data = f'42["instruments/unsubscribe", {orjson.dumps(payload).decode()}]'
        await self.send_websocket_request(data)

    async def subscribe_Traders_mood(self, asset: str, instrument: str) -> None:
        """Subscribes to traders' mood/sentiment for a specific asset."""
        payload = {"asset": asset, "instrument": instrument}
        data = f'42["sentiment/subscribe", {orjson.dumps(payload).decode()}]'
        await self.send_websocket_request(data)

    async def subscribe_all_size(self, asset: str) -> None:
        """Subscribes to all candle sizes for a specific asset."""
        payload = {"asset": asset}
        data = f'42["history/subscribe_all", {orjson.dumps(payload).decode()}]'
        await self.send_websocket_request(data)

    async def get_history_line(
            self,
            asset: str,
            index: int,
            time_from: float,
            offset: int
    ) -> None:
        """Requests historical price line data."""
        payload = {
            "asset": asset,
            "index": index,
            "time": time_from,
            "offset": offset
        }
        data = f'42["history/load", {orjson.dumps(payload).decode()}]'
        await self.send_websocket_request(data)

    async def open_pending(
            self,
            amount: float | int,
            asset: str,
            direction: str,
            duration: int,
            open_time: int
    ) -> None:
        """Places a pending order to be executed at a specific future time."""
        payload = {
            "asset": asset,
            "amount": amount,
            "action": direction,
            "time": duration,
            "openTime": open_time,
            "isDemo": int(self.account_type) if self.account_type is not None else AccountType.DEMO,
            "tournamentId": self.tournament_id,
            "requestId": int(time.time())
        }
        data = f'42["pending/create", {orjson.dumps(payload).decode()}]'
        await self.send_websocket_request(data)

    async def instruments_follow(
            self,
            amount: float | int,
            asset: str,
            direction: str,
            duration: int,
            open_time: int
    ) -> None:
        """Alias for open_pending or similar follow request."""
        await self.open_pending(amount, asset, direction, duration, open_time)

    async def start_websocket(self) -> tuple[bool, str]:
        """
        Initializes and starts the WebSocket connection.
        Attempts to authenticate if no SSID is present.

        Returns:
            tuple[bool, str]: (Success status, Connection status message).
        """
        self.state.check_websocket_if_connect = None
        self.state.check_websocket_if_error = False
        if not self.state.SSID:
            await self.authenticate()

        self.websocket_client = WebsocketClient(self)
        extra_headers = {
            "User-Agent": self.session_data.get("user_agent", ""),
            "Origin": self.https_url,
            "Cookie": self.session_data.get("cookies", ""),
        }
        self._websocket_task = asyncio.create_task(
            self.websocket_client.run_forever(
                url=self.wss_url, extra_headers=extra_headers, ssl=ssl_context
            )
        )
        for _ in range(100):
            if self.state.check_websocket_if_error:
                return False, self.state.websocket_error_reason
            if self.state.check_websocket_if_connect == 1:
                return True, "Connected"
            await asyncio.sleep(0.1)
        return False, "Timeout"

    async def send_ssid(self) -> bool:
        """Sends the SSID token to the WebSocket to authorize the
        connection."""
        if not self.state.SSID: return False
        await self.ssid(self.state.SSID)
        return True

    async def connect(self, is_demo: bool) -> tuple[bool, str]:
        """
        Connects to the Quotex platform.

        Args:
            is_demo (bool): True to connect to a DEMO account, False for REAL.

        Returns:
            tuple[bool, str]: (Connection success, Status message).
        """
        self.account_type = (
            AccountType.DEMO if is_demo else AccountType.REAL
        )
        ok, reason = await self.start_websocket()
        if ok: await self.send_ssid()
        return ok, reason

    async def close(self) -> bool:
        """Closes the WebSocket connection and the HTTP client session."""
        if self.websocket_client: await self.websocket_client.close()
        if self._http_client: await self._http_client.aclose()
        return True

    @property
    def logout(self) -> Logout:
        """Returns the Logout action handler."""
        return Logout(self)

    @property
    def login(self) -> Login:
        """Returns the Login action handler."""
        return Login(self)

    @property
    def ssid(self) -> Ssid:
        """Returns the SSID authorization handler."""
        return Ssid(self)

    @property
    def buy(self) -> Buy:
        """Returns the Buy order handler."""
        return Buy(self)

    @property
    def sell_option(self) -> SellOption:
        """Returns the Sell Option handler."""
        return SellOption(self)

    @property
    def get_candles(self) -> GetCandles:
        """Returns the Candles retrieval handler."""
        return GetCandles(self)

    @property
    def get_history(self) -> GetHistory:
        """Returns the Trade History retrieval handler."""
        return GetHistory(self)

    async def get_profile(self) -> Profile:
        """
        Retrieves and parses the user profile data.

        Updates the internal profile object with nickname, balances, 
        country, and timezone.

        Returns:
            Profile: The updated profile object.
        """
        user_settings = await self.settings.get_settings()
        d = user_settings.get("data", {})
        self.profile.nick_name = d.get("nickname")
        self.profile.profile_id = d.get("id")
        self.profile.demo_balance = float(d.get("demoBalance", 0))
        self.profile.live_balance = float(d.get("liveBalance", 0))
        self.profile.currency_code = d.get("currencyCode")
        self.profile.currency_symbol = d.get("currencySymbol")
        self.profile.country_name = d.get("countryName")
        self.profile.offset = d.get("timeOffset")
        return self.profile

    async def get_trader_history(
            self, account_type: int, page: int
    ) -> dict[str, Any]:
        """
        Retrieves the trade history for a specific account and page.

        Args:
            account_type (int): AccountType.REAL or AccountType.DEMO.
            page (int): Page number to retrieve.

        Returns:
            dict: The trade history data.
        """
        history = await self.get_history(account_type, page)
        return history.get("data", {})
