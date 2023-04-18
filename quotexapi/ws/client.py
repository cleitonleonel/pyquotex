"""Module for Quotex websocket."""
import os
import json
import random
import logging
import time

import websocket
from quotexapi import global_value
from quotexapi.http.user_agents import agents

user_agent_list = agents.split("\n")


class WebsocketClient(object):
    """Class for work with Quotex API websocket."""

    def __init__(self, api):
        """
        :param api: The instance of :class:`QuotexAPI
            <quotexapi.api.QuotexAPI>`.
        :trace_ws: Enables and disable `enableTrace` in WebSocket Client.
        """
        self.api = api
        self.headers = {
            "User-Agent": self.api.user_agent if not None
            else user_agent_list[random.randint(0, len(user_agent_list) - 1)],
        }
        websocket.enableTrace(self.api.trace_ws)
        self.wss = websocket.WebSocketApp(
            self.api.wss_url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
            header=self.headers,
            cookie=self.api.cookies
        )

    def on_message(self, wss, message):
        """Method to process websocket messages."""
        global_value.ssl_Mutual_exclusion = True
        try:
            logger = logging.getLogger(__name__)
            message = message
            if "authorization/reject" in str(message):
                if os.path.isfile("session.json"):
                    os.remove("session.json")
                global_value.SSID = None
                global_value.check_rejected_connection = 1
            elif "s_authorization" in str(message):
                global_value.check_accepted_connection = 1
            try:
                message = message[1:]
                message = message.decode()
                logger.debug(message)
                message = json.loads(str(message))
                self.api.profile.msg = message
                if "call" in str(message) or 'put' in str(message):
                    self.api.instruments = message
                    # print(message)
                elif message.get("liveBalance") or message.get("demoBalance"):
                    self.api.account_balance = message
                elif message.get("index"):
                    # print(message)
                    self.api.candles.candles_data = message
                elif message.get("id"):
                    self.api.buy_successful = message
                    self.api.buy_id = message["id"]
                    self.api.timesync.server_timestamp = message["closeTimestamp"]
                elif message.get("ticket"):
                    self.api.sold_options_respond = message
                if message.get("deals"):
                    for get_m in message["deals"]:
                        self.api.profit_in_operation = get_m["profit"]
                        get_m["win"] = True if message["profit"] > 0 else False
                        get_m["game_state"] = 1
                        self.api.listinfodata.set(
                            get_m["win"], get_m["game_state"], get_m["id"]
                        )
                if message.get("isDemo") and message.get("balance"):
                    self.api.training_balance_edit_request = message
                elif message.get("error"):
                    # print(message)
                    pass
            except:
                pass
            if len(message[0]) == 4:
                ans = {"time": message[0][1], "price": message[0][2]}
                self.api.realtime_price[message[0][0]].append(ans)
            if "signals" in str(message):
                for i in message["signals"]:
                    self.api.signal_data[i[0]][i[2]]["dir"] = i[1][0]["signal"]
                    self.api.signal_data[i[0]][i[2]]["duration"] = i[1][0]["timeFrame"]
            if "51-" in str(message):
                self.api._temp_status = str(message)
            elif self.api._temp_status == """51-["settings/list",{"_placeholder":true,"num":0}]""":
                self.api.settings_list = message
                self.api._temp_status = ""
            elif self.api._temp_status == """51-["history/list/v2",{"_placeholder":true,"num":0}]""":
                self.api.candle_v2_data[message["asset"]] = message["history"]
        except:
            pass
        wss.send('42["tick"]')
        global_value.ssl_Mutual_exclusion = False

    @staticmethod
    def on_error(wss, error):
        """Method to process websocket errors."""
        logger = logging.getLogger(__name__)
        logger.error(error)
        global_value.websocket_error_reason = str(error)
        global_value.check_websocket_if_error = True

    def on_open(self, wss):
        """Method to process websocket open."""
        logger = logging.getLogger(__name__)
        logger.debug("Websocket client connected.")
        global_value.check_websocket_if_connect = 1

    @staticmethod
    def on_close(wss, close_status_code, close_msg):
        """Method to process websocket close."""
        logger = logging.getLogger(__name__)
        logger.debug("Websocket connection closed.")
        global_value.check_websocket_if_connect = 0
