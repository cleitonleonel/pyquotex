import re
import json
from pathlib import Path
from quotexapi.http.navigator import Browser


class Login(Browser):
    """Class for Quotex login resource."""

    url = ""
    cookies = None
    ssid = None
    base_url = 'qxbroker.com'
    https_base_url = f'https://{base_url}'

    def get_token(self):
        self.headers["referer"] = f"{self.https_base_url}/pt/trade"
        self.response = self.send_request(
            "GET",
            f"{self.https_base_url}/pt/sign-in/"
        )
        self.cookies = '; '.join(['%s=%s' % (i.name, i.value)
                                  for i in self.session.cookies])
        return self.get_soup().find(
            "input", {"name": "_token"})["value"]

    def awaiting_pin(self, data):
        self.headers["Content-Type"] = "application/x-www-form-urlencoded"
        data["keep_code"] = 1
        data["code"] = int(
            input("Insira o código PIN que acabamos de enviar para o seu e-mail: "))
        self.send_request(method="POST",
                          url=f"{self.https_base_url}/pt/sign-in/",
                          data=data)

    def get_profile(self):
        self.response = self.send_request(method="GET",
                                          url=f"{self.https_base_url}/pt/trade")
        if self.response:
            script = self.get_soup().find_all(
                "script", {"type": "text/javascript"})[1].get_text()
            match = re.sub(
                "window.settings = ", "", script.strip().replace(";", ""))
            self.ssid = json.loads(match).get("token")
            output_file = Path("./session.json")
            output_file.parent.mkdir(exist_ok=True, parents=True)
            output_file.write_text(
                json.dumps({"cookies": self.cookies, "ssid": self.ssid, "user_agent": self.api.user_agent}, indent=4)
            )
            return self.response, json.loads(match)
        return None, None

    def _get(self):
        return self.send_request(method="GET",
                                 url=f"f{self.https_base_url}/pt/trade")

    def _post(self, data):
        """Send get request for Quotex API login http resource.
        :returns: The instance of :class:`requests.Response`.
        """
        self.headers["Content-Type"] = "application/x-www-form-urlencoded"
        self.response = self.send_request(method="POST",
                                          url=f"{self.https_base_url}/pt/sign-in/",
                                          data=data)
        if "Insira o código PIN que acabamos de enviar para o seu e-mail" \
                in self.get_soup().get_text():
            self.awaiting_pin(data)
        result_data = self.get_profile()
        return result_data

    def __call__(self, username, password):
        """Method to get Quotex API login http request.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :returns: The instance of :class:`requests.Response`.
        """
        data = {
            "_token": self.get_token(),
            "email": username,
            "password": password,
            "remember": 1,

        }
        response, self.api.profile.msg = self._post(data=data)
        return self.ssid, self.cookies
