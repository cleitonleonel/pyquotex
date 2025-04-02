"""Module for Quotex websocket."""
import json
import time
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
            "Origin": self.api.https_url,
            "Host": f"ws2.{self.api.host}",
        }

        websocket.enableTrace(self.api.trace_ws)
        self.wss = websocket.WebSocketApp(
            self.api.wss_url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
            on_ping=self.on_ping,
            on_pong=self.on_pong,
            header=self.headers,
            # cookie=self.api.cookies
        )

    def on_message(self, wss, message):
        """Method to process websocket messages."""
        global_value.ssl_Mutual_exclusion = True
        current_time = time.localtime()
        if current_time.tm_sec in [0, 5, 10, 15, 20, 30, 40, 50]:
            self.wss.send('42["tick"]')
        try:
            if "authorization/reject" in str(message):
                print("Token rejected, making automatic reconnection.")
                logger.debug("Token rejected, making automatic reconnection.")
                global_value.check_rejected_connection = 1
            elif "s_authorization" in str(message):
                global_value.check_accepted_connection = 1
                global_value.check_rejected_connection = 0
            elif "instruments/list" in str(message):
                global_value.started_listen_instruments = True

            try:
                message = message[1:].decode()
                logger.debug(message)
                message = json.loads(message)
                self.api.wss_message = message
                if "call" in str(message) or 'put' in str(message):
                    self.api.instruments = message
                if isinstance(message, dict):
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
                    elif message.get("position"):
                        self.api.top_list_leader = message
                    elif len(message) == 1 and message.get("profit", -1) > -1:
                        self.api.profit_today = message
                    elif message.get("index"):
                        self.api.historical_candles = message
                        self.api.timesync.server_timestamp = message.get("closeTimestamp")
                    if message.get("pending"):
                        self.api.pending_successful = message
                        self.api.pending_id = message["pending"]["ticket"]
                    elif message.get("id") and not message.get("ticket"):
                        self.api.buy_successful = message
                        self.api.buy_id = message["id"]
                        self.api.timesync.server_timestamp = message.get("closeTimestamp")
                    elif message.get("ticket") and not message.get("id"):
                        self.api.sold_options_respond = message
                    elif message.get("deals"):
                        for get_m in message["deals"]:
                            self.api.profit_in_operation = get_m["profit"]
                            get_m["win"] = True if message["profit"] > 0 else False
                            get_m["game_state"] = 1
                            self.api.listinfodata.set(
                                get_m["win"],
                                get_m["game_state"],
                                get_m["id"]
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
                logger.info("Disconnection event triggered by the platform, causing automatic reconnection.")
                global_value.check_websocket_if_connect = 0
            if "51-" in str(message):
                self.api._temp_status = str(message)
            elif self.api._temp_status == """451-["settings/list",{"_placeholder":true,"num":0}]""":
                self.api.settings_list = message
                self.api._temp_status = ""
            elif self.api._temp_status == """451-["history/list/v2",{"_placeholder":true,"num":0}]""":
                if message.get("asset") == self.api.current_asset:
                    self.api.candles.candles_data = message["history"]
                    self.api.candle_v2_data[message["asset"]] = message
                    self.api.candle_v2_data[message["asset"]]["candles"] = [{
                        "time": candle[0],
                        "open": candle[1],
                        "close": candle[2],
                        "high": candle[3],
                        "low": candle[4],
                        "ticks": candle[5]
                    } for candle in message["candles"]]
            elif len(message[0]) == 4:
                result = {
                    "time": message[0][1],
                    "price": message[0][2]
                }
                self.api.realtime_price[message[0][0]].append(result)
                self.api.realtime_candles = message[0]
            elif len(message[0]) == 2:
                for i in message:
                    result = {
                        "sentiment": {
                            "sell": 100 - int(i[1]),
                            "buy": int(i[1])
                        }
                    }
                    self.api.realtime_sentiment[i[0]] = result
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
        asset_name = self.api.current_asset
        period = self.api.current_period
        self.wss.send('42["tick"]')
        self.wss.send('42["indicator/list"]')
        self.wss.send('42["drawing/load"]')
        self.wss.send('42["pending/list"]')
        self.wss.send('42["instruments/update",{"asset":"%s","period":%d}]' % (asset_name, period))
        self.wss.send('42["depth/follow","%s"]' % asset_name)
        self.wss.send('42["chart_notification/get"]')
        self.wss.send('42["tick"]')

    def on_close(self, wss, close_status_code, close_msg):
        """Method to process websocket close."""
        logger.info("Websocket connection closed.")
        global_value.check_websocket_if_connect = 0

    def on_ping(self, wss, ping_msg):
        pass

    def on_pong(self, wss, pong_msg):
        self.wss.send("2")
