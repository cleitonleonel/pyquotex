"""Rejection-probe tests (audit 2026-05-14, Task 3).

Spec §3.2: when pyquotex's ``client.connect()`` returns a rejection
(either ``(False, reason)`` or a raised exception), emit a single
``broker.connect.rejection_probe`` log line capturing pyquotex
client state. No behavior change — pure observation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs


class _FakeState:
    auth_status = 0          # not AuthStatus.AUTHENTICATED
    SSID = None


class _FakePyqApi:
    state = _FakeState()
    wss_url = "wss://ws2.qxbroker.com/socket.io/?EIO=4&transport=websocket"


class _FakePyqClient:
    api = _FakePyqApi()

    def __init__(self, *_a, **_kw) -> None:
        pass

    async def connect(self):  # type: ignore[no-untyped-def]
        return False, "Websocket connection rejected."

    def set_account_mode(self, *_a, **_kw) -> None:
        pass


@pytest.mark.asyncio
async def test_rejection_probe_fires_when_pyquotex_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``client.connect()`` returns ``(False, reason)``, the
    probe fires with raw_error=reason, ssid_loaded=False, and the
    impersonate profile."""
    from autotrader.config import settings  # noqa: PLC0415
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        QuotexManager, "_preflight_check", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", _FakePyqClient,
    )

    with capture_logs() as logs:
        await mgr._do_connect()

    probes = [r for r in logs if r["event"] == "broker.connect.rejection_probe"]
    assert len(probes) == 1, logs
    p = probes[0]
    assert "Websocket connection rejected" in str(p["raw_error"])
    assert p["ssid_loaded"] is False
    assert "elapsed_ms" in p
    assert p["impersonate_profile"] == settings.broker_curl_cffi_profile
    assert "auth_status" in p
    assert "ws_url" in p and p["ws_url"] is not None and "qxbroker" in p["ws_url"]


@pytest.mark.asyncio
async def test_rejection_probe_silent_on_successful_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean connect must NOT emit a probe."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    class _OkClient(_FakePyqClient):
        async def connect(self):  # type: ignore[no-untyped-def, override]
            return True, "ok"

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]
    monkeypatch.setattr(
        QuotexManager, "_preflight_check", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", _OkClient,
    )

    with capture_logs() as logs:
        await mgr._do_connect()

    assert [
        r for r in logs if r["event"] == "broker.connect.rejection_probe"
    ] == []
