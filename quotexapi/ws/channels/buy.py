import json
from quotexapi.ws.channels.base import Base
from quotexapi.expiration import get_expiration_time


class Buy(Base):
    """Class for Quotex buy websocket channel."""

    name = "buy"

    def __call__(self, price, asset, direction, duration, request_id):
        """exp, idx = get_expiration_time(
            int(self.api.timesync.server_timestamp), duration)"""
        option_type = 100
        if "_otc" not in asset:
            option_type = 1
            duration = get_expiration_time(
                int(self.api.timesync.server_timestamp), duration)
        payload = {
            "chartId": "graph",
            "settings": {
                "chartId": "graph",
                "chartType": 2,
                "currentExpirationTime": duration,
                "isFastOption": False,
                "isFastAmountOption": False,
                "isIndicatorsMinimized": False,
                "isIndicatorsShowing": True,
                "isShortBetElement": False,
                "chartPeriod": 4,
                "currentAsset": {
                    "symbol": asset
                },
                "dealValue": 5,
                "dealPercentValue": 1,
                "isVisible": True,
                "timePeriod": 30,
                "gridOpacity": 8,
                "isAutoScrolling": 1,
                "isOneClickTrade": True,
                "upColor": "#0FAF59",
                "downColor": "#FF6251"
            }
        }
        data = f'42["settings/store",{json.dumps(payload)}]'
        self.send_websocket_request(data)

        payload = {
            "asset": asset,
            "amount": price,
            "time": duration,
            "action": direction,
            "isDemo": self.api.account_type,
            "tournamentId": 0,
            "requestId": request_id,
            "optionType": option_type
        }
        data = f'42["orders/open",{json.dumps(payload)}]'
        self.send_websocket_request(data)


"""
42["pending/create",{"openType":1,"asset":"EURUSD","openPrice":"1.06401","timeframe":120,"command":"call","amount":5}]
{"pending":{"ticket":"4f296304-2dee-4271-8893-3bc4adc56daf","openType":1,"amount":5,"uid":20522712,"isDemo":1,"asset":"EURUSD","openPrice":"1.06401","timeframe":120,"command":0,"currency":"BRL"}}
42["instruments/follow",{"amount":5,"command":0,"currency":"BRL","isDemo":1,"min_payout":0,"open_price":"1.06401","open_type":1,"symbol":"EURUSD","ticket":"4f296304-2dee-4271-8893-3bc4adc56daf","timeframe":120,"uid":20522712}]
42["settings/store",{"chartId":"graph","settings":{"chartId":"graph","chartType":2,"currentExpirationTime":1677005460,"isFastOption":False,"isFastAmountOption":False,"isIndicatorsMinimized":False,"isIndicatorsShowing":True,"isShortBetElement":False,"chartPeriod":4,"currentAsset":{"symbol":"EURUSD"},"dealValue":5,"dealPercentValue":1,"isVisible":True,"timePeriod":60,"gridOpacity":8,"isAutoScrolling":1,"isOneClickTrade":True,"upColor":"#0FAF59","downColor":"#FF6251"}}]	
"""
