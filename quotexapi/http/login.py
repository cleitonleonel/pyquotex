# quotexapi/http/login.py

from ..http.qxbroker import Browser


class Login(Browser):
    """Class for Quotex login resource."""

    async def __call__(self, email, password, user_data_dir=None):
        """Method to get Quotex API login http request.
        :param str email: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str user_data_dir: The optional value for path userdata.
        :returns: The instance of :class:`playwright.cookies`.
        """
        self.user_data_dir = user_data_dir
        self.email = email
        self.password = password
        return await self.get_cookies_and_ssid()
