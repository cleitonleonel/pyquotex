from typing import Any


class Base(object):
    """Class for base Quotex websocket channel."""

    def __init__(self, api: Any) -> None:
        """
        :param api: The instance of :class:`QuotexAPI
            <pyquotex.api.QuotexAPI>`.
        """
        self.api = api

    async def send_websocket_request(self, data: str) -> None:
        """Send request to Quotex server websocket.
        :param str data: The websocket channel data.
        """
        return await self.api.send_websocket_request(data)
