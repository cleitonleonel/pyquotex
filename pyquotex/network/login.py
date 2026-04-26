import asyncio
import inspect
import re
import sys
from typing import Any

from pyquotex.config import update_session
from pyquotex.network.navigator import Browser
from pyquotex.utils import json_utils as json


class Login(Browser):
    """Class for Quotex login resource."""

    url: str = ""
    cookies: str | None = None
    ssid: str | None = None
    base_url: str = 'qxbroker.com'
    https_base_url: str = f'https://{base_url}'

    def __init__(self, api: Any, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.api = api
        self.html: Any = None
        self.headers: dict[str, str] = self.get_headers()
        self.full_url: str = f"{self.https_base_url}/{api.lang}"

    async def get_token(self) -> str | None:
        self.headers["Connection"] = "keep-alive"
        self.headers["Accept-Encoding"] = "gzip, deflate, br"
        self.headers["Accept-Language"] = (
            "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3"
        )
        self.headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        )
        self.headers["Referer"] = f"{self.full_url}/sign-in"
        self.headers["Upgrade-Insecure-Requests"] = "1"
        self.headers["Sec-Ch-Ua-Mobile"] = "?0"
        self.headers["Sec-Ch-Ua-Platform"] = '"Linux"'
        self.headers["Sec-Fetch-Site"] = "same-origin"
        self.headers["Sec-Fetch-User"] = "?1"
        self.headers["Sec-Fetch-Dest"] = "document"
        self.headers["Sec-Fetch-Mode"] = "navigate"
        self.headers["Dnt"] = "1"
        await self.send_request(
            "GET",
            f"{self.full_url}/sign-in/modal/"
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
        self.headers["Referer"] = f"{self.full_url}/sign-in/modal"
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

        await asyncio.sleep(1)
        await self.send_request(
            method="POST",
            url=f"{self.full_url}/sign-in/modal",
            data=data
        )

    async def get_profile(self) -> tuple[Any, dict[str, Any] | None]:
        self.response = await self.send_request(
            method="GET",
            url=f"{self.full_url}/trade"
        )
        if self.response:
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

        return None, None

    async def _get(self) -> Any:
        return await self.send_request(
            method="GET",
            url=f"{self.full_url}/trade"
        )

    async def _post(self, data: dict[str, Any]) -> tuple[bool, str]:
        """Send post-request for Quotex API login http resource.
        :returns: The instance of: class:`httpx.Response`.
        """
        self.response = await self.send_request(
            method="POST",
            url=f"{self.full_url}/sign-in/",
            data=data
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
        await asyncio.sleep(1)
        success = self.success_login()
        return success

    def success_login(self) -> tuple[bool, str]:
        if self.response is None:
            return False, "No response received."

        response_url = str(self.response.url)
        if "trade" in response_url:
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
        data = {
            "_token": await self.get_token(),
            "email": username,
            "password": password,
            "remember": 1,

        }
        status, msg = await self._post(data)
        if status:
            await self.get_profile()

        return status, msg
