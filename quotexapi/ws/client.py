"""Module for Quotex websocket."""
import os
import json
import time
import asyncio
import logging
import websocket
from .. import global_value

logger = logging.getLogger(__name__)


class WebsocketClient(object):
    """Class for work with Quotex API websocket."""

    def __init__(self, api):
        """
        :param api: The instance of :class:`QuotexAPI
            <quotexapi.api.QuotexAPI>`.
        trace_ws: Enables and disable `enableTrace` in WebSocket Client.
        """
        self.api = api
        self.headers = {
            "User-Agent": self.api.session_data.get("user_agent"),
        }
        websocket.enableTrace(self.api.trace_ws)
        self.wss = websocket.WebSocketApp(
            f"wss://ws2.{self.api.wss_host}/socket.io/?EIO=3&transport=websocket",
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
            on_ping=self.on_ping,
            on_pong=self.on_pong,
            header=self.headers,
            cookie=self.api.session_data.get("cookies")
        )

    def on_message(self, wss, message):
        """Method to process websocket messages."""
        global_value.ssl_Mutual_exclusion = True
        current_time = time.localtime()
        if current_time.tm_sec in [0, 20, 40]:
            self.wss.send('42["tick"]')
        try:
            if "authorization/reject" in str(message):
                if os.path.isfile(os.path.join(self.api.resource_path, "session.json")):
                    os.remove(os.path.join(self.api.resource_path, "session.json"))
                global_value.SSID = None
                global_value.check_rejected_connection = 1
            elif "s_authorization" in str(message):
                global_value.check_accepted_connection = 1
            elif "instruments/list" in str(message):
                global_value.started_listen_instruments = True
            try:
                message = message[1:].decode()
                logger.debug(message)
                message = json.loads(message)
                self.api.wss_message = message
                if "call" in str(message) or 'put' in str(message):
                    self.api.instruments = message
                if message.get("signals"):
                    time_in = message.get("time")
                    for i in message["signals"]:
                        try:
                            self.api.signal_data[i[0]] = {}
                            self.api.signal_data[i[0]][i[2]] = {}
                            self.api.signal_data[i[0]][i[2]]["dir"] = i[1][0]["signal"]
                            self.api.signal_data[i[0]][i[2]]["duration"] = i[1][0]["timeFrame"]
                        except:
                            self.api.signal_data[i[0]] = {}
                            self.api.signal_data[i[0]][time_in] = {}
                            self.api.signal_data[i[0]][time_in]["dir"] = i[1][0][1]
                            self.api.signal_data[i[0]][time_in]["duration"] = i[1][0][0]
                elif message.get("liveBalance") or message.get("demoBalance"):
                    self.api.account_balance = message
                elif message.get("index"):
                    self.api.candles.candles_data = message
                elif message.get("id"):
                    self.api.trade_successful = message
                    self.api.trade_id = message["id"]
                    self.api.timesync.server_timestamp = message["closeTimestamp"]
                elif message.get("ticket"):
                    self.api.sold_options_respond = message
                elif message.get("deals"):
                    for get_m in message["deals"]:
                        self.api.profit_in_operation = get_m["profit"]
                        get_m["win"] = True if message["profit"] > 0 else False
                        get_m["game_state"] = 1
                        self.api.listinfodata.set(
                            get_m["win"], get_m["game_state"], get_m["id"]
                        )
                elif message.get("isDemo") and message.get("balance"):
                    self.api.training_balance_edit_request = message
                elif message.get("error"):
                    global_value.websocket_error_reason = message.get("error")
                    global_value.check_websocket_if_error = True
                    if global_value.websocket_error_reason == "not_money":
                        self.api.account_balance = {"liveBalance": 0}
                elif not message.get("list") == []:
                    self.api.wss_message = message
            except:
                pass
            if str(message) == "41":
                print("Evento de desconexão disparado pela plataforma, fazendo reconexão automática.")
                global_value.check_websocket_if_connect = 0
                asyncio.run(self.api.reconnect())
            if "51-" in str(message):
                self.api._temp_status = str(message)
            elif self.api._temp_status == """451-["settings/list",{"_placeholder":true,"num":0}]""":
                self.api.settings_list = message
                self.api._temp_status = ""
            elif self.api._temp_status == """451-["history/list/v2",{"_placeholder":true,"num":0}]""":
                self.api.candles.candles_data = message["candles"]
                self.api.candle_v2_data[message["asset"]] = message
                self.api.candle_v2_data[message["asset"]]["candles"] = [{
                        "time": candle[0],
                        "open": candle[1],
                        "close": candle[2],
                        "high": candle[3],
                        "low": candle[4]
                    } for candle in message["candles"]]
            elif len(message[0]) == 4:
                result = {
                    "time": message[0][1],
                    "price": message[0][2]
                }
                self.api.realtime_price[message[0][0]].append(result)
            elif len(message[0]) == 2:
                result = {
                    "sentiment": {
                        "sell": 100 - int(message[0][1]),
                        "buy": int(message[0][1])
                    }
                }
                self.api.realtime_sentiment[message[0][0]] = result
        except:
            pass
        global_value.ssl_Mutual_exclusion = False

    def on_error(self, wss, error):
        """Method to process websocket errors."""
        logger.error(error)
        global_value.websocket_error_reason = str(error)
        global_value.check_websocket_if_error = True

    def on_open(self, wss):
        """Method to process websocket open."""
        logger.info("Websocket client connected.")
        global_value.check_websocket_if_connect = 1
        self.wss.send('42["tick"]')
        self.wss.send('42["indicator/list"]')
        self.wss.send('42["drawing/load"]')
        self.wss.send('42["pending/list"]')
        # self.wss.send('42["instruments/update",{"asset":"EURUSD","period":60}]')
        self.wss.send('42["chart_notification/get"]')
        # self.wss.send('42["depth/follow","EURUSD"]')
        self.wss.send('42["tick"]')

    def on_close(self, wss, close_status_code, close_msg):
        """Method to process websocket close."""
        logger.info("Websocket connection closed.")
        global_value.check_websocket_if_connect = 0

    def on_ping(self, wss, ping_msg):
        pass

    def on_pong(self, wss, pong_msg):
        self.wss.send("2")
