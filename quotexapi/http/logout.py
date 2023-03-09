"""Module for Quotex http login resource."""

from quotexapi.http.navigator import Browser
from quotexapi.http.resource import Resource


class Logout(Browser):
    """Class for Quotex login resource."""

    url = ""

    def _post(self, data=None, headers=None):
        """Send get request for Quotex API login http resource.
        :returns: The instance of :class:`navigator.Session`.
        """
        return self.send_request(method="POST",
                                 url="https://quotex.com/logout",
                                 data=data,
                                 headers=headers)

    def __call__(self):
        return self._post()
