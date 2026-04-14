"""Module for Quotex websocket."""
import os
import time
import ssl
import asyncio
from typing import Tuple
import certifi
import logging
import orjson
import httpx
from .global_value import ConnectionState
from .http.login import Login
from .http.logout import Logout
from .http.settings import Settings
from .http.history import GetHistory
from .http.navigator import Browser
from .ws.channels.ssid import Ssid
from .ws.channels.buy import Buy
from .ws.channels.candles import GetCandles
from .ws.channels.sell_option import SellOption
from .ws.objects.timesync import TimeSync
from .ws.objects.candles import Candles
from .ws.objects.profile import Profile
from .ws.objects.listinfodata import ListInfoData
from .ws.client import WebsocketClient
from .utils.async_utils import EventRegistry, FastJSONParser
from collections import defaultdict

logger = logging.getLogger(__name__)

cert_path = certifi.where()
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['WEBSOCKET_CLIENT_CA_BUNDLE'] = cert_path

ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2
ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
ssl_context.load_verify_locations(cert_path)


def nested_dict(n, type):
    if n == 1:
        return defaultdict(type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, type))


class QuotexAPI:
    """Class for communication with Quotex API."""

    def __init__(
            self,
            host,
            username,
            password,
            lang,
            proxies=None,
            resource_path=None,
            user_data_dir="."
    ):
        """
        :param str host: The hostname or ip address of a Quotex server.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str lang: The lang of a Quotex platform.
        :param proxies: The proxies of a Quotex server.
        :param user_data_dir: The path browser user data dir.
        """
        # -- per-instance connection state (replaces global_value) --
        self.state = ConnectionState()

        # -- per-instance mutable attributes (were class-level before) --
        self.socket_option_opened = {}
        self.buy_id = None
        self.pending_id = None
        self.trace_ws = False
        self.buy_expiration = None
        self.current_asset = None
        self.current_period = None
        self.buy_successful = None
        self.pending_successful = None
        self.account_balance = None
        self.account_type = None
        self.instruments = None
        self.training_balance_edit_request = None
        self.profit_in_operation = None
        self.sold_options_respond = None
        self.sold_digital_options_respond = None
        self.listinfodata = ListInfoData()
        self.timesync = TimeSync()
        self.candles = Candles()
        self.profile = Profile()

        self.host = host
        self.https_url = f"https://{host}"
        self.wss_url = f"wss://ws2.{host}/socket.io/?EIO=3&transport=websocket"
        self.wss_message = None
        self.websocket_client = None
        self._websocket_task = None
        self.set_ssid = None
        self.object_id = None
        self.token_login2fa = None
        self.is_logged = False
        self._temp_status = ""
        self.username = username
        self.password = password
        self.resource_path = resource_path
        self.user_data_dir = user_data_dir
        self.proxies = proxies
        self.lang = lang
        self.settings_list = {}
        self.signal_data = {}
        self.get_candle_data = {}
        self.historical_candles = {}
        self.candle_v2_data = {}
        self.realtime_price = {}
        self.realtime_price_data = []
        self.realtime_candles = {}
        self.realtime_sentiment = {}
        self.top_list_leader = {}
        self.session_data = {}
        self.browser = Browser()
        self.browser.set_headers()
        self.settings = Settings(self)
        # Event registry for optimized async operations
        self.event_registry = EventRegistry()
        # Persistent async HTTP client for connection pooling
        self._http_client = httpx.AsyncClient(
            verify=cert_path,
            timeout=30.0,
            follow_redirects=True,
        )
        self.profit_today = None

    # ------------------------------------------------------------------
    # WebSocket event handlers (moved from ws/client.py)
    # ------------------------------------------------------------------

    def _on_open(self):
        """Called when WebSocket connection is established."""
        logger.info("Websocket client connected.")
        self.state.check_websocket_if_connect = 1
        asset_name = self.current_asset
        period = self.current_period
        self.websocket.send('42["tick"]')
        self.websocket.send('42["indicator/list"]')
        self.websocket.send('42["drawing/load"]')
        self.websocket.send('42["pending/list"]')
        self.websocket.send('42["instruments/update",{"asset":"%s","period":%d}]' % (asset_name, period))
        self.websocket.send('42["depth/follow","%s"]' % asset_name)
        self.websocket.send('42["chart_notification/get"]')
        self.websocket.send('42["tick"]')

    def _on_message(self, msg):
        """Called for every WebSocket message received."""
        self.state.ssl_Mutual_exclusion = True
        current_time = time.localtime()
        if current_time.tm_sec in [0, 5, 10, 15, 20, 30, 40, 50]:
            self.websocket.send('42["tick"]')
        try:
            if "authorization/reject" in str(msg):
                logger.warning("Token rejected, making automatic reconnection.")
                self.state.check_rejected_connection = 1
            elif "s_authorization" in str(msg):
                self.state.check_accepted_connection = 1
                self.state.check_rejected_connection = 0
            elif "instruments/list" in str(msg):
                self.state.started_listen_instruments = True

            msg_str = msg.decode("utf-8", errors="ignore") if isinstance(msg, bytes) else str(msg)
            message = msg_str

            if len(msg_str) > 1:
                msg_parsed_str = msg_str[1:]
                logger.debug(msg_parsed_str)
                try:
                    message_json = orjson.loads(msg_parsed_str)
                    message = message_json
                    self.wss_message = message
                    if "call" in str(message) or 'put' in str(message):
                        self.instruments = message
                except (ValueError, TypeError):
                    pass
                if isinstance(message, dict):
                    if message.get("signals"):
                        time_in = message.get("time")
                        for i in message["signals"]:
                            try:
                                self.signal_data[i[0]] = {}
                                self.signal_data[i[0]][i[2]] = {}
                                self.signal_data[i[0]][i[2]]["dir"] = i[1][0]["signal"]
                                self.signal_data[i[0]][i[2]]["duration"] = i[1][0]["timeFrame"]
                            except (KeyError, IndexError, TypeError):
                                self.signal_data[i[0]] = {}
                                self.signal_data[i[0]][time_in] = {}
                                self.signal_data[i[0]][time_in]["dir"] = i[1][0][1]
                                self.signal_data[i[0]][time_in]["duration"] = i[1][0][0]
                    elif message.get("liveBalance") or message.get("demoBalance"):
                        self.account_balance = message
                    elif message.get("position"):
                        self.top_list_leader = message
                    elif len(message) == 1 and message.get("profit", -1) > -1:
                        self.profit_today = message
                    elif message.get("index"):
                        self.historical_candles = message
                        if message.get("closeTimestamp"):
                            self.timesync.server_timestamp = message.get("closeTimestamp")
                    if message.get("pending"):
                        self.pending_successful = message
                        self.pending_id = message["pending"]["ticket"]
                    elif message.get("id") and not message.get("ticket"):
                        self.buy_successful = message
                        self.buy_id = message["id"]
                        if message.get("closeTimestamp"):
                            self.timesync.server_timestamp = message.get("closeTimestamp")
                    elif message.get("ticket") and not message.get("id"):
                        self.sold_options_respond = message
                    elif message.get("deals"):
                        for get_m in message["deals"]:
                            self.profit_in_operation = get_m["profit"]
                            get_m["win"] = True if message["profit"] > 0 else False
                            get_m["game_state"] = 1
                            self.listinfodata.set(
                                get_m["win"],
                                get_m["game_state"],
                                get_m["id"]
                            )
                    elif message.get("isDemo") and message.get("balance"):
                        self.training_balance_edit_request = message
                    elif message.get("error"):
                        self.state.websocket_error_reason = message.get("error")
                        self.state.check_websocket_if_error = True
                        if self.state.websocket_error_reason == "not_money":
                            self.account_balance = {"liveBalance": 0}
                    elif not message.get("list") == []:
                        self.wss_message = message
            if str(message) == "41":
                logger.info("Disconnection event triggered by the platform, causing automatic reconnection.")
                self.state.check_websocket_if_connect = 0
            if "51-" in str(message):
                self._temp_status = str(message)
            elif self._temp_status == """451-["settings/list",{"_placeholder":true,"num":0}]""":
                self.settings_list = message
                self._temp_status = ""
            elif self._temp_status == """451-["history/list/v2",{"_placeholder":true,"num":0}]""":
                if message.get("asset") == self.current_asset:
                    self.candles.candles_data = message["history"]
                    self.candle_v2_data[message["asset"]] = message
                    self.candle_v2_data[message["asset"]]["candles"] = [{
                        "time": candle[0],
                        "open": candle[1],
                        "close": candle[2],
                        "high": candle[3],
                        "low": candle[4],
                        "ticks": candle[5]
                    } for candle in message["candles"]]
            elif isinstance(message, list) and len(message) > 0 and isinstance(message[0], list) and len(message[0]) == 4:
                result = {
                    "time": message[0][1],
                    "price": message[0][2]
                }
                self.realtime_price[message[0][0]].append(result)
                self.realtime_candles[self.current_asset] = message[0]
            elif isinstance(message, list) and len(message) > 0 and isinstance(message[0], list) and len(message[0]) == 2:
                for i in message:
                    result = {
                        "sentiment": {
                            "sell": 100 - int(i[1]),
                            "buy": int(i[1])
                        }
                    }
                    self.realtime_sentiment[i[0]] = result
        except Exception as e:
            logger.error("Unhandled error in _on_message: %s", e)
        self.state.ssl_Mutual_exclusion = False

    def _on_error(self, error):
        """Called on WebSocket error."""
        logger.error(error)
        self.state.websocket_error_reason = str(error)
        self.state.check_websocket_if_error = True

    def _on_close(self, close_status_code, close_msg):
        """Called when WebSocket connection closes."""
        logger.info("Websocket connection closed.")
        self.state.check_websocket_if_connect = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def websocket(self):
        """Property to get websocket client."""
        return self.websocket_client.wss

    # ------------------------------------------------------------------
    # WebSocket request helpers
    # ------------------------------------------------------------------

    def subscribe_realtime_candle(self, asset, period):
        self.realtime_price[asset] = []
        self.realtime_candles[asset] = {}
        payload = {"asset": asset, "period": period}
        data = f'42["instruments/update", {orjson.dumps(payload).decode()}]'
        return self.send_websocket_request(data)

    def chart_notification(self, asset):
        payload = {"asset": asset, "version": "1.0.0"}
        data = f'42["chart_notification/get", {orjson.dumps(payload).decode()}]'
        return self.send_websocket_request(data)

    def follow_candle(self, asset):
        data = f'42["depth/follow", {orjson.dumps(asset).decode()}]'
        return self.send_websocket_request(data)

    def unfollow_candle(self, asset):
        data = f'42["depth/unfollow", {orjson.dumps(asset).decode()}]'
        return self.send_websocket_request(data)

    def settings_apply(
            self,
            asset,
            duration,
            is_fast_option=False,
            end_time=None,
            deal=5,
            percent_mode=False,
            percent_deal=1
    ):
        payload = {
            "chartId": "graph",
            "settings": {
                "chartId": "graph",
                "chartType": 2,
                "currentExpirationTime": (
                    int(time.time()) if not is_fast_option else end_time
                ),
                "isFastOption": is_fast_option,
                "isFastAmountOption": percent_mode,
                "isIndicatorsMinimized": False,
                "isIndicatorsShowing": True,
                "isShortBetElement": False,
                "chartPeriod": 4,
                "currentAsset": {"symbol": asset},
                "dealValue": deal,
                "dealPercentValue": percent_deal,
                "isVisible": True,
                "timePeriod": duration,
                "gridOpacity": 8,
                "isAutoScrolling": 1,
                "isOneClickTrade": True,
                "upColor": "#0FAF59",
                "downColor": "#FF6251",
            },
        }
        data = f'42["settings/store",{orjson.dumps(payload).decode()}]'
        self.send_websocket_request(data)

    def unsubscribe_realtime_candle(self, asset):
        data = f'42["subfor", {orjson.dumps(asset).decode()}]'
        return self.send_websocket_request(data)

    def edit_training_balance(self, amount):
        data = f'42["demo/refill",{orjson.dumps(amount).decode()}]'
        self.send_websocket_request(data)

    def signals_subscribe(self):
        data = '42["signal/subscribe"]'
        self.send_websocket_request(data)

    def change_account(self, account_type):
        self.account_type = account_type
        payload = {"demo": self.account_type, "tournamentId": 0}
        data = f'42["account/change",{orjson.dumps(payload).decode()}]'
        self.send_websocket_request(data)

    def get_history_line(self, asset_id, index, end_from_time, offset):
        payload = {
            "id": asset_id,
            "index": index,
            "time": end_from_time,
            "offset": offset,
        }
        data = f'42["history/load/line",{orjson.dumps(payload).decode()}]'
        self.send_websocket_request(data)

    def open_pending(self, amount, asset, direction, duration, open_time):
        payload = {
            "openType": 0,
            "asset": asset,
            "openTime": open_time,
            "timeframe": duration,
            "command": direction,
            "amount": amount,
        }
        data = f'42["pending/create",{orjson.dumps(payload).decode()}]'
        logger.debug(data)
        self.send_websocket_request(data)

    def instruments_follow(self, amount, asset, direction, duration, open_time):
        payload = {
            "amount": amount,
            "command": 0 if direction == "call" else 1,
            "currency": self.profile.currency_code,
            "min_payout": 0,
            "open_time": open_time,
            "open_type": 0,
            "symbol": asset,
            "ticket": self.pending_id,
            "timeframe": duration,
            "uid": self.profile.profile_id,
        }
        data = f'42["instruments/follow",{orjson.dumps(payload).decode()}]'
        self.send_websocket_request(data)

    def indicators(self):
        pass

    # ------------------------------------------------------------------
    # HTTP resource properties
    # ------------------------------------------------------------------

    @property
    def logout(self):
        return Logout(self)

    @property
    def login(self):
        return Login(self)

    @property
    def ssid(self):
        return Ssid(self)

    @property
    def buy(self):
        return Buy(self)

    @property
    def sell_option(self):
        return SellOption(self)

    @property
    def get_candles(self):
        return GetCandles(self)

    @property
    def get_history(self):
        return GetHistory(self)

    # ------------------------------------------------------------------
    # HTTP request (async, httpx, connection-pooled)
    # ------------------------------------------------------------------

    async def send_http_request_v1(
            self,
            resource,
            method,
            data=None,
            params=None,
            headers=None
    ):
        """Send async http request to Quotex server using httpx.

        :param resource: The instance of
        :class:`Resource <pyquotex.http.resource.Resource>`.
        :param str method: The http request method.
        :param dict data: (optional) The http request data.
        :param dict params: (optional) The http request params.
        :param dict headers: (optional) The http request headers.
        :returns: The httpx.Response or None on HTTP error.
        """
        url = resource.url
        logger.debug(url)
        cookies_str = self.session_data.get("cookies")
        user_agent = self.session_data.get("user_agent")

        req_headers = dict(self.browser.headers)
        if cookies_str:
            req_headers["Cookie"] = cookies_str
        if user_agent:
            req_headers["User-Agent"] = user_agent
        req_headers["Connection"] = "keep-alive"
        req_headers["Accept-Encoding"] = "gzip, deflate, br"
        req_headers["Accept-Language"] = "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3"
        req_headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        )
        if headers:
            req_headers["Referer"] = headers.get("referer", "")
        req_headers["Upgrade-Insecure-Requests"] = "1"
        req_headers["Sec-Ch-Ua"] = (
            '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
        )
        req_headers["Sec-Ch-Ua-Mobile"] = "?0"
        req_headers["Sec-Ch-Ua-Platform"] = '"Linux"'
        req_headers["Sec-Fetch-Site"] = "same-origin"
        req_headers["Sec-Fetch-User"] = "?1"
        req_headers["Sec-Fetch-Dest"] = "document"
        req_headers["Sec-Fetch-Mode"] = "navigate"
        req_headers["Dnt"] = "1"

        try:
            response = await self._http_client.request(
                method=method,
                url=url,
                data=data,
                params=params,
                headers=req_headers,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError:
            return None
        except Exception as e:
            logger.error("HTTP request error: %s", e)
            return None

    # ------------------------------------------------------------------
    # Profile / account helpers
    # ------------------------------------------------------------------

    async def get_profile(self):
        user_settings = self.settings.get_settings()
        self.profile.nick_name = user_settings.get("data")["nickname"]
        self.profile.profile_id = user_settings.get("data")["id"]
        self.profile.demo_balance = float(
            user_settings.get("data").get("demoBalance", 0)
        )
        self.profile.live_balance = float(
            user_settings.get("data").get("liveBalance", 0)
        )
        self.profile.avatar = user_settings.get("data")["avatar"]
        self.profile.currency_code = user_settings.get("data")["currencyCode"]
        self.profile.country = user_settings.get("data")["country"]
        self.profile.country_name = user_settings.get("data")["countryName"]
        self.profile.currency_symbol = user_settings.get("data")["currencySymbol"]
        self.profile.offset = user_settings.get("data").get("timeOffset")
        return self.profile

    async def get_trader_history(self, account_type, page_number):
        history = await self.get_history(account_type, page_number)
        return history.get("data", {})

    def change_time_offset(self, time_offset):
        user_settings = self.settings.set_time_offset(time_offset)
        self.profile.offset = user_settings.get("data").get("timeOffset")
        return self.profile

    # ------------------------------------------------------------------
    # WebSocket send
    # ------------------------------------------------------------------

    def send_websocket_request(self, data, no_force_send=True):
        """Send websocket request to Quotex server."""
        while (self.state.ssl_Mutual_exclusion
               or self.state.ssl_Mutual_exclusion_write) and no_force_send:
            pass
        self.state.ssl_Mutual_exclusion_write = True
        self.websocket.send(data)
        logger.debug(data)
        self.state.ssl_Mutual_exclusion_write = False

    # ------------------------------------------------------------------
    # Authentication & connection lifecycle
    # ------------------------------------------------------------------

    async def authenticate(self) -> Tuple[bool, str]:
        logger.info("Connecting User Account ...")
        logger.debug("Login Account User...")
        async with self.login as login:
            status, msg = await login(
                self.username,
                self.password,
                self.user_data_dir
            )
            logger.info(msg)

        if status:
            self.state.SSID = self.session_data.get("token")
            self.is_logged = True

        return status, msg

    async def start_websocket(self):
        self.state.check_websocket_if_connect = None
        self.state.check_websocket_if_error = False
        self.state.websocket_error_reason = None
        if not self.state.SSID:
            await self.authenticate()
        self.websocket_client = WebsocketClient(self)

        extra_headers = {
            "User-Agent": self.session_data.get("user_agent", ""),
            "Origin": self.https_url,
            "Host": f"ws2.{self.host}",
            "Cookie": self.session_data.get("cookies", ""),
        }

        self._websocket_task = asyncio.create_task(
            self.websocket_client.run_forever(
                url=self.wss_url,
                extra_headers=extra_headers,
                ssl=ssl_context,
            )
        )

        while True:
            if self.state.check_websocket_if_error:
                return False, self.state.websocket_error_reason
            elif self.state.check_websocket_if_connect == 0:
                logger.debug("Websocket connection closed.")
                return False, "Websocket connection closed."
            elif self.state.check_websocket_if_connect == 1:
                logger.debug("Websocket connected successfully!!!")
                return True, "Websocket connected successfully!!!"
            elif self.state.check_rejected_connection == 1:
                self.state.SSID = None
                logger.debug("Websocket Token Rejected.")
                return True, "Websocket Token Rejected."

            await asyncio.sleep(0.1)

    async def send_ssid(self, timeout=10):
        self.wss_message = None
        if not self.state.SSID:
            return False

        self.ssid(self.state.SSID)
        start_time = time.time()

        while self.wss_message is None:
            if time.time() - start_time > timeout:
                return False
            await asyncio.sleep(0.5)

        return True

    async def connect(self, is_demo):
        """Method for connection to Quotex API."""
        self.account_type = is_demo
        self.state.ssl_Mutual_exclusion = False
        self.state.ssl_Mutual_exclusion_write = False
        if self.state.check_websocket_if_connect:
            logger.info("Closing websocket connection...")
            await self.close()

        check_websocket, websocket_reason = await self.start_websocket()

        if not check_websocket:
            return check_websocket, websocket_reason
        check_ssid = await self.send_ssid()

        if not check_ssid:
            await self.authenticate()
            if self.is_logged:
                await self.send_ssid()

        return check_websocket, websocket_reason

    async def reconnect(self):
        """Method for reconnection to Quotex API."""
        logger.info("Websocket Reconnection...")
        await self.start_websocket()

    async def close(self):
        if self.websocket_client:
            self.websocket_client.close()
            await asyncio.sleep(0.5)
        if self._websocket_task and not self._websocket_task.done():
            self._websocket_task.cancel()
            try:
                await self._websocket_task
            except asyncio.CancelledError:
                pass
        await self._http_client.aclose()
        return True

    def websocket_alive(self):
        return self.websocket_client.is_alive() if self.websocket_client else False
