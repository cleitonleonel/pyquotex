"""Pre-startup broker probe tests (audit 2026-05-14, Task 2).

Spec §3.1: before pyquotex burns OTP-supervisor retry budget on a
broker-side hard failure, hit ``qxbroker.com/en/sign-in`` once with
``curl_cffi`` and short-circuit on:

* 403 — Cloudflare fingerprint regression (current incident class)
* 5xx — broker upstream down

Network errors (timeout / connection refused) fall through to
pyquotex — the probe isn't conclusive in that case. 200 continues
silently.

The probe runs inside ``_do_connect:SETUP`` — the locked phase
from the Phase 3a lock-split (audit 2026-05-13 H2).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs


class _FakeResp:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self.content = body


@pytest.mark.asyncio
async def test_preflight_403_blocks_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §3.1 first bullet: a 403 from the sign-in page raises
    ``BrokerPreflightFailed``."""
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        BrokerPreflightFailed,
        QuotexManager,
    )

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=403, body=b"<html>cf</html>")),
    )

    with capture_logs() as logs, pytest.raises(BrokerPreflightFailed, match="cloudflare 403"):
        await mgr._preflight_check()

    assert any(
        r["event"] == "broker.preflight.cloudflare_403" for r in logs
    ), logs


@pytest.mark.asyncio
async def test_preflight_5xx_blocks_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §3.1 second bullet: 5xx raises ``BrokerPreflightFailed``."""
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        BrokerPreflightFailed,
        QuotexManager,
    )

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=503)),
    )

    with capture_logs() as logs, pytest.raises(BrokerPreflightFailed, match="503"):
        await mgr._preflight_check()

    assert any(
        r["event"] == "broker.preflight.upstream_5xx" for r in logs
    ), logs


@pytest.mark.asyncio
async def test_preflight_network_timeout_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §3.1 third bullet: a network-level error does NOT raise —
    pyquotex still gets to try. The log line
    ``broker.preflight.network_error`` is the breadcrumb."""
    import curl_cffi.requests as curl_requests  # noqa: PLC0415

    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    def _raise(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise curl_requests.RequestsError("connection timed out")

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get", _raise,
    )

    with capture_logs() as logs:
        await mgr._preflight_check()  # MUST NOT RAISE

    assert any(
        r["event"] == "broker.preflight.network_error" for r in logs
    ), logs


@pytest.mark.asyncio
async def test_preflight_200_continues_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: 200 logs ``broker.preflight.ok``."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=200, body=b"<html>ok</html>")),
    )

    with capture_logs() as logs:
        await mgr._preflight_check()

    assert any(r["event"] == "broker.preflight.ok" for r in logs), logs


@pytest.mark.asyncio
async def test_do_connect_aborts_on_preflight_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: pre-flight 403 inside _do_connect leaves the
    manager in ``error`` state with operator-readable last_error,
    and pyquotex.Quotex() is never even constructed."""
    from unittest.mock import MagicMock  # noqa: PLC0415

    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=403)),
    )
    sentinel_ctor = MagicMock(
        side_effect=AssertionError("Quotex() must not be called when preflight 403"),
    )
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", sentinel_ctor,
    )

    await mgr._do_connect()

    assert mgr._state == "error"
    assert "cloudflare 403" in (mgr._last_error or "").lower()
    sentinel_ctor.assert_not_called()
