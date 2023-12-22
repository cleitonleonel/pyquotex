"""Module for Quotex websocket."""
import os
import time
import json
import ssl
import urllib3
import certifi
import logging
import platform
import threading
from . import global_value
from .http.login import Login
from .http.logout import Logout
from .http.settings import Settings
from .ws.channels.ssid import Ssid
from .ws.channels.trade import Trade
from .ws.channels.candles import GetCandles
from .ws.channels.sell_option import SellOption
from .ws.objects.timesync import TimeSync
from .ws.objects.candles import Candles
from .ws.objects.profile import Profile
from .ws.objects.listinfodata import ListInfoData
from .ws.client import WebsocketClient
from collections import defaultdict

urllib3.disable_warnings()
logger = logging.getLogger(__name__)

cert_path = certifi.where()
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['WEBSOCKET_CLIENT_CA_BUNDLE'] = cert_path
cacert = os.environ.get('WEBSOCKET_CLIENT_CA_BUNDLE')


def nested_dict(n, type):
    if n == 1:
        return defaultdict(type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, type))


class QuotexAPI(object):
    """Class for communication with Quotex API."""
    socket_option_opened = {}
    trade_id = None
    trace_ws = False
    trade_expiration = None
    current_asset = None
    trade_successful = None
    account_balance = None
    account_type = None
    instruments = None
    training_balance_edit_request = None
    profit_in_operation = None
    sold_options_respond = None
    sold_digital_options_respond = None
    listinfodata = ListInfoData()
    timesync = TimeSync()
    candles = Candles()
    profile = Profile()

    def __init__(self,
                 host,
                 username,
                 password,
                 email_pass=None,
                 proxies=None,
                 resource_path=None,
                 user_data_dir=None):
        """
        :param str host: The hostname or ip address of a Quotex server.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str email_pass: The password of a Email.
        :param proxies: The proxies of a Quotex server.
        :param user_data_dir: The path browser user data dir.
        """
        self.wss_message = None
        self.websocket_thread = None
        self.websocket_client = None
        self.set_ssid = None
        self.object_id = None
        self.token_login2fa = None
        self._temp_status = ""
        self.username = username
        self.password = password
        self.email_pass = email_pass
        self.resource_path = resource_path
        self.user_data_dir = user_data_dir
        self.proxies = proxies
        self.wss_host = host
        self.settings_list = {}
        self.signal_data = {}
        self.get_candle_data = {}
        self.candle_v2_data = {}
        self.realtime_price = {}
        self.realtime_sentiment = {}
        self.session_data = {}

    @property
    def websocket(self):
        """Property to get websocket.

        :returns: The instance of :class:`WebSocket <websocket.WebSocket>`.
        """
        return self.websocket_client.wss

    def subscribe_realtime_candle(self, asset, period):
        self.realtime_price[asset] = []
        payload = {
            "asset": asset,
            "period": period
        }
        data = f'42["instruments/update", {json.dumps(payload)}]'
        return self.send_websocket_request(data)

    def follow_candle(self, asset):
        data = f'42["depth/follow", {json.dumps(asset)}]'
        return self.send_websocket_request(data)

    def unfollow_candle(self, asset):
        data = f'42["depth/unfollow", {json.dumps(asset)}]'
        return self.send_websocket_request(data)

    def unsubscribe_realtime_candle(self, asset):
        data = f'42["subfor", {json.dumps(asset)}]'
        return self.send_websocket_request(data)

    @property
    def logout(self):
        """Property for get Quotex http login resource.
        :returns: The instance of :class:`Login
            <quotexapi.http.login.Login>`.
        """
        return Logout(self)

    @property
    def login(self):
        """Property for get Quotex http login resource.
        :returns: The instance of :class:`Login
            <quotexapi.http.login.Login>`.
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
    def trade(self):
        """Property for get Quotex websocket ssid channel.
        :returns: The instance of :class:`Trade
            <Quotex.ws.channels.trade.Trade>`.
        """
        return Trade(self)

    @property
    def sell_option(self):
        return SellOption(self)

    @property
    def get_candles(self):
        """Property for get Quotex websocket candles channel.

        :returns: The instance of :class:`GetCandles
            <quotexapi.ws.channels.candles.GetCandles>`.
        """
        return GetCandles(self)

    async def get_profile(self):
        settings = Settings(self)
        user_settings = settings.get_settings()
        self.profile.nick_name = user_settings.get("data")["nickname"]
        self.profile.profile_id = user_settings.get("data")["id"]
        self.profile.demo_balance = user_settings.get("data")["demoBalance"]
        self.profile.live_balance = user_settings.get("data")["liveBalance"]
        self.profile.avatar = user_settings.get("data")["avatar"]
        self.profile.currency_code = user_settings.get("data")["currencyCode"]
        self.profile.country = user_settings.get("data")["country"]
        self.profile.country_name = user_settings.get("data")["countryName"]
        self.profile.currency_symbol = user_settings.get("data")["currencySymbol"]
        return self.profile

    async def check_session(self):
        if os.path.isfile(os.path.join(self.resource_path, "session.json")):
            with open(os.path.join(self.resource_path, "session.json")) as file:
                self.session_data = json.loads(file.read())

    def send_websocket_request(self, data, no_force_send=True):
        """Send websocket request to Quotex server.
        :param str data: The websocket request data.
        :param bool no_force_send: Default None.
        """
        while (global_value.ssl_Mutual_exclusion
               or global_value.ssl_Mutual_exclusion_write) and no_force_send:
            pass
        global_value.ssl_Mutual_exclusion_write = True
        logger.debug(data)
        self.websocket.send(data)
        global_value.ssl_Mutual_exclusion_write = False

    def edit_training_balance(self, amount):
        data = f'42["demo/refill",{json.dumps(amount)}]'
        self.send_websocket_request(data)

    def signals_subscribe(self):
        data = f'42["signal/subscribe"]'
        self.send_websocket_request(data)

    async def autenticate(self):
        await self.check_session()
        if not self.session_data.get("token"):
            print("Autenticando usuário...")
            await self.login(
                self.username,
                self.password,
                self.email_pass,
                self.user_data_dir
            )
            if self.session_data.get("token"):
                print("Login realizado com sucesso!!!")

    async def start_websocket(self, reconnect):
        if not reconnect:
            await self.autenticate()
        global_value.check_websocket_if_connect = None
        global_value.check_websocket_if_error = False
        global_value.websocket_error_reason = None
        self.websocket_client = WebsocketClient(self)
        payload = {
            "ping_interval": 25,
            "ping_timeout": 15,
            "ping_payload": "2",
            "origin": "https://qxbroker.com",
            "host": "ws2.qxbroker.com",
            "sslopt": {
                # "check_hostname": False,
                "cert_reqs": ssl.CERT_NONE,
                "ca_certs": cacert,
            }
        }
        if platform.system() == "Linux":
            payload["sslopt"]["ssl_version"] = ssl.PROTOCOL_TLSv1_2
        self.websocket_thread = threading.Thread(
            target=self.websocket.run_forever,
            kwargs=payload
        )
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        while True:
            try:
                if global_value.check_websocket_if_error:
                    return False, global_value.websocket_error_reason
                elif global_value.check_websocket_if_connect == 0:
                    logger.debug("Websocket conexão fechada.")
                    return False, "Websocket conexão fechada."
                elif global_value.check_websocket_if_connect == 1:
                    logger.debug("Websocket conectado com sucesso!!!")
                    return True, "Websocket conectado com sucesso!!!"
            except:
                pass
            pass

    def send_ssid(self):
        self.wss_message = None
        if not global_value.SSID:
            if os.path.exists(os.path.join(self.resource_path, "session.json")):
                os.remove(os.path.join(self.resource_path, "session.json"))
            return False
        self.ssid(global_value.SSID)
        while not self.wss_message:
            time.sleep(0.3)
        if not self.wss_message:
            return False
        return True

    async def connect(self, is_demo, reconnect=False):
        """Method for connection to Quotex API."""
        self.account_type = is_demo
        global_value.ssl_Mutual_exclusion = False
        global_value.ssl_Mutual_exclusion_write = False
        if global_value.check_websocket_if_connect:
            logger.info("Closing websocket connection...")
            self.close()
        check_websocket, websocket_reason = await self.start_websocket(reconnect)
        if not check_websocket:
            return check_websocket, websocket_reason
        else:
            if not global_value.SSID:
                global_value.SSID = self.session_data.get("token")
        return check_websocket, websocket_reason

    async def reconnect(self):
        """Method for connection to Quotex API."""
        logger.info("Websocket Reconnection...")
        await self.start_websocket(reconnect=True)

    def close(self):
        if self.websocket_client:
            self.websocket.close()
            self.websocket_thread.join()
        return True

    def websocket_alive(self):
        return self.websocket_thread.is_alive()
