"""Module for Quotex http login resource."""

from ..http.navigator import Browser


class Logout(Browser):
    """Class for Quotex login resource."""

    base_url = 'qxbroker.com'
    https_base_url = f'https://{base_url}'

    def _post(self, data=None, headers=None):
        """Send get request for Quotex API login http resource.
        :returns: The instance of :class:`navigator.Session`.
        """
        return self.send_request(method="POST",
                                 url=f"{self.https_base_url}/logout",
                                 data=data,
                                 headers=headers)

    def __call__(self):
        return self._post()
