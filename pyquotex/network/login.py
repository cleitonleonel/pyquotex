import inspect
import re
import sys
from typing import Any

from bs4.element import AttributeValueList

from pyquotex._api._waits import backoff_sleep
from pyquotex.config import update_session
from pyquotex.network.navigator import Browser
from pyquotex.utils import json_utils as json


class Login(Browser):
    """Class for Quotex login resource."""

    url: str = ""
    cookies: str | None = None
    ssid: str | None = None
    def __init__(self, api: Any, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.api = api
        self.html: Any = None
        self.headers: dict[str, str] = self.get_headers()
        self.full_url: str = f"{api.https_url}/{api.lang}"

    def _nav_headers(self, referer: str) -> dict[str, str]:
        """Headers Chrome sends on a top-level/iframe document navigation.

        Cloudflare's managed rules challenge login POSTs that lack the
        ``Sec-Fetch-*``/``Origin`` set a real browser would send, even when
        the TLS fingerprint is impersonated correctly.
        """
        return {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": referer,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-User": "?1",
        }

    def _form_headers(self, referer: str) -> dict[str, str]:
        """Headers Chrome sends when submitting an HTML form (POST)."""
        headers = self._nav_headers(referer)
        headers.update({
            "Origin": self.api.https_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "max-age=0",
        })
        return headers

    async def get_sign_page(self):
        headers = self._nav_headers(self.api.https_url)
        headers["Sec-Fetch-Dest"] = "document"
        response = await self.send_request(
            method="GET",
            url=self.full_url,
            headers=headers
        )

        if not response.is_success:
            return response

        cookies_dict = dict(response.cookies)
        cookies_str = '; '.join([f"{key}={value}" for key, value in cookies_dict.items()])
        self.cookies = cookies_str
        return response

    async def get_token(self) -> None | str | AttributeValueList:
        self.headers["Cookie"] = self.cookies or ''
        await self.send_request(
            "GET",
            f"{self.full_url}/sign-in/modal/",
            headers=self._nav_headers(f"{self.full_url}/sign-in"),
        )
        html = self.get_soup()
        match = html.find(
            "input", {"name": "_token"}
        )
        token = None if not match else match.get("value")
        return token

    async def awaiting_pin(
            self,
            data: dict[str, Any],
            input_message: str
    ) -> None:
        self.headers["Content-Type"] = "application/x-www-form-urlencoded"
        self.headers["Referer"] = f"{self.full_url}/sign-in/modal/"
        data["keep_code"] = 1
        try:
            if self.api.on_otp_callback:
                if inspect.iscoroutinefunction(self.api.on_otp_callback):
                    code = await self.api.on_otp_callback(input_message)
                else:
                    code = self.api.on_otp_callback(input_message)
            else:
                code = input(input_message)

            if not code or not str(code).isdigit():
                print("Please enter a valid code.")
                return await self.awaiting_pin(data, input_message)
            data["code"] = code
        except KeyboardInterrupt:
            print("\nClosing program.")
            sys.exit()

        # TODO: this is a linear settle-delay between OTP entry and POST,
        # not a counted retry. backoff_sleep(0) preserves ~1s pacing today;
        # a proper retry counter should be introduced when this method is
        # refactored to handle transient PIN-submission failures.
        await backoff_sleep(0)
        self.response = await self.send_request(
            method="POST",
            url=f"{self.full_url}/sign-in/modal/",
            data=data,
            headers=self._form_headers(f"{self.full_url}/sign-in/modal/"),
        )

    async def get_profile(self):
        headers = {
            "Referer": f"{self.api.https_url}/{self.api.lang}/trade",
            "Cookie": self.api.session_data["cookies"]
        }
        response = await self.send_request(
            method="GET",
            url=f"{self.api.https_url}/api/v1/cabinets/digest",
            headers=headers
        )
        if response.is_success:
            data = response.json()["data"]
            self.api.session_data["token"] = data.get("token")

        return response

    async def get_settings(self) -> tuple[Any, dict[str, Any] | None]:
        script_tags = self.get_soup().find_all(
            "script",
            {"type": "text/javascript"}
        )
        script_content = script_tags[0].get_text() if script_tags else "{}"
        match = re.sub(
            "window.settings = ",
            "",
            script_content.strip().replace(";", "")
        )
        self.cookies = self.get_cookies()
        try:
            settings_data = json.loads(match)
            self.ssid = settings_data.get("token")
            self.api.session_data["cookies"] = self.cookies
            self.api.session_data["token"] = self.ssid
            self.api.session_data["user_agent"] = (
                self.headers["User-Agent"]
            )

            update_session(self.api.username, self.api.session_data)
            return self.response, settings_data
        except Exception:
            return self.response, None

    async def _post(self, data: dict[str, Any]) -> tuple[bool, str]:
        """Send post-request for Quotex API login http resource.
        :returns: The instance of: class:`httpx.Response`.
        """
        self.response = await self.send_request(
            method="POST",
            url=f"{self.full_url}/sign-in/",
            data=data,
            headers=self._form_headers(f"{self.full_url}/sign-in/modal/"),
        )
        required_keep_code = self.get_soup().find(
            "input", {"name": "keep_code"}
        )
        if required_keep_code:
            auth_body = self.get_soup().find(
                "main", {"class": "auth__body"}
            )
            input_message = (
                f'{auth_body.find("p").text}: '
                if auth_body and auth_body.find("p")
                else "Insira o código PIN que acabamos "
                     "de enviar para o seu e-mail: "
            )
            await self.awaiting_pin(data, input_message)
        # TODO: linear settle-delay before reading the post-login redirect,
        # not a counted retry. backoff_sleep(0) preserves ~1s pacing today;
        # a proper retry counter should be introduced if login form
        # submission grows transient-failure handling.
        await backoff_sleep(0)
        success = await self.success_login()
        return success

    async def success_login(self) -> tuple[bool, str]:
        if self.response is None:
            return False, "No response received."

        response_url = str(self.response.url)
        if "trade" in response_url:
            await self.get_settings()
            return True, "Login successful."

        soup = self.get_soup()

        not_available = soup.select_one(
            "#tab-1 > div > div.modal-sign__not-avalible__title"
        )
        if not_available:
            return False, (
                f"Service unavailable: {not_available.get_text(strip=True)}"
            )

        error = soup.select_one("#tab-1 form > div:nth-child(2) > div")
        msg = error.get_text(strip=True) if error else "Unknown error"

        return False, f"Login failed. {msg}"

    async def __call__(
            self,
            username: str,
            password: str,
            user_data_dir: str | None = None
    ) -> tuple[bool, str]:
        """Method to get Quotex API login http request.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str user_data_dir: The optional value for path userdata.
        :returns: The instance of: class:`httpx.Response`.
        """
        home = await self.get_sign_page()
        reason_msg = 'Access page with SSL RESOLVER'
        if not home.is_success:
            reason_msg = f'Error on access page: {home.reason_phrase}'

        print(reason_msg)

        data = {
            "_token": await self.get_token(),
            "email": username,
            "password": password,
            "remember": 1,

        }
        status, msg = await self._post(data)

        return status, msg
