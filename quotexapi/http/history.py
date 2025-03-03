"""Module for Quotex http history resource."""

from ..http.resource import Resource


class GetHistory(Resource):
    """Class for Quotex history resource."""

    def _get(self, data=None, headers=None):
        """Send get request for Quotex API history http resource.
        :returns: The instance of :class:`navigator.Session`.
        """
        return self.send_http_request(
            method="GET",
            data=data,
            headers=headers
        )

    async def __call__(self, account_type, page_number=1):
        self.url = f"{self.api.https_url}/api/v1/cabinets/trades/history/type/{account_type}?page={page_number}"
        headers = {
            "referer": f"{self.api.https_url}/{self.api.lang}/trade",
            "cookie": self.api.session_data["cookies"],
            "content-type": "application/json",
            "accept": "application/json",
        }
        response = self._get(headers=headers)
        if response:
            return response.json()
        return {}
