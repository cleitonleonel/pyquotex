from ..http.navigator import Browser


class Settings(Browser):

    def __init__(self, api):
        super().__init__()
        self.set_headers()
        self.api = api
        self.headers = self.get_headers()

    def get_settings(self):
        self.headers["content-type"] = "application/json"
        self.headers["Referer"] = f"https://qxbroker.com/{self.api.lang}/trade"
        self.headers["cookie"] = self.api.session_data["cookies"]
        self.headers["User-Agent"] = self.api.session_data["user_agent"]
        response = self.send_request("GET",
                                     "https://qxbroker.com/api/v1/cabinets/digest"
                                     )
        return response.json()
