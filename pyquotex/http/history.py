"""Module for Quotex http history resource."""

from typing import Any

from ..http.resource import Resource


class GetHistory(Resource):
    """Class for Quotex history resource."""

    async def _get(
            self,
            data: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None
    ) -> Any:
        """Send get request for Quotex API history http resource.
        :returns: The httpx.Response instance.
        """
        return await self.send_http_request(
            method="GET",
            data=data,
            headers=headers
        )

    async def __call__(
            self,
            account_type: str | int,
            page_number: int = 1
    ) -> dict[str, Any]:
        self.url = (
            f"{self.api.https_url}/api/v1/cabinets/trades/history/"
            f"type/{account_type}?page={page_number}"
        )
        headers = {
            "referer": f"{self.api.https_url}/{self.api.lang}/trade",
            "cookie": self.api.session_data.get("cookies", ""),
            "content-type": "application/json",
            "accept": "application/json",
        }
        response = await self._get(headers=headers)
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception:
                return {}
        return {}
