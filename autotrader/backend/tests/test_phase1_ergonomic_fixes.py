"""Phase 1 ergonomic fix tests (audit 2026-05-13).

* M1 — Telegram MTProto calls in the login flow are now wrapped in
  ``asyncio.wait_for`` so a hung peer becomes a bounded
  ``TelegramManagerError`` instead of a silent stall.
* M4 — ``routers/feed.py`` now catches the specific auth-rejection
  classes instead of bare ``Exception``; this test verifies the
  narrowed-catch behaviour by feeding a real-shape invalid token.
* L7 — ``QuotexManager.status()`` escapes the OTP prompt at the
  boundary; rendering-context bugs in any consumer become inert.

Heavy imports stay inside each test function to dodge the import-graph
side-effect captured in test_phase0_instrumentation.py's docstring.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from structlog.testing import capture_logs


# ---------------------------------------------------------------------------
# M1 — Telegram login timeouts
# ---------------------------------------------------------------------------


class _HangingTelegramClient:
    """Pyrogram client double whose ``connect`` and ``send_code`` await
    forever (a ``Future`` no-one ever resolves). Exercises the
    ``asyncio.wait_for`` deadline added in Phase 1."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._hang: asyncio.Future[None] | None = None

    async def connect(self) -> None:
        # Hold the call until the test gives up via wait_for.
        self._hang = asyncio.get_running_loop().create_future()
        await self._hang

    async def send_code(self, phone: str) -> Any:  # pragma: no cover
        self._hang = asyncio.get_running_loop().create_future()
        await self._hang

    async def disconnect(self) -> None:
        if self._hang is not None and not self._hang.done():
            self._hang.cancel()


@pytest.mark.asyncio
async def test_begin_login_times_out_on_hung_mtproto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung ``client.connect()`` must surface as a
    ``TelegramManagerError`` with the timeout message — not stall the
    coroutine forever. Phase 0 added the soft-warning timer; this is
    the bounded-failure behaviour."""
    from autotrader.services.telegram_manager import (  # noqa: PLC0415
        TelegramManager,
        TelegramManagerError,
    )

    mgr = TelegramManager()
    monkeypatch.setattr(mgr, "_new_client", lambda: _HangingTelegramClient())
    # Shrink the deadline so the test runs fast — semantics unchanged.
    monkeypatch.setattr(mgr, "_LOGIN_TIMEOUT_SECONDS", 0.1, raising=False)

    with capture_logs() as logs, pytest.raises(TelegramManagerError) as excinfo:
        await mgr.begin_login("+15551234567")

    assert "timed out" in str(excinfo.value).lower()
    timeouts = [r for r in logs if r["event"] == "telegram.send_code.timeout"]
    assert len(timeouts) == 1, logs
    # State machine must be in ``error``, not stuck in ``awaiting_code``.
    assert mgr.status().state == "error"


# ---------------------------------------------------------------------------
# M4 — feed.ws narrowed except + auth log
# ---------------------------------------------------------------------------


def test_feed_ws_narrowed_except_still_logs_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bogus token continues to emit ``feed.ws.auth_rejected`` even
    after Phase 1 narrowed the ``except`` clause — the audit's concern
    was a *bare* ``except Exception`` swallowing every error class
    silently; the narrowed catch still handles the legitimate cases."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", FakeQuotex,
    )

    from autotrader.main import app  # noqa: PLC0415

    with capture_logs() as logs, TestClient(app) as c:
        try:
            with c.websocket_connect("/feed/ws?token=not-a-real-token"):
                pass
        except Exception:
            pass

    rejects = [r for r in logs if r["event"] == "feed.ws.auth_rejected"]
    assert rejects, logs
    # The exception that fires today is ``HTTPException`` (raised by
    # ``_decode`` after Fernet's ``InvalidToken``); assert the narrowed
    # catch handled it correctly.
    assert rejects[0]["reason"] in {"HTTPException", "InvalidToken"}


# ---------------------------------------------------------------------------
# L7 — html.escape OTP prompt
# ---------------------------------------------------------------------------


def test_otp_prompt_html_escaped_in_status() -> None:
    """``QuotexManager.status()`` must escape HTML-significant chars in
    the OTP prompt — defence against any consumer that ever decides to
    render this string inside an HTML context."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    # Drive the manager into ``awaiting_otp`` with an HTML-bearing prompt.
    mgr._state = "awaiting_otp"  # type: ignore[assignment]
    mgr._otp_prompt = "Enter PIN <b>now</b> & confirm"

    snapshot = mgr.status()

    # Angle brackets and ampersand must be escaped.
    assert snapshot.otp_prompt is not None
    assert "<b>" not in snapshot.otp_prompt
    assert "&lt;b&gt;" in snapshot.otp_prompt
    assert "&amp;" in snapshot.otp_prompt


def test_otp_prompt_none_when_not_awaiting() -> None:
    """No prompt leaked out when the manager is in any other state —
    the Phase 1 refactor preserved the original gating logic."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr._otp_prompt = "leftover from a previous cycle"
    # _state defaults to "idle".
    assert mgr.status().otp_prompt is None
