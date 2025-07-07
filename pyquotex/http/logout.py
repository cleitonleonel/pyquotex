"""Module for Quotex http login resource."""

from ..http.resource import Resource


class Logout(Resource):
    """Class for Quotex login resource."""

    def _get(self, data=None, headers=None):
        """Send get request for Quotex API login http resource.
        :returns: The instance of :class:`navigator.Session`.
        """
        return self.send_http_request(
            method="GET",
            data=data,
            headers=headers
        )

    async def __call__(self):
        self.url = f"{self.api.https_url}/{self.api.lang}/logout"
        headers = {
            "referer": f"{self.api.https_url}/{self.api.lang}/trade"
        }
        return self._get(headers=headers)
