from ..http.navigator import Browser


class Settings(Browser):

    def __init__(self, api):
        super().__init__()
        self.set_headers()
        self.api = api
        self.headers = self.get_headers()

    def get_settings(self):
        self.headers["content-type"] = "application/json"
        self.headers["referer"] = f"{self.api.https_url}/{self.api.lang}/trade"
        self.headers["cookie"] = self.api.session_data["cookies"]
        self.headers["user-agent"] = self.api.session_data["user_agent"]
        response = self.send_request(
            "GET",
            f"{self.api.https_url}/api/v1/cabinets/digest"
        )
        return response.json()

    def set_time_offset(self, time_offset):
        payload = {
            "time_offset": time_offset
        }
        self.headers["referer"] = f"{self.api.https_url}/{self.api.lang}/trade"
        self.headers["cookie"] = self.api.session_data["cookies"]
        self.headers["user-agent"] = self.api.session_data["user_agent"]
        response = self.send_request(
            method="POST",
            url=f"{self.api.https_url}/api/v1/user/profile/time_offset",
            json=payload
        )

        return response.json()