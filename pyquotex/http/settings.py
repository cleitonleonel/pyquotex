from typing import Any

from ..http.navigator import Browser


class Settings(Browser):

    def __init__(self, api: Any):
        super().__init__()
        self.set_headers()
        self.api = api
        self.headers: dict[str, str] = self.get_headers()

    async def get_settings(self) -> dict[str, Any]:
        self.headers["content-type"] = "application/json"
        self.headers["referer"] = (
            f"{self.api.https_url}/{self.api.lang}/trade"
        )
        self.headers["cookie"] = self.api.session_data.get("cookies", "")
        self.headers["user-agent"] = self.api.session_data.get(
            "user_agent", ""
        )
        response = await self.send_request(
            "GET",
            f"{self.api.https_url}/api/v1/cabinets/digest"
        )
        return response.json()

    async def set_time_offset(self, time_offset: int) -> dict[str, Any]:
        payload = {
            "time_offset": time_offset
        }
        self.headers["referer"] = f"{self.api.https_url}/{self.api.lang}/trade"
        self.headers["cookie"] = self.api.session_data.get("cookies", "")
        self.headers["user-agent"] = self.api.session_data.get(
            "user_agent", ""
        )
        response = await self.send_request(
            method="POST",
            url=f"{self.api.https_url}/api/v1/user/profile/time_offset",
            json=payload
        )

        return response.json()