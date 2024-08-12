"""Module for Quotex websocket."""
import os
import time
import json
import ssl
import urllib3
import requests
import certifi
import logging
import platform
import threading
from . import global_value
from .http.login import Login
from .http.logout import Logout
from .http.settings import Settings
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
from collections import defaultdict

urllib3.disable_warnings()
logger = logging.getLogger(__name__)

# cert_path = certifi.where()
cert_path = os.path.join("../", "quotex.pem")
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['WEBSOCKET_CLIENT_CA_BUNDLE'] = cert_path
cacert = os.environ.get('WEBSOCKET_CLIENT_CA_BUNDLE')

# Configuração do contexto SSL para usar TLS 1.3
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2  # Desativar versões TLS mais antigas
ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3  # Garantir o uso de TLS 1.3

ssl_context.load_verify_locations(certifi.where())


def nested_dict(n, type):
    if n == 1:
        return defaultdict(type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, type))


class QuotexAPI(object):
    """Class for communication with Quotex API."""
    socket_option_opened = {}
    buy_id = None
    trace_ws = False
    buy_expiration = None
    current_asset = None
    current_period = None
    buy_successful = None
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
                 lang,
                 email_pass=None,
                 proxies=None,
                 resource_path=None,
                 user_data_dir="."):
        """
        :param str host: The hostname or ip address of a Quotex server.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str lang: The lang of a Quotex platform.
        :param str email_pass: The password of a Email.
        :param proxies: The proxies of a Quotex server.
        :param user_data_dir: The path browser user data dir.
        """
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
        self.email_pass = email_pass
        self.resource_path = resource_path
        self.user_data_dir = user_data_dir
        self.proxies = proxies
        self.lang = lang
        self.settings_list = {}
        self.signal_data = {}
        self.get_candle_data = {}
        self.candle_v2_data = {}
        self.realtime_price = {}
        self.realtime_sentiment = {}
        self.session_data = {}
        self.browser = Browser()
        self.browser.set_headers()

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

    def edit_training_balance(self, amount):
        data = f'42["demo/refill",{json.dumps(amount)}]'
        self.send_websocket_request(data)

    def signals_subscribe(self):
        data = f'42["signal/subscribe"]'
        self.send_websocket_request(data)

    def change_account(self, account_type):
        self.account_type = account_type
        payload = {
            "demo": self.account_type,
            "tournamentId": 0
        }
        data = f'42["account/change",{json.dumps(payload)}]'
        self.send_websocket_request(data)

    def indicators(self):
        # 42["indicator/change",{"id":"Y5zYtYaUtjI6eUz06YlGF","settings":{"lines":{"main":{"lineWidth":1,"color":"#db4635"}},"ma":"SMA","period":10}}]
        # 42["indicator/delete", {"id": "23507dc2-05ca-4aec-9aef-55939735b3e0"}]
        pass

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
            <quotexapi.ws.channels.candles.GetCandles>`.
        """
        return GetCandles(self)

    def send_http_request_v1(self, resource, method, data=None, params=None, headers=None):
        """Send http request to Quotex server.

        :param resource: The instance of
        :class:`Resource <quotexapi.http.resource.Resource>`.
        :param str method: The http request method.
        :param dict data: (optional) The http request data.
        :param dict params: (optional) The http request params.
        :param dict headers: (optional) The http request headers.
        :returns: The instance of :class:`Response <requests.Response>`.
        """
        url = resource.url
        logger.debug(url)
        cookies = self.session_data.get('cookies')
        user_agent = self.session_data.get('user_agent')
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
        self.browser.headers["Referer"] = headers.get('referer')
        self.browser.headers["Upgrade-Insecure-Requests"] = "1"
        self.browser.headers["Sec-Ch-Ua"] = '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
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
            params=params
        )
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            return None
        return response

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

    def send_websocket_request(self, data, no_force_send=True):
        """Send websocket request to Quotex server.
        :param str data: The websocket request data.
        :param bool no_force_send: Default None.
        """
        while (global_value.ssl_Mutual_exclusion
               or global_value.ssl_Mutual_exclusion_write) and no_force_send:
            pass
        global_value.ssl_Mutual_exclusion_write = True
        self.websocket.send(data)
        logger.debug(data)
        global_value.ssl_Mutual_exclusion_write = False

    async def autenticate(self):
        print("Autenticando usuário...")
        response = await self.login(
            self.username,
            self.password,
            self.email_pass,
            self.user_data_dir
        )
        if response:
            global_value.SSID = self.session_data.get("token")
            self.is_logged = True
            print("Login realizado com sucesso!!!")
        return response

    async def start_websocket(self):
        global_value.check_websocket_if_connect = None
        global_value.check_websocket_if_error = False
        global_value.websocket_error_reason = None
        if not global_value.SSID:
            await self.autenticate()
        self.websocket_client = WebsocketClient(self)
        payload = {
            "ping_interval": 24,
            "ping_timeout": 20,
            "ping_payload": "2",
            "origin": self.https_url,
            "host": f"ws2.{self.host}",
            "sslopt": {
                "check_hostname": False,
                "cert_reqs": ssl.CERT_NONE,
                "ca_certs": cacert,
                "context": ssl_context
            }
        }
        if platform.system() == "Linux":
            payload["sslopt"]["ssl_version"] = ssl.PROTOCOL_TLS
        self.websocket_thread = threading.Thread(
            target=self.websocket.run_forever,
            kwargs=payload
        )
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        while True:
            if global_value.check_websocket_if_error:
                return False, global_value.websocket_error_reason
            elif global_value.check_websocket_if_connect == 0:
                logger.debug("Websocket conexão fechada.")
                return False, "Websocket conexão fechada."
            elif global_value.check_websocket_if_connect == 1:
                logger.debug("Websocket conectado com sucesso!!!")
                return True, "Websocket conectado com sucesso!!!"
            elif global_value.check_rejected_connection == 1:
                global_value.SSID = None
                logger.debug("Websocket Token Rejeitado.")
                return True, "Websocket Token Rejeitado."

    def send_ssid(self, timeout=10):
        self.wss_message = None
        if not global_value.SSID:
            return False
        self.ssid(global_value.SSID)
        start_time = time.time()
        while self.wss_message is None:
            if time.time() - start_time > timeout:
                return False
            time.sleep(0.5)
        return True

    async def connect(self, is_demo):
        """Method for connection to Quotex API."""
        self.account_type = is_demo
        global_value.ssl_Mutual_exclusion = False
        global_value.ssl_Mutual_exclusion_write = False
        if global_value.check_websocket_if_connect:
            logger.info("Closing websocket connection...")
            self.close()
        check_websocket, websocket_reason = await self.start_websocket()
        if not check_websocket:
            return check_websocket, websocket_reason
        check_ssid = self.send_ssid()
        if not check_ssid:
            await self.autenticate()
            if self.is_logged:
                self.send_ssid()
        return check_websocket, websocket_reason

    async def reconnect(self):
        """Method for connection to Quotex API."""
        logger.info("Websocket Reconnection...")
        await self.start_websocket()

    def close(self):
        if self.websocket_client:
            self.websocket.close()
            self.websocket_thread.join()
        return True

    def websocket_alive(self):
        return self.websocket_thread.is_alive()
