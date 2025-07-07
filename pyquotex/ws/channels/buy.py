import json
import time
from pyquotex.ws.channels.base import Base
from pyquotex.expiration import get_expiration_time_quotex


class Buy(Base):
    """Class for Quotex buy websocket channel."""

    name = "buy"

    def __call__(self, price, asset, direction, duration, request_id, is_fast_option):
        option_type = 1

        expiration_time = get_expiration_time_quotex(
            int(time.time()),
            duration
        )
        expiration = expiration_time

        if asset.endswith("_otc") and not is_fast_option:
            option_type = 100
            expiration = duration

        if option_type == 1 and duration < 60:
            print(f"{duration}s duration is not allowed for this type of operation, except for OTC assets. "
                  f"60 seconds will be added to meet Quotex requirements.")

        self.api.settings_apply(
            asset,
            expiration,
            is_fast_option=is_fast_option,
            end_time=expiration_time,
        )

        payload = {
            "asset": asset,
            "amount": price,
            "time": expiration,
            "action": direction,
            "isDemo": self.api.account_type,
            "tournamentId": 0,
            "requestId": request_id,
            "optionType": option_type
        }

        data = f'42["tick"]'
        self.send_websocket_request(data)

        data = f'42["orders/open",{json.dumps(payload)}]'
        print(data)
        self.send_websocket_request(data)
