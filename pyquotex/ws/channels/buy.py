import logging
import time

import orjson

from pyquotex.ws.channels.base import Base

logger = logging.getLogger(__name__)
from pyquotex.expiration import get_expiration_time_quotex


class Buy(Base):
    """Class for Quotex buy websocket channel."""

    name = "buy"

    async def __call__(
            self,
            price: float | int,
            asset: str,
            direction: str,
            duration: int,
            request_id: int,
            is_fast_option: bool
    ) -> None:
        option_type = 3 if is_fast_option else 1

        expiration_time = get_expiration_time_quotex(
            int(time.time()),
            duration
        )
        expiration = expiration_time

        if asset.endswith("_otc") and not is_fast_option:
            option_type = 100
            expiration = duration

        if option_type == 1 and duration < 60:
            print(
                f"{duration}s duration is not allowed for this type of "
                "operation, except for OTC assets. 60 seconds will be added "
                "to meet Quotex requirements."
            )

        await self.api.settings_apply(
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
        await self.send_websocket_request(data)

        data = f'42["orders/open",{orjson.dumps(payload).decode()}]'
        logger.debug(data)
        await self.send_websocket_request(data)
