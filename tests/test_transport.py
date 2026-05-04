"""Tests for the pluggable HTTP transport backends."""
import sys
from unittest.mock import MagicMock

import pytest

from pyquotex.network.transport import (
    CurlCffiBackend,
    HttpxBackend,
    build_backend,
)


def test_httpx_backend_constructs_with_proxy():
    backend = HttpxBackend(
        verify=True,
        timeout=10.0,
        follow_redirects=True,
        proxy="http://p:8080",
    )
    assert backend.is_closed is False
    assert backend.cookies is not None


@pytest.mark.asyncio
async def test_httpx_backend_close_is_idempotent():
    backend = HttpxBackend()
    await backend.aclose()
    assert backend.is_closed is True
    await backend.aclose()  # should not raise


def test_build_backend_returns_httpx_by_default():
    backend = build_backend(
        verify=True,
        timeout=10.0,
        follow_redirects=True,
        proxy=None,
        use_browser_tls=False,
    )
    assert isinstance(backend, HttpxBackend)


def test_build_backend_falls_back_to_httpx_when_curl_cffi_missing(monkeypatch):
    """If curl_cffi isn't installed, build_backend should warn and use httpx."""
    # Simulate import failure for curl_cffi.
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", None)

    backend = build_backend(
        verify=True,
        timeout=10.0,
        follow_redirects=True,
        proxy=None,
        use_browser_tls=True,
    )
    assert isinstance(backend, HttpxBackend)


def test_curl_cffi_backend_raises_when_lib_missing(monkeypatch):
    """The class itself raises ImportError when curl_cffi is unavailable."""
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", None)
    with pytest.raises(ImportError):
        CurlCffiBackend()


def test_curl_cffi_backend_uses_session_when_lib_present(monkeypatch):
    """When curl_cffi imports cleanly, the backend wraps an AsyncSession."""
    fake_session = MagicMock()
    fake_module = MagicMock()
    fake_requests = MagicMock()
    fake_requests.AsyncSession = MagicMock(return_value=fake_session)
    fake_module.requests = fake_requests

    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_requests)

    backend = CurlCffiBackend(
        verify=True,
        timeout=10.0,
        follow_redirects=True,
        proxy="http://p:8080",
        impersonate="chrome120",
    )
    assert backend._session is fake_session
    fake_requests.AsyncSession.assert_called_once()
    call_kwargs = fake_requests.AsyncSession.call_args.kwargs
    assert call_kwargs["impersonate"] == "chrome120"
    assert call_kwargs["proxies"] == {
        "http": "http://p:8080", "https": "http://p:8080"
    }
