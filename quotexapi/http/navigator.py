import random
from http import cookiejar as cookielib
from quotexapi.http.retry import Retry
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, \
    install_opener, build_opener, HTTPCookieProcessor
from bs4 import BeautifulSoup

user_agent_list = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Mozilla/5.0 (Android 13; Mobile; LG-M255; rv:110.0) Gecko/110.0 Firefox/110.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.53 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Windows; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.114 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_5) AppleWebKit/603.3.8 (KHTML, like Gecko) Version/10.1.2 Safari/603.3.8",
    "Mozilla/5.0 (Windows NT 10.0; Windows; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.114 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.53 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Windows; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.114 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.5060.53 Safari/537.36"
]


class Session(object):
    cookies = cookielib.LWPCookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))

    def __init__(self):
        self.openrate = None
        install_opener(self.opener)

    @Retry(URLError, tries=4, delay=3, backoff=2)
    def request(self, method, url, data=None, **kwargs):
        req = Request(
            url,
            urlencode(data).encode("utf-8") if data else data,
            method=method,
            **kwargs
        )
        return self.opener.open(req)


class Browser(object):

    def __init__(self, api):
        self.api = api
        self.response = None
        self.headers = self.get_headers()
        self.session = Session()
        self.api.user_agent = self.headers["User-Agent"]

    def get_headers(self):
        self.headers = {
            "User-Agent": user_agent_list[random.randint(0, len(user_agent_list) - 1)]
        }
        return self.headers

    def get_soup(self):
        return BeautifulSoup(
            self.response.read(),
            "html.parser")

    def send_request(self, method, url, **kwargs):
        return self.session.request(
            method,
            url,
            headers=self.headers,
            **kwargs)
