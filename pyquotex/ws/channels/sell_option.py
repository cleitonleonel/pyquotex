import orjson

from pyquotex.ws.channels.base import Base


class SellOption(Base):
    """Class for Quotex sell option websocket channel."""

    name = "sell_option"

    async def __call__(self, options_ids: list[int | str] | int | str) -> None:
        """
        :param options_ids: list or int/str
        """
        if not isinstance(options_ids, list):
            payload = {
                "ticket": options_ids
            }
            await self.send_websocket_request(
                f'42["orders/cancel",{orjson.dumps(payload).decode()}]'
            )
        else:
            for ids in options_ids:
                payload = {
                    "ticket": ids
                }
                await self.send_websocket_request(
                    f'42["orders/cancel",{orjson.dumps(payload).decode()}]'
                )
