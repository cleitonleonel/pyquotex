from quotexapi.http.qxbroker import Browser


class Login(Browser):
    """Class for Quotex login resource."""

    url = ""
    cookies = None
    ssid = None
    base_url = 'qxbroker.com'
    https_base_url = f'https://{base_url}'

    async def __call__(self, username, password):
        """Method to get Quotex API login http request.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :returns: The instance of :class:`playwright.cookies`.
        """
        self.username = username
        self.password = password
        self.ssid, self.cookies = await self.get_cookies_and_ssid()
        return self.ssid, self.cookies
