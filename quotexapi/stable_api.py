import os
import time
import math
import logging
from datetime import datetime
from quotexapi import expiration
from quotexapi import global_value
from quotexapi.api import QuotexAPI
from quotexapi.constants import codes_asset
from collections import defaultdict


def nested_dict(n, type):
    if n == 1:
        return defaultdict(type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, type))


def truncate(f, n):
    return math.floor(f * 10 ** n) / 10 ** n


class Quotex(object):
    __version__ = "1.0"

    def __init__(self, email, password):
        self.size = [1, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
                     3600, 7200, 14400, 28800, 43200, 86400, 604800, 2592000]
        self.email = email
        self.password = password
        self.set_ssid = None
        self.duration = None
        self.suspend = 0.5
        self.subscribe_candle = []
        self.subscribe_candle_all_size = []
        self.subscribe_mood = []
        self.websocket_client = None
        self.websocket_thread = None
        self.debug_ws_enable = False
        self.api = None

    @property
    def websocket(self):
        """Property to get websocket.

        :returns: The instance of :class:`WebSocket <websocket.WebSocket>`.
        """
        return self.websocket_client.wss

    @staticmethod
    def check_connect():
        if not global_value.check_websocket_if_connect:
            return False
        else:
            return True

    def re_subscribe_stream(self):
        try:
            for ac in self.subscribe_candle:
                sp = ac.split(",")
                self.start_candles_one_stream(sp[0], sp[1])
        except:
            pass
        try:
            for ac in self.subscribe_candle_all_size:
                self.start_candles_all_size_stream(ac)
        except:
            pass
        try:
            for ac in self.subscribe_mood:
                self.start_mood_stream(ac)
        except:
            pass

    def get_instruments(self):
        time.sleep(self.suspend)
        self.api.instruments = None
        while self.api.instruments is None:
            try:
                self.api.get_instruments()
                start = time.time()
                while self.api.instruments is None and time.time() - start < 10:
                    pass
            except:
                logging.error('**error** api.get_instruments need reconnect')
                self.connect()
        return self.api.instruments

    def get_all_asset_name(self):
        if self.api.instruments:
            return [instrument[2].replace("\n", "") for instrument in self.api.instruments]

    def check_asset_open(self, instrument):
        if self.api.instruments:
            for i in self.api.instruments:
                if instrument == i[2]:
                    return i[0], i[2], i[14]

    def get_candles(self, asset, offset, period=None):
        index = expiration.get_timestamp()
        # index - offset
        if period:
            period = expiration.get_period_time(period)
        else:
            period = index
        self.api.current_asset = asset
        self.api.candles.candles_data = None
        while True:
            try:
                self.api.getcandles(codes_asset[asset], offset, period, index)
                while self.check_connect and self.api.candles.candles_data is None:
                    pass
                if self.api.candles.candles_data is not None:
                    break
            except:
                logging.error('**error** get_candles need reconnect')
                self.connect()
        return self.api.candles.candles_data

    def connect(self):
        try:
            self.api.close()
        except:
            pass
        self.api = QuotexAPI(
            "qxbroker.com",
            self.email,
            self.password
        )
        self.api.trace_ws = self.debug_ws_enable
        check, reason = self.api.connect()
        if check:
            self.api.send_ssid()
            if not global_value.check_accepted_connection:
                check, reason = False, "Acesso negado, sessão não existe!!!"
            return check, reason
        elif os.path.isfile("session.json"):
            os.remove("session.json")
        return False, reason

    def change_account(self, balance_mode="PRACTICE"):
        """Change active account `real` or `practice`"""
        if balance_mode.upper() == "REAL":
            self.api.account_type = 0
        elif balance_mode.upper() == "PRACTICE":
            self.api.account_type = 1
        else:
            logging.error("ERROR doesn't have this mode")
            exit(1)
        self.api.send_ssid()

    def edit_practice_balance(self, amount=None):
        self.api.training_balance_edit_request = None
        self.api.edit_training_balance(amount)
        while self.api.training_balance_edit_request is None:
            pass
        return self.api.training_balance_edit_request

    def get_balance(self):
        while not self.api.account_balance:
            time.sleep(0.1)
        balance = self.api.account_balance.get("liveBalance") \
                  or self.api.account_balance.get("demoBalance")
        return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

    def buy(self, price, asset, direction, duration):
        """Buy Binary option"""
        count = 0
        status_buy = False
        self.duration = duration - 1
        request_id = expiration.get_timestamp()
        self.api.current_asset = asset
        self.api.buy(price, asset, direction, duration, request_id)
        while not self.api.buy_id:
            if count == 10:
                break
            count += 1
            time.sleep(0.1)
        else:
            status_buy = True
        return status_buy, self.api.buy_successful

    def sell_option(self, options_ids):
        """Sell asset Quotex"""
        self.api.sell_option(options_ids)
        self.api.sold_options_respond = None
        while self.api.sold_options_respond is None:
            pass
        return self.api.sold_options_respond

    def get_payment(self):
        """Payment Quotex server"""
        assets_data = {}
        for i in self.api.instruments:
            assets_data[i[2]] = {
                "payment": i[5],
                "open": i[14]
            }
        return assets_data

    def check_win(self, id_number):
        """Check win based id"""
        now_stamp = datetime.fromtimestamp(expiration.get_timestamp())
        expiration_stamp = datetime.fromtimestamp(self.api.timesync.server_timestamp)
        remaing_time = int((expiration_stamp - now_stamp).total_seconds())
        while True:
            try:
                listinfodata_dict = self.api.listinfodata.get(id_number)
                if listinfodata_dict["game_state"] == 1:
                    break
            except:
                pass
            remaing_time -= 1
            time.sleep(1)
            print(f"\rRestando {remaing_time if remaing_time > 0 else 0} segundos ...", end="")
        self.api.listinfodata.delete(id_number)
        return listinfodata_dict["win"]

    def get_signal_data(self):
        """ Get signal Quotex server"""
        pass

    def get_profit(self):
        return self.api.profit_in_operation or 0

    def start_candles_one_stream(self, asset, size):
        if not (str(asset + "," + str(size)) in self.subscribe_candle):
            self.subscribe_candle.append((asset + "," + str(size)))
        start = time.time()
        self.api.candle_generated_check[str(asset)][int(size)] = {}
        while True:
            if time.time() - start > 20:
                logging.error(
                    '**error** start_candles_one_stream late for 20 sec')
                return False
            try:
                if self.api.candle_generated_check[str(asset)][int(size)]:
                    return True
            except:
                pass
            try:
                self.api.subscribe(codes_asset[asset], size)
            except:
                logging.error('**error** start_candles_stream reconnect')
                self.connect()
            time.sleep(1)

    def start_candles_all_size_stream(self, asset):
        self.api.candle_generated_all_size_check[str(asset)] = {}
        if not (str(asset) in self.subscribe_candle_all_size):
            self.subscribe_candle_all_size.append(str(asset))
        start = time.time()
        while True:
            if time.time() - start > 20:
                logging.error(f'**error** fail {asset} start_candles_all_size_stream late for 10 sec')
                return False
            try:
                if self.api.candle_generated_all_size_check[str(asset)]:
                    return True
            except:
                pass
            try:
                self.api.subscribe_all_size(codes_asset[asset])
            except:
                logging.error(
                    '**error** start_candles_all_size_stream reconnect')
                self.connect()
            time.sleep(1)

    def start_mood_stream(self, asset, instrument="turbo-option"):
        if asset not in self.subscribe_mood:
            self.subscribe_mood.append(asset)
        while True:
            self.api.subscribe_Traders_mood(
                asset[asset], instrument)
            try:
                self.api.traders_mood[codes_asset[asset]] = codes_asset[asset]
                break
            except:
                time.sleep(5)

    def close(self):
        self.api.close()
