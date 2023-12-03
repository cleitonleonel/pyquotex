import json
from quotexapi.ws.channels.base import Base


class GetCandles(Base):
    """Class for Quotex candles websocket channel."""

    name = "candles"

    def __call__(self, asset_id, offset, period, index):
        """Method to send message to candles websocket chanel.

        :param asset_id: The active/asset identifier.
        :param period: The candle duration (timeframe for the candles).
        :param offset: The number of candles you want to have
        :param index: The index of candles.
        """
        payload = {
            "id": asset_id,
            "index": index,
            "time": period,
            "offset": offset,
        }
        data = f'42["history/load/line",{json.dumps(payload)}]'
        self.send_websocket_request(data)
