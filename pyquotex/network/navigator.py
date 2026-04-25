"""Async HTTP browser client using httpx for Quotex API communication."""
import logging
import ssl
from typing import Any

import certifi
import httpx
from bs4 import BeautifulSoup
from typing_extensions import Self

logger = logging.getLogger("Browser")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(handler)


class Browser:
    """Async HTTP client wrapping httpx.AsyncClient with TLS, cookies, 
    and proxy support."""

    def __init__(self, *args: Any, **kwargs: Any):
        self.response: httpx.Response | None = None
        self.default_headers: dict[str, str] | None = None
        self.source_address: Any = kwargs.pop('source_address', None)
        self.server_hostname: str | None = kwargs.pop('server_hostname', None)
        self.proxies: dict[str, str] | str | None = kwargs.pop('proxies', None)
        self.debug: bool = kwargs.pop('debug', False)

        # Build SSL context
        cert_path = certifi.where()
        self._ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        self._ssl_context.load_verify_locations(cert_path)
        self._ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3

        if self.server_hostname:
            self._ssl_context.check_hostname = False

        self.headers: dict[str, str] = self.get_headers()

        # Build httpx.AsyncClient
        self._client = httpx.AsyncClient(
            verify=self._ssl_context,
            timeout=30.0,
            follow_redirects=True,
            proxy=self.proxies if isinstance(self.proxies, str) else None,
        )

        if self.debug:
            logger.setLevel(logging.DEBUG)

    def __enter__(self) -> Self:
        return self

    def __exit__(
            self, exc_type: Any, exc_val: Any, exc_tb: Any
    ) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
            self, exc_type: Any, exc_val: Any, exc_tb: Any
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def get_headers(self) -> dict[str, str]:
        self.default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) "
                "Gecko/20100101 Firefox/119.0"
            )
        }
        return dict(self.default_headers)

    def set_headers(self, headers: dict[str, str] | None = None) -> None:
        if self.default_headers:
            self.headers.update(self.default_headers)
        if headers:
            self.headers.update(headers)

    def get_cookies(self) -> str:
        """Get cookies as semicolon-separated string from the httpx 
        client jar."""
        return '; '.join(
            f'{name}={value}'
            for name, value in self._client.cookies.items()
        )

    def get_soup(self) -> BeautifulSoup:
        """Parse the last response content with BeautifulSoup."""
        if self.response and self.response.status_code >= 400:
            raise RuntimeError(
                f"HTTP {self.response.status_code}: "
                f"{self.response.reason_phrase}"
            )
        return BeautifulSoup(
            self.response.content if self.response else b"",
            "html.parser"
        )

    def get_json(self) -> Any:
        """Parse last response as JSON."""
        if self.response and self.response.status_code >= 400:
            raise RuntimeError(
                f"HTTP {self.response.status_code}: "
                f"{self.response.reason_phrase}"
            )
        try:
            return self.response.json() if self.response else None
        except Exception:
            return None

    async def send_request(
            self,
            method: str,
            url: str,
            headers: dict[str, str] | None = None,
            **kwargs: Any
    ) -> httpx.Response:
        """Send an async HTTP request using httpx.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Target URL
            headers: Optional additional headers
            **kwargs: Additional httpx request arguments (data, json, 
                      params, etc.)

        Returns:
            httpx.Response object
        """
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)

        logger.debug("Using proxies: %s", self.proxies)

        self.response = await self._client.request(
            method,
            url,
            headers=merged_headers,
            **kwargs,
        )

        if self.debug:
            logger.debug(f"→ {method} {url}")
            logger.debug(f"Status: {self.response.status_code}")
            logger.debug(f"Headers enviados: {merged_headers}")
            logger.debug(f"Headers recebidos: {dict(self.response.headers)}")
            logger.debug(f"Cookies: {self.get_cookies()}")
            content_preview = (
                self.response.text[:250].strip().replace('\n', '')
            )
            logger.debug(f"Body (preview): {content_preview} [...]")

        return self.response
