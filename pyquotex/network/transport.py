"""Pluggable HTTP transport backends for the Browser client.

The default is :class:`HttpxBackend` (matches existing behaviour).
:class:`CurlCffiBackend` is an optional drop-in that uses curl_cffi to
emit a real Chrome/Firefox TLS (JA3/JA4) and HTTP-2 fingerprint, which
is the only reliable way to get past modern bot-detection layers when a
Multilogin profile or rotating-residential proxy is in use.

To enable curl_cffi support::

    pip install pyquotex[stealth]

…then build the client with ``ProxyConfig(use_browser_tls=True, ...)``.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class HTTPResponse(Protocol):
    """Minimal subset of ``httpx.Response`` used by the Browser."""

    status_code: int
    content: bytes
    text: str
    url: Any
    headers: Any
    reason_phrase: str

    def json(self) -> Any: ...


class TransportBackend(Protocol):
    async def request(
            self, method: str, url: str, **kwargs: Any
    ) -> HTTPResponse: ...

    async def aclose(self) -> None: ...

    @property
    def is_closed(self) -> bool: ...

    @property
    def cookies(self) -> Any: ...


class HttpxBackend:
    """Default backend wrapping ``httpx.AsyncClient``.

    Existing behaviour is preserved exactly — every callsite that used
    to talk to ``httpx.AsyncClient`` still does.
    """

    def __init__(
            self,
            *,
            verify: Any = True,
            timeout: float = 30.0,
            follow_redirects: bool = True,
            proxy: str | None = None,
    ):
        self._client = httpx.AsyncClient(
            verify=verify,
            timeout=timeout,
            follow_redirects=follow_redirects,
            proxy=proxy,
        )

    async def request(
            self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        return await self._client.request(method, url, **kwargs)

    async def aclose(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    @property
    def cookies(self) -> Any:
        return self._client.cookies


class CurlCffiBackend:
    """Backend using ``curl_cffi`` for browser-impersonating TLS.

    Requires the optional ``stealth`` extra. The constructor will raise
    :class:`ImportError` if ``curl_cffi`` is not installed; callers
    should fall back to :class:`HttpxBackend` in that case.
    """

    def __init__(
            self,
            *,
            verify: Any = True,
            timeout: float = 30.0,
            follow_redirects: bool = True,
            proxy: str | None = None,
            impersonate: str = "chrome120",
    ):
        try:
            from curl_cffi.requests import AsyncSession  # type: ignore
        except ImportError as e:  # pragma: no cover - tested via mock
            raise ImportError(
                "curl_cffi is not installed. Install pyquotex with the "
                "'stealth' extra: pip install 'pyquotex[stealth]'"
            ) from e

        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}

        self._session = AsyncSession(
            verify=verify if not isinstance(verify, ssl.SSLContext) else True,
            timeout=timeout,
            allow_redirects=follow_redirects,
            proxies=proxies,
            impersonate=impersonate,
        )
        self._closed = False

    async def request(
            self, method: str, url: str, **kwargs: Any
    ) -> Any:
        # Map httpx kwargs to curl_cffi shape.
        if "follow_redirects" in kwargs:
            kwargs["allow_redirects"] = kwargs.pop("follow_redirects")
        return await self._session.request(method, url, **kwargs)

    async def aclose(self) -> None:
        if self._closed:
            return
        try:
            # ``AsyncSession.close`` is async in curl_cffi >= 0.7 but
            # was sync in some earlier 0.6.x releases — handle both.
            result = self._session.close()
            if asyncio.iscoroutine(result):
                await result
        finally:
            self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def cookies(self) -> Any:
        return self._session.cookies


def build_backend(
        *,
        verify: Any,
        timeout: float,
        follow_redirects: bool,
        proxy: str | None,
        use_browser_tls: bool,
        impersonate: str = "chrome120",
) -> TransportBackend:
    """Pick a transport based on the ``use_browser_tls`` flag.

    Falls back to :class:`HttpxBackend` with a warning if curl_cffi is
    requested but not installed — this keeps existing scripts working
    even when the optional extra is missing.
    """
    if use_browser_tls:
        try:
            backend = CurlCffiBackend(
                verify=verify,
                timeout=timeout,
                follow_redirects=follow_redirects,
                proxy=proxy,
                impersonate=impersonate,
            )
            logger.info(
                "pyquotex.transport: curl_cffi backend (impersonate=%s)",
                impersonate,
            )
            return backend
        except ImportError as e:
            logger.warning(
                "use_browser_tls requested but %s; falling back to httpx.",
                e,
            )
    logger.info("pyquotex.transport: httpx backend")
    return HttpxBackend(
        verify=verify,
        timeout=timeout,
        follow_redirects=follow_redirects,
        proxy=proxy,
    )
