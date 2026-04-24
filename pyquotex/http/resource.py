"""Module for base Quotex http base resource."""

from typing import Any


class Resource(object):
    """Class for base Quotex API http resource."""
    # pylint: disable=too-few-public-methods
    url: str = ""

    def __init__(self, api: Any):
        """
        :param api: The instance of :class:`QuotexAPI
            <pyquotex.api.QuotexAPI>`.
        """
        self.api = api

    async def send_http_request(
            self,
            method: str,
            data: dict[str, Any] | None = None,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None
    ) -> Any:
        """Send async http request to Quotex API.
        :param str method: The http request method.
        :param dict data: (optional) The http request data.
        :param dict params: (optional) The http request params.
        :param dict headers: (optional) The http request headers.
        :returns: The httpx.Response instance.
        """
        return await self.api.send_http_request_v1(
            method,
            self.url,
            data=data,
            params=params,
            headers=headers
        )
