"""Module for Quotex http logout resource."""

from typing import Any

from ..network.resource import Resource


class Logout(Resource):
    """Class for Quotex logout resource."""

    async def _get(
            self,
            data: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None
    ) -> Any:
        """Send get request for Quotex API logout http resource.
        :returns: The httpx.Response instance.
        """
        return await self.send_http_request(
            method="GET",
            data=data,
            headers=headers
        )

    async def __call__(self) -> Any:
        self.url = f"{self.api.https_url}/{self.api.lang}/logout"
        headers = {
            "referer": f"{self.api.https_url}/{self.api.lang}/trade"
        }
        return await self._get(headers=headers)
