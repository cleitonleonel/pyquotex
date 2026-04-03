"""Module for Quotex websocket."""
import os
import time
import ssl
import json
import asyncio
from typing import Tuple
import certifi
import logging
import platform
import threading
import orjson
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
cacert = os.environ.get('WEBSOCKET_CLIENT_CA_BUNDLE')

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
        self.websocket_thread = None
        self.websocket_client = None
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

    @property
    def websocket(self):
        """Property to get websocket.

        :returns: The instance of :class:`WebSocket <websocket.WebSocket>`.
        """
        return self.websocket_client.wss

    def subscribe_realtime_candle(self, asset, period):
        self.realtime_price[asset] = []
        self.realtime_candles[asset] = {}
        payload = {
            "asset": asset,
            "period": period
        }
        data = f'42["instruments/update", {json.dumps(payload)}]'
        return self.send_websocket_request(data)

    def chart_notification(self, asset):
        payload = {
            "asset": asset,
            "version": "1.0.0"
        }
        data = f'42["chart_notification/get", {json.dumps(payload)}]'
        return self.send_websocket_request(data)

    def follow_candle(self, asset):
        data = f'42["depth/follow", {json.dumps(asset)}]'
        return self.send_websocket_request(data)

    def unfollow_candle(self, asset):
        data = f'42["depth/unfollow", {json.dumps(asset)}]'
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
        data = f'42["settings/store",{json.dumps(payload)}]'
        self.send_websocket_request(data)

    def unsubscribe_realtime_candle(self, asset):
        data = f'42["subfor", {json.dumps(asset)}]'
        return self.send_websocket_request(data)

    def edit_training_balance(self, amount):
        data = f'42["demo/refill",{json.dumps(amount)}]'
        self.send_websocket_request(data)

    def signals_subscribe(self):
        data = '42["signal/subscribe"]'
        self.send_websocket_request(data)

    def change_account(self, account_type):
        self.account_type = account_type
        payload = {
            "demo": self.account_type,
            "tournamentId": 0
        }
        data = f'42["account/change",{json.dumps(payload)}]'
        self.send_websocket_request(data)

    def get_history_line(self, asset_id, index, end_from_time, offset):
        payload = {
            "id": asset_id,
            "index": index,
            "time": end_from_time,
            "offset": offset,
        }
        data = f'42["history/load/line",{json.dumps(payload)}]'
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
        data = f'42["pending/create",{json.dumps(payload)}]'
        logger.debug(data)
        self.send_websocket_request(data)

    def instruments_follow(
            self,
            amount,
            asset,
            direction,
            duration,
            open_time
    ):
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
        data = f'42["instruments/follow",{json.dumps(payload)}]'
        self.send_websocket_request(data)

    def indicators(self):
        pass

    @property
    def logout(self):
        """Property for get Quotex http login resource.
        :returns: The instance of: class:`Login
            <pyquotex.http.login.Login>`.
        """
        return Logout(self)

    @property
    def login(self):
        """Property for get Quotex http login resource.
        :returns: The instance of: class:`Login
            <pyquotex.http.login.Login>`.
        """
        return Login(self)

    @property
    def ssid(self):
        """Property for get Quotex websocket ssid channel.
        :returns: The instance of :class:`Ssid
            <Quotex.ws.channels.ssid.Ssid>`.
        """
        return Ssid(self)

    @property
    def buy(self):
        """Property for get Quotex websocket ssid channel.
        :returns: The instance of :class:`Buy
            <Quotex.ws.channels.buy.Buy>`.
        """
        return Buy(self)

    @property
    def sell_option(self):
        return SellOption(self)

    @property
    def get_candles(self):
        """Property for get Quotex websocket candles channel.

        :returns: The instance of :class:`GetCandles
            <pyquotex.ws.channels.candles.GetCandles>`.
        """
        return GetCandles(self)

    @property
    def get_history(self):
        """Property for get Quotex http get history.

        :returns: The instance of: class:`GetHistory
            <pyquotex.http.history.GetHistory>`.
        """
        return GetHistory(self)

    def send_http_request_v1(
            self,
            resource,
            method,
            data=None,
            params=None,
            headers=None
    ):
        """Send http request to Quotex server.

        :param resource: The instance of
        :class:`Resource <pyquotex.http.resource.Resource>`.
        :param str method: The http request method.
        :param dict data: (optional) The http request data.
        :param dict params: (optional) The http request params.
        :param dict headers: (optional) The http request headers.
        :returns: The instance of: class:`Response <requests.Response>`.
        """
        import requests

        url = resource.url
        logger.debug(url)
        cookies = self.session_data.get("cookies")
        user_agent = self.session_data.get("user_agent")
        if cookies:
            self.browser.headers["Cookie"] = cookies
        if user_agent:
            self.browser.headers["User-Agent"] = user_agent
        self.browser.headers["Connection"] = "keep-alive"
        self.browser.headers["Accept-Encoding"] = "gzip, deflate, br"
        self.browser.headers["Accept-Language"] = "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3"
        self.browser.headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        )
        self.browser.headers["Referer"] = headers.get("referer")
        self.browser.headers["Upgrade-Insecure-Requests"] = "1"
        self.browser.headers["Sec-Ch-Ua"] = (
            '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
        )
        self.browser.headers["Sec-Ch-Ua-Mobile"] = "?0"
        self.browser.headers["Sec-Ch-Ua-Platform"] = '"Linux"'
        self.browser.headers["Sec-Fetch-Site"] = "same-origin"
        self.browser.headers["Sec-Fetch-User"] = "?1"
        self.browser.headers["Sec-Fetch-Dest"] = "document"
        self.browser.headers["Sec-Fetch-Mode"] = "navigate"
        self.browser.headers["Dnt"] = "1"
        response = self.browser.send_request(
            method=method,
            url=url,
            data=data,
            params=params,
            proxies=self.proxies
        )
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            return None
        return response

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

    def send_websocket_request(self, data, no_force_send=True):
        """Send websocket request to Quotex server.
        :param str data: The websocket requests data.
        :param bool no_force_send: Default None.
        """
        while (self.state.ssl_Mutual_exclusion
               or self.state.ssl_Mutual_exclusion_write) and no_force_send:
            pass
        self.state.ssl_Mutual_exclusion_write = True
        self.websocket.send(data)
        logger.debug(data)
        self.state.ssl_Mutual_exclusion_write = False

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
        payload = {
            "suppress_origin": True,
            "ping_interval": 24,
            "ping_timeout": 20,
            "ping_payload": "2",
            "origin": self.https_url,
            "host": f"ws2.{self.host}",
            "sslopt": {
                "check_hostname": True,
                "cert_reqs": ssl.CERT_REQUIRED,
                "ca_certs": cacert,
                "context": ssl_context,
            }
        }
        if platform.system() == "Linux":
            payload["sslopt"]["ssl_version"] = ssl.PROTOCOL_TLS
        self.websocket_thread = threading.Thread(
            target=self.websocket.run_forever, kwargs=payload
        )
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
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
        """Method for connection to Quotex API."""
        logger.info("Websocket Reconnection...")
        await self.start_websocket()

    async def close(self):
        if self.websocket_client:
            self.websocket.close()
            await asyncio.sleep(1)
            self.websocket_thread.join()
        return True

    def websocket_alive(self):
        return self.websocket_thread.is_alive()
