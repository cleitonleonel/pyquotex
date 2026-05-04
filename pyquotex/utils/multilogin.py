"""Multilogin integration for browser fingerprint isolation.

Multilogin (https://multilogin.com) exposes a local agent and a cloud
API that can launch isolated browser profiles, each with its own
fingerprint, cookies, proxy, and User-Agent. This module wraps the
relevant endpoints so a Quotex client can run behind a profile and
inherit its proxy + user agent automatically.

Two transports are supported:

* ``v1`` – the legacy local agent on ``http://127.0.0.1:35000``. No
  bearer token is required.
* ``v3`` – the cloud API at ``https://api.multilogin.com`` with a
  short-lived bearer token obtained via ``/user/signin``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .proxy_config import ProxyConfig

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_URL = "http://127.0.0.1:35000"
DEFAULT_CLOUD_URL = "https://api.multilogin.com"


@dataclass
class MultiloginConfig:
    """Configuration for a Multilogin browser profile.

    Attributes:
        profile_id: UUID of an existing Multilogin profile.
        folder_id: Folder/workspace ID (required by the v3 cloud API).
        token: Bearer token for the v3 API. Ignored when ``api`` is ``v1``.
        api: ``"v1"`` (local agent) or ``"v3"`` (cloud).
        base_url: Override the default API URL.
        auto_start: Start the profile automatically on connect.
        auto_stop: Stop the profile automatically on close.
    """

    profile_id: str
    folder_id: str | None = None
    token: str | None = None
    api: str = "v3"
    base_url: str | None = None
    auto_start: bool = True
    auto_stop: bool = True
    extra_launch_args: dict[str, Any] = field(default_factory=dict)

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        return (
            DEFAULT_LOCAL_URL if self.api == "v1" else DEFAULT_CLOUD_URL
        )


@dataclass
class MultiloginProfile:
    """Runtime state of a started Multilogin profile."""

    profile_id: str
    user_agent: str | None = None
    proxy_url: str | None = None
    cdp_endpoint: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_proxy_config(self) -> ProxyConfig | None:
        if not self.proxy_url:
            return None
        return ProxyConfig(url=self.proxy_url)


class MultiloginClient:
    """Async client for the Multilogin agent / cloud API."""

    def __init__(self, config: MultiloginConfig, timeout: float = 30.0):
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.resolved_base_url(),
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "MultiloginClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.config.api == "v3" and self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        return headers

    async def start_profile(self) -> MultiloginProfile:
        """Start the profile and return its proxy + user-agent details."""
        cfg = self.config
        if cfg.api == "v1":
            url = f"/api/v2/profile/start?automation=true&profileId={cfg.profile_id}"
            resp = await self._client.get(url, headers=self._headers())
        else:
            if not cfg.folder_id:
                raise ValueError(
                    "Multilogin v3 API requires folder_id to start a profile."
                )
            url = (
                f"/profile/f/{cfg.folder_id}/p/{cfg.profile_id}"
                "/start?automation_type=selenium"
            )
            resp = await self._client.get(url, headers=self._headers())

        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body) if isinstance(body, dict) else {}

        proxy_url = self._extract_proxy(data)
        return MultiloginProfile(
            profile_id=cfg.profile_id,
            user_agent=data.get("user_agent") or data.get("ua"),
            proxy_url=proxy_url,
            cdp_endpoint=(
                data.get("cdp_url")
                or data.get("ws_endpoint")
                or data.get("automation_url")
            ),
            raw=body if isinstance(body, dict) else {},
        )

    async def stop_profile(self) -> None:
        cfg = self.config
        try:
            if cfg.api == "v1":
                url = f"/api/v1/profile/stop/p/{cfg.profile_id}"
            else:
                url = f"/profile/stop/p/{cfg.profile_id}"
            await self._client.get(url, headers=self._headers())
        except Exception as e:
            logger.warning("Multilogin stop_profile failed: %s", e)

    @staticmethod
    def _extract_proxy(data: dict[str, Any]) -> str | None:
        proxy = data.get("proxy") or {}
        if isinstance(proxy, str):
            return proxy
        if not isinstance(proxy, dict):
            return None

        host = proxy.get("host") or proxy.get("address")
        port = proxy.get("port")
        if not host or not port:
            return None
        scheme = (proxy.get("type") or "http").lower()
        user = proxy.get("username") or proxy.get("user")
        pwd = proxy.get("password") or proxy.get("pass")
        auth = f"{user}:{pwd}@" if user and pwd else ""
        return f"{scheme}://{auth}{host}:{port}"
