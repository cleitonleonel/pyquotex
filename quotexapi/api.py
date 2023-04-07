"""Module for Quotex websocket."""
import os
import time
import json
import ssl
import logging
import threading
import pathlib
import requests
import urllib3
import certifi
from quotexapi import global_value
from quotexapi.http.login import Login
from quotexapi.http.logout import Logout
from quotexapi.ws.channels.ssid import Ssid
from quotexapi.ws.channels.buy import Buy
from quotexapi.ws.channels.candles import GetCandles
from quotexapi.ws.channels.sell_option import SellOption
from quotexapi.ws.objects.timesync import TimeSync
from quotexapi.ws.objects.candles import Candles
from quotexapi.ws.objects.profile import Profile
from quotexapi.ws.objects.listinfodata import ListInfoData
from quotexapi.ws.client import WebsocketClient
from collections import defaultdict


def nested_dict(n, type):
    if n == 1:
        return defaultdict(type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, type))


urllib3.disable_warnings()
logger = logging.getLogger(__name__)

cert_path = certifi.where()
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['WEBSOCKET_CLIENT_CA_BUNDLE'] = cert_path
cacert = os.environ.get('WEBSOCKET_CLIENT_CA_BUNDLE')


class QuotexAPI(object):
    """Class for communication with Quotex API."""
    socket_option_opened = {}
    buy_id = None
    trace_ws = False
    buy_expiration = None
    current_asset = None
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

    def __init__(self, host, username, password, proxies=None):
        """
        :param str host: The hostname or ip address of a Quotex server.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param proxies: The proxies of a Quotex server.
        """
        self.username = username
        self.password = password
        self.cookies = None
        self.profile = None
        self.websocket_thread = None
        self.wss_url = f"wss://ws2.{host}/socket.io/?EIO=3&transport=websocket"
        self.websocket_client = None
        self.set_ssid = None
        self.user_agent = None
        self.token_login2fa = None
        self.proxies = proxies
        self.profile = Profile()

    @property
    def websocket(self):
        """Property to get websocket.

        :returns: The instance of :class:`WebSocket <websocket.WebSocket>`.
        """
        return self.websocket_client.wss

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
    def getcandles(self):
        """Property for get Quotex websocket candles channel.

        :returns: The instance of :class:`GetCandles
            <quotexapi.ws.channels.candles.GetCandles>`.
        """
        return GetCandles(self)

    def check_session(self):
        data = {}
        if os.path.isfile("./session.json"):
            with open("./session.json") as file:
                data = json.loads(file.read())
            self.user_agent = data.get("user_agent")
        return data.get("ssid"), data.get("cookies")

    def send_websocket_request(self, data, no_force_send=True):
        """Send websocket request to Quotex server.
        :param str data: The websocket request data.
        :param bool no_force_send: Default None.
        """

        logger = logging.getLogger(__name__)

        while (global_value.ssl_Mutual_exclusion or global_value.ssl_Mutual_exclusion_write) and no_force_send:
            pass
        global_value.ssl_Mutual_exclusion_write = True
        self.websocket.send('42["tick"]')
        self.websocket.send('42["indicator/list"]')
        self.websocket.send('42["drawing/load"]')
        self.websocket.send('42["pending/list"]')
        self.websocket.send('42["instruments/update",{"asset":"%s","period":60}]' % self.current_asset)
        self.websocket.send('42["chart_notification/get"]')
        self.websocket.send('42["depth/follow","%s"]' % self.current_asset)
        self.websocket.send(data)
        logger.debug(data)
        global_value.ssl_Mutual_exclusion_write = False

    def edit_training_balance(self, amount):
        data = f'42["demo/refill",{json.dumps(amount)}]'
        self.send_websocket_request(data)

    def get_ssid(self):
        ssid, cookies = self.check_session()
        if not ssid:
            # try:
            print("Autenticando usuário...")
            ssid, cookies = self.login(
                self.username,
                self.password
            )
            print("Login realizado com sucesso!!!")
            """except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(e)
                return e"""
        return ssid, cookies

    def start_websocket(self):
        global_value.check_websocket_if_connect = None
        global_value.check_websocket_if_error = False
        global_value.websocket_error_reason = None
        self.websocket_client = WebsocketClient(self)
        self.websocket_thread = threading.Thread(
            target=self.websocket.run_forever,
            kwargs={
                'ping_interval': 25,
                'ping_timeout': 20,
                'ping_payload': "2",
                'origin': 'https://qxbroker.com',
                'host': 'ws2.qxbroker.com',
                'sslopt': {
                    "check_hostname": False,
                    "cert_reqs": ssl.CERT_NONE,
                    "ca_certs": cacert,
                }
            }
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
        self.profile.msg = None
        self.ssid(global_value.SSID)
        while not self.profile.msg:
            time.sleep(0.1)
            pass
        if not self.profile.msg:
            return False
        return True

    def connect(self):
        """Method for connection to Quotex API."""
        global_value.ssl_Mutual_exclusion = False
        global_value.ssl_Mutual_exclusion_write = False
        if global_value.check_websocket_if_connect:
            self.close()
        ssid, self.cookies = self.get_ssid()
        check_websocket, websocket_reason = self.start_websocket()
        if not check_websocket:
            return check_websocket, websocket_reason
        else:
            if not global_value.SSID:
                global_value.SSID = ssid
        return check_websocket, websocket_reason

    def close(self):
        if self.websocket_client:
            self.websocket.close()
            self.websocket_thread.join()

    def websocket_alive(self):
        return self.websocket_thread.is_alive()
