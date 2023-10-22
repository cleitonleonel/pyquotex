import random
import requests
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from quotexapi.http.user_agents import agents

retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504, 104],
    allowed_methods=["HEAD", "POST", "PUT", "GET", "OPTIONS"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
user_agent_list = agents.split("\n")


class Browser(object):
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    def __init__(self, api):
        self.api = api
        self.response = None
        self.headers = self.get_headers()
        self.api.user_agent = self.headers["User-Agent"]

    def get_headers(self):
        self.headers = {
            "User-Agent": user_agent_list[random.randint(0, len(user_agent_list) - 1)],
        }
        return self.headers

    def get_soup(self):
        return BeautifulSoup(self.response.content, "html.parser")

    def send_request(self, method, url, **kwargs):
        self.response = self.session.request(method, url, headers=self.headers, **kwargs)
        if not self.response:
            self.session = cloudscraper.create_scraper()
            self.response = self.session.request(method, url, headers=self.headers, **kwargs)
        return self.response
