import ssl
import logging
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504, 104],
    allowed_methods=["HEAD", "POST", "PUT", "GET", "OPTIONS"]
)

logger = logging.getLogger("Browser")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(handler)


class CipherSuiteAdapter(HTTPAdapter):
    __attrs__ = [
        'ssl_context',
        'max_retries',
        'config',
        '_pool_connections',
        '_pool_maxsize',
        '_pool_block',
        'source_address'
    ]

    def __init__(self, *args, **kwargs):
        self.ssl_context = kwargs.pop('ssl_context', None)
        self.cipherSuite = kwargs.pop('cipherSuite', None)
        self.source_address = kwargs.pop('source_address', None)
        self.server_hostname = kwargs.pop('server_hostname', None)
        self.ecdhCurve = kwargs.pop('ecdhCurve', 'prime256v1')

        if self.source_address:
            if isinstance(self.source_address, str):
                self.source_address = (self.source_address, 0)
            if not isinstance(self.source_address, tuple):
                raise TypeError("source_address deve ser uma string IP ou tupla (ip, porta)")

        if not self.ssl_context:
            self.ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            self.ssl_context.orig_wrap_socket = self.ssl_context.wrap_socket
            self.ssl_context.wrap_socket = self.wrap_socket

            if self.server_hostname:
                self.ssl_context.server_hostname = self.server_hostname

            self.ssl_context.set_ciphers(self.cipherSuite)
            self.ssl_context.set_ecdh_curve(self.ecdhCurve)
            self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3

        super().__init__(**kwargs)

    def wrap_socket(self, *args, **kwargs):
        if hasattr(self.ssl_context, 'server_hostname') and self.ssl_context.server_hostname:
            kwargs['server_hostname'] = self.ssl_context.server_hostname
            self.ssl_context.check_hostname = False
        else:
            self.ssl_context.check_hostname = True
        return self.ssl_context.orig_wrap_socket(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = self.ssl_context
        kwargs['source_address'] = self.source_address
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs['ssl_context'] = self.ssl_context
        kwargs['source_address'] = self.source_address
        return super().proxy_manager_for(*args, **kwargs)


class Browser(Session):

    def __init__(self, *args, **kwargs):
        self.response = None
        self.default_headers = None
        self.ecdhCurve = kwargs.pop('ecdhCurve', 'prime256v1')
        self.cipherSuite = kwargs.pop('cipherSuite', 'DEFAULT@SECLEVEL=1')
        self.source_address = kwargs.pop('source_address', None)
        self.server_hostname = kwargs.pop('server_hostname', None)
        self.ssl_context = kwargs.pop('ssl_context', None)
        self.proxies = kwargs.pop('proxies', None)
        self.debug = kwargs.pop('debug', False)

        super().__init__(*args, **kwargs)

        self.headers.update(self.get_headers())

        self.mount(
            'https://',
            CipherSuiteAdapter(
                ecdhCurve=self.ecdhCurve,
                cipherSuite=self.cipherSuite,
                server_hostname=self.server_hostname,
                source_address=self.source_address,
                ssl_context=self.ssl_context,
                max_retries=retry_strategy
            )
        )

        if self.debug:
            logger.setLevel(logging.DEBUG)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self):
        self.__enter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)

    def get_headers(self):
        self.default_headers = {
            "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) "
                          "Gecko/20100101 Firefox/119.0"
        }
        return self.default_headers

    def set_headers(self, headers=None):
        self.headers.update(self.default_headers)
        if headers:
            self.headers.update(headers)

    def get_cookies(self):
        return '; '.join(f'{i.name}={i.value}' for i in self.cookies)

    def get_soup(self):
        if not self.response:
            raise RuntimeError("No response stored. Use send_request() first.")
        return BeautifulSoup(self.response.content, "html.parser")

    def get_json(self):
        if not self.response:
            raise RuntimeError("No response stored.")
        try:
            return self.response.json()
        except Exception:
            return None

    def send_request(self, method, url, headers=None, **kwargs):
        merged_headers = self.headers.copy()
        if headers:
            merged_headers.update(headers)

        if self.proxies:
            kwargs['proxies'] = self.proxies

        self.response = self.request(
            method,
            url,
            headers=merged_headers,
            **kwargs
        )

        if self.debug:
            logger.debug(f"→ {method} {url}")
            logger.debug(f"Status: {self.response.status_code}")
            logger.debug(f"Headers enviados: {merged_headers}")
            logger.debug(f"Headers recebidos: {dict(self.response.headers)}")
            logger.debug(f"Cookies: {self.get_cookies()}")
            content_preview = self.response.text[:250].strip().replace('\n', '')
            logger.debug(f"Body (preview): {content_preview} [...]")

        return self.response
