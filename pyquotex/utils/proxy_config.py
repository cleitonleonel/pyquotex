"""Proxy and DNS configuration for HTTP and WebSocket transports.

Supports HTTP, HTTPS, and SOCKS5 proxies and per-host DNS overrides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse


@dataclass
class ProxyConfig:
    """Proxy / DNS configuration shared by HTTP and WebSocket clients.

    Attributes:
        url: Full proxy URL, e.g. ``http://user:pass@proxy.host:8080``
            or ``socks5://user:pass@proxy.host:1080``.
        verify_ssl: Whether to verify the upstream SSL certificate.
        dns_overrides: Mapping of ``hostname`` to ``ip`` used to bypass
            the system resolver. The original hostname is preserved in
            the ``Host`` header / TLS SNI to keep certificates valid.
        extra_headers: Extra headers to send on every HTTP request.
    """

    url: str | None = None
    verify_ssl: bool = True
    dns_overrides: dict[str, str] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    use_browser_tls: bool = False
    impersonate: str = "chrome120"

    @property
    def scheme(self) -> str | None:
        if not self.url:
            return None
        return urlparse(self.url).scheme.lower()

    @property
    def is_socks(self) -> bool:
        return bool(self.scheme and self.scheme.startswith("socks"))

    def httpx_proxy(self) -> str | None:
        """Return value usable as ``httpx.AsyncClient(proxy=...)``."""
        return self.url

    def websockets_proxy(self) -> str | None:
        """Return value usable by ``websockets.connect(proxy=...)``.

        ``websockets>=13`` accepts a proxy URL directly. For older versions
        callers should fall back to a CONNECT tunnel transport.
        """
        return self.url

    def resolve_url(self, url: str) -> tuple[str, dict[str, str]]:
        """Apply DNS overrides to a URL.

        Returns the (possibly rewritten) URL and any extra headers needed
        to keep the original ``Host`` so TLS SNI and routing stay correct.
        """
        if not self.dns_overrides:
            return url, {}

        parsed = urlparse(url)
        host = parsed.hostname
        if not host or host not in self.dns_overrides:
            return url, {}

        ip = self.dns_overrides[host]
        netloc = ip if not parsed.port else f"{ip}:{parsed.port}"
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            netloc = f"{auth}@{netloc}"

        rewritten = urlunparse(parsed._replace(netloc=netloc))
        return rewritten, {"Host": host}

    def merge_headers(
            self, headers: dict[str, str] | None = None
    ) -> dict[str, str]:
        merged = dict(self.extra_headers)
        if headers:
            merged.update(headers)
        return merged

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProxyConfig | None":
        """Build a ProxyConfig from a plain dict (e.g. from JSON config)."""
        if not data:
            return None
        return cls(
            url=data.get("url") or data.get("proxy"),
            verify_ssl=bool(data.get("verify_ssl", True)),
            dns_overrides=dict(data.get("dns_overrides") or {}),
            extra_headers=dict(data.get("extra_headers") or {}),
            use_browser_tls=bool(data.get("use_browser_tls", False)),
            impersonate=str(data.get("impersonate", "chrome120")),
        )
