import json
from quotexapi.ws.channels.base import Base
from quotexapi.expiration import get_expiration_time_quotex


class Buy(Base):
    """Class for Quotex buy websocket channel."""

    name = "buy"

    def __call__(self, price, asset, direction, duration, request_id):
        option_type = 100
        if "_otc" not in asset:
            option_type = 1
            duration = get_expiration_time_quotex(
                int(self.api.timesync.server_timestamp),
                duration
            )

        data = f'42["depth/follow", f"{asset}"]'
        self.send_websocket_request(data)

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

        data = f'42["tick"]'
        self.send_websocket_request(data)

        data = f'42["orders/open",{json.dumps(payload)}]'
        self.send_websocket_request(data)
