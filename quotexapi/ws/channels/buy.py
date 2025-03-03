# quotexapi/ws/channels/buy.py

import json
from quotexapi.ws.channels.base import Base
from quotexapi.expiration import get_expiration_time_quotex


class Buy(Base):
    """Class for Quotex buy websocket channel."""

    name = "buy"

    def __call__(self, price, asset, direction, duration, request_id, is_fast_option):
        option_type = 100
        expiration_time = get_expiration_time_quotex(
            int(self.api.timesync.server_timestamp),
            duration
        )

        duration = expiration_time

        if "_otc" not in asset or is_fast_option:
            option_type = 1
            if is_fast_option:
                self.api.settings_apply(
                    asset,
                    duration,
                    is_fast_option=True,
                    end_time=expiration_time,
                )

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
