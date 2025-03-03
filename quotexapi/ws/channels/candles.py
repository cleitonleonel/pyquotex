import json
from quotexapi.ws.channels.base import Base


class GetCandles(Base):
    """Class for Quotex candles websocket channel."""

    name = "candles"

    def __call__(self, asset, index, time, offset, period):
        """Method to send message to candles websocket chanel.

        :param asset: The active/asset identifier.
        :param index: The index of candles.
        :param time: The time of candles.
        :param offset: Time interval in which you want to catalog the candles
        :param period: The candle duration (timeframe for the candles).
        """
        payload = {
            "asset": asset,
            "index": index,
            "time": time,
            "offset": offset,
            "period": period
        }
        data = f'42["history/load",{json.dumps(payload)}]'
        self.send_websocket_request(data)
