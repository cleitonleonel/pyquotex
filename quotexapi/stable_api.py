import time
import logging
import asyncio
from datetime import datetime
from . import expiration
from . import global_value
from .api import QuotexAPI
from .constants import codes_asset
from .utils.services import truncate
from .config import (
    load_session,
    resource_path
)

__version__ = "1.0.0"
logger = logging.getLogger(__name__)


class Quotex(object):

    def __init__(
            self,
            email,
            password,
            lang="pt",
            email_pass=None,
            user_agent="Quotex/1.0",
            root_path=".",
            user_data_dir="browser",
            asset_default="EURUSD",
            period_default=60
    ):
        self.size = [
            1,
            5,
            10,
            15,
            30,
            60,
            120,
            300,
            600,
            900,
            1800,
            3600,
            7200,
            14400,
            86400,
        ]
        self.email = email
        self.password = password
        self.lang = lang
        self.email_pass = email_pass
        self.resource_path = root_path
        self.user_data_dir = user_data_dir
        self.asset_default = asset_default
        self.period_default = period_default
        self.subscribe_candle = []
        self.subscribe_candle_all_size = []
        self.subscribe_mood = []
        self.account_is_demo = 1
        self.suspend = 0.5
        self.api = None
        self.session_data = None
        self.set_ssid = None
        self.duration = None
        self.websocket_client = None
        self.websocket_thread = None
        self.debug_ws_enable = False
        self.resource_path = resource_path(root_path)
        session = load_session(user_agent)
        self.session_data = session

    @property
    def websocket(self):
        """Property to get websocket.
        :returns: The instance of :class:`WebSocket <websocket.WebSocket>`.
        """
        return self.websocket_client.wss

    @staticmethod
    def check_connect():
        if global_value.check_accepted_connection == 1:
            return True
        else:
            return False

    def set_session(self, user_agent: str, cookies: str = None, ssid: str = None):
        session = {
            "user_agent": user_agent,
            "cookies": cookies,
            "token": ssid
        }

        self.session_data = session

    async def re_subscribe_stream(self):
        try:
            for ac in self.subscribe_candle:
                sp = ac.split(",")
                await self.start_candles_one_stream(sp[0], sp[1])
        except:
            pass
        try:
            for ac in self.subscribe_candle_all_size:
                await self.start_candles_all_size_stream(ac)
        except:
            pass
        try:
            for ac in self.subscribe_mood:
                await self.start_mood_stream(ac)
        except:
            pass

    async def get_instruments(self):
        while self.check_connect and self.api.instruments is None:
            await asyncio.sleep(0.1)
        return self.api.instruments or []

    def get_all_asset_name(self):
        if self.api.instruments:
            return [instrument[2].replace("\n", "") for instrument in self.api.instruments]

    async def get_available_asset(self, asset_name: str, force_open: bool = False):
        asset_open = await self.check_asset_open(asset_name)
        if force_open and (not asset_open or not asset_open[2]):
            condition_otc = "otc" not in asset_name
            refactor_asset = asset_name.replace("_otc", "")
            asset_name = f"{asset_name}_otc" if condition_otc else refactor_asset
            asset_open = await self.check_asset_open(asset_name)
        return asset_name, asset_open

    async def check_asset_open(self, asset_name: str):
        instruments = await self.get_instruments()
        for i in instruments:
            if asset_name == i[1]:
                self.api.current_asset = asset_name
                return i[0], i[2], i[14]

    async def get_candles(self, asset, end_from_time, offset, period):
        index = expiration.get_timestamp()
        self.api.current_asset = asset
        self.api.candles.candles_data = None
        self.start_candles_stream(asset, period)
        while True:
            try:
                self.api.get_candles(asset, index, end_from_time, offset, period)
                while self.check_connect and self.api.candles.candles_data is None:
                    await asyncio.sleep(0.1)
                if self.api.candles.candles_data is not None:
                    break
            except:
                logger.error('**error** get_candles need reconnect')
                await self.connect()
        return self.api.candles.candles_data

    async def get_candle_v2(self, asset, period):
        self.api.candle_v2_data[asset] = None
        self.stop_candles_stream(asset)
        self.api.subscribe_realtime_candle(asset, period)
        while self.api.candle_v2_data[asset] is None:
            await asyncio.sleep(0.1)
        return self.api.candle_v2_data[asset]

    async def connect(self):
        self.api = QuotexAPI(
            "qxbroker.com",
            self.email,
            self.password,
            self.lang,
            email_pass=self.email_pass,
            resource_path=self.resource_path,
            user_data_dir=self.user_data_dir
        )
        await self.api.logout()
        self.api.trace_ws = self.debug_ws_enable
        self.api.session_data = self.session_data
        self.api.current_asset = self.asset_default
        self.api.current_period = self.period_default
        global_value.SSID = self.session_data.get("token")
        check, reason = await self.api.connect(self.account_is_demo)
        if check:
            if global_value.check_accepted_connection == 0:
                check, reason = await self.connect()
                if not check:
                    check, reason = check, "Acesso negado, sessão não existe!!!"
        return check, reason

    def set_account_mode(self, balance_mode="PRACTICE"):
        """Set active account `real` or `practice`"""
        if balance_mode.upper() == "REAL":
            self.account_is_demo = 0
        elif balance_mode.upper() == "PRACTICE":
            self.account_is_demo = 1
        else:
            logger.error("ERROR doesn't have this mode")
            exit(1)

    def change_account(self, balance_mode: str):
        """Change active account `real` or `practice`"""
        self.account_is_demo = 0 if balance_mode.upper() == "REAL" else 1
        self.api.change_account(self.account_is_demo)

    async def edit_practice_balance(self, amount=None):
        self.api.training_balance_edit_request = None
        self.api.edit_training_balance(amount)
        while self.api.training_balance_edit_request is None:
            await asyncio.sleep(0.1)
        return self.api.training_balance_edit_request

    async def get_balance(self):
        while self.api.account_balance is None:
            await asyncio.sleep(0.1)
        balance = self.api.account_balance.get("demoBalance") \
            if self.api.account_type > 0 else self.api.account_balance.get("liveBalance")
        return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

    async def get_profile(self):
        return await self.api.get_profile()

    async def buy(self, amount: float, asset: str, direction: str, duration: int):
        """Buy Binary option"""
        request_id = expiration.get_timestamp()
        self.api.buy_id = None
        self.api.current_asset = asset
        self.api.timesync.server_timestamp = time.time()
        self.api.subscribe_realtime_candle(asset, duration)
        self.api.buy(amount, asset, direction, duration, request_id)
        count = 0.1
        while self.api.buy_id is None:
            count += 0.1
            if count > duration:
                status_buy = False
                break
            await asyncio.sleep(0.1)
            if global_value.check_websocket_if_error:
                return False, global_value.websocket_error_reason
        else:
            status_buy = True
        return status_buy, self.api.buy_successful

    async def sell_option(self, options_ids):
        """Sell asset Quotex"""
        self.api.sell_option(options_ids)
        self.api.sold_options_respond = None
        while self.api.sold_options_respond is None:
            await asyncio.sleep(0.1)
        return self.api.sold_options_respond

    def get_payment(self):
        """Payment Quotex server"""
        assets_data = {}
        for i in self.api.instruments:
            assets_data[i[2]] = {
                "turbo_payment": i[18],
                "payment": i[5],
                "open": i[14]
            }
        return assets_data

    async def start_remaing_time(self):
        now_stamp = datetime.fromtimestamp(expiration.get_timestamp())
        expiration_stamp = datetime.fromtimestamp(self.api.timesync.server_timestamp)
        remaing_time = int((expiration_stamp - now_stamp).total_seconds())
        while remaing_time >= 0:
            remaing_time -= 1
            print(f"\rRestando {remaing_time if remaing_time > 0 else 0} segundos ...", end="")
            await asyncio.sleep(1)
        await asyncio.sleep(5)

    async def check_win(self, id_number):
        """Check win based id"""
        await self.start_remaing_time()
        while True:
            try:
                listinfodata_dict = self.api.listinfodata.get(id_number)
                if listinfodata_dict["game_state"] == 1:
                    break
            except:
                pass
        self.api.listinfodata.delete(id_number)
        return listinfodata_dict["win"]

    def start_candles_stream(self, asset, period=0):
        self.api.follow_candle(asset)
        self.api.subscribe_realtime_candle(asset, period)

    def stop_candles_stream(self, asset):
        self.api.unfollow_candle(asset)
        self.api.unsubscribe_realtime_candle(asset)

    def start_signals_data(self):
        self.api.signals_subscribe()

    def get_realtime_candles(self, asset):
        while True:
            if self.api.realtime_price.get(asset):
                return self.api.realtime_price
            time.sleep(0.1)

    def get_realtime_sentiment(self, asset):
        while True:
            if self.api.realtime_sentiment.get(asset):
                return self.api.realtime_sentiment
            time.sleep(0.1)

    def get_signal_data(self):
        return self.api.signal_data

    def get_profit(self):
        return self.api.profit_in_operation or 0

    async def start_candles_one_stream(self, asset, size):
        if not (str(asset + "," + str(size)) in self.subscribe_candle):
            self.subscribe_candle.append((asset + "," + str(size)))
        start = time.time()
        self.api.candle_generated_check[str(asset)][int(size)] = {}
        while True:
            if time.time() - start > 20:
                logger.error(
                    '**error** start_candles_one_stream late for 20 sec')
                return False
            try:
                if self.api.candle_generated_check[str(asset)][int(size)]:
                    return True
            except:
                pass
            try:
                self.api.follow_candle(codes_asset[asset])
            except:
                logger.error('**error** start_candles_stream reconnect')
                await self.connect()
            await asyncio.sleep(0.1)

    async def start_candles_all_size_stream(self, asset):
        self.api.candle_generated_all_size_check[str(asset)] = {}
        if not (str(asset) in self.subscribe_candle_all_size):
            self.subscribe_candle_all_size.append(str(asset))
        start = time.time()
        while True:
            if time.time() - start > 20:
                logger.error(f'**error** fail {asset} start_candles_all_size_stream late for 10 sec')
                return False
            try:
                if self.api.candle_generated_all_size_check[str(asset)]:
                    return True
            except:
                pass
            try:
                self.api.subscribe_all_size(codes_asset[asset])
            except:
                logger.error(
                    '**error** start_candles_all_size_stream reconnect')
                await self.connect()
            await asyncio.sleep(0.1)

    async def start_mood_stream(self, asset, instrument="turbo-option"):
        if asset not in self.subscribe_mood:
            self.subscribe_mood.append(asset)
        while True:
            self.api.subscribe_Traders_mood(
                asset[asset], instrument)
            try:
                self.api.traders_mood[codes_asset[asset]] = codes_asset[asset]
                break
            finally:
                await asyncio.sleep(0.1)

    def close(self):
        return self.api.close()
