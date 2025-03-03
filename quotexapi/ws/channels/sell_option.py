import json
from quotexapi.ws.channels.base import Base


class SellOption(Base):
    """Class for Quotex sell option websocket channel."""

    name = "sell_option"

    def __call__(self, options_ids):
        """
        :param options_ids: list or int
        """
        if type(options_ids) != list:
            payload = {
                "ticket": options_ids
            }
            self.send_websocket_request(f'42["orders/cancel",{json.dumps(payload)}]')
        else:
            for ids in options_ids:
                payload = {
                    "ticket": ids
                }
                self.send_websocket_request(f'42["orders/cancel",{json.dumps(payload)}]')
