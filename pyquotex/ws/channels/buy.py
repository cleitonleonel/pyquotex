import logging
import time

from pyquotex.expiration import get_expiration_time_quotex
from pyquotex.utils import json_utils as json
from pyquotex.utils.option_type import OptionType, is_otc_asset, resolve_option_type
from pyquotex.ws.channels.base import Base

logger = logging.getLogger(__name__)


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
        option_type = resolve_option_type(
            asset, duration, is_fast_option, is_pending=False
        )

        expiration_time = get_expiration_time_quotex(
            int(time.time()), duration
        )

        # OTC digital options use the duration directly as the ``time``
        # field; regular binaries and blitz options use the rounded-up
        # expiration timestamp.
        expiration = (
            duration
            if option_type is OptionType.DIGITAL_OTC and is_otc_asset(asset)
            else expiration_time
        )

        if option_type is OptionType.BINARY and duration < 60:
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
            "tournamentId": self.api.tournament_id,
            "requestId": request_id,
            "optionType": int(option_type),
        }

        # The heartbeat task pings ``42["tick"]`` every 5s; an extra
        # tick before each order is pure overhead. Send the order
        # directly.
        data = f'42["orders/open",{json.dumps_str(payload)}]'
        logger.debug(data)
        await self.send_websocket_request(data)
