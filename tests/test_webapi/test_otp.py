"""Tests for the two-step OTP / 2FA login flow.

Drives the route + OtpManager interaction with a custom
``fake_quotex.connect`` side_effect that mirrors what the real
``Quotex.connect()`` does on a fresh device — i.e. invokes the
``on_otp_callback`` and waits for a code before completing.
"""
from __future__ import annotations

import asyncio

import pytest


def _wire_otp_connect(fake_quotex, otp_manager, valid_code="123456"):
    """Replace ``fake_quotex.connect`` with a coroutine that simulates
    the broker's PIN flow.

    On entry it calls the OTP callback (which arms an async Future
    inside the manager). The HTTP route is expected to return 202
    while waiting. When ``/auth/otp`` resolves the Future, this
    coroutine validates the code and returns the final
    ``(connected, message)`` tuple.
    """
    fake_quotex.check_connect.return_value = False

    async def fake_connect():
        # Simulate the library's awaiting_pin step. Note we go
        # through the SAME OtpManager instance the route uses.
        code = await otp_manager.callback("Insira o código PIN")
        if code == valid_code:
            fake_quotex.check_connect.return_value = True
            return (True, "Connected")
        return (False, f"Login failed: bad code {code}")

    fake_quotex.connect.side_effect = fake_connect
    fake_quotex.connect.return_value = None  # side_effect wins


@pytest.mark.asyncio
async def test_connect_returns_202_when_broker_requests_otp(
        client, auth_headers, fake_quotex, otp_manager,
):
    """First step: ``POST /auth/connect`` should return 202 with
    ``otp_required: true`` once the broker has armed the callback."""
    _wire_otp_connect(fake_quotex, otp_manager)

    r = client.post("/auth/connect", headers=auth_headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["connected"] is False
    assert body["otp_required"] is True
    assert "PIN" in (body["otp_prompt"] or "")


@pytest.mark.asyncio
async def test_otp_submit_completes_connect(
        client, auth_headers, fake_quotex, otp_manager,
):
    """Second step: posting the correct PIN to ``/auth/otp`` resolves
    the pending callback and the route returns 200 connected."""
    _wire_otp_connect(fake_quotex, otp_manager)

    pre = client.post("/auth/connect", headers=auth_headers)
    assert pre.status_code == 202, pre.text

    r = client.post(
        "/auth/otp", headers=auth_headers,
        json={"code": "123456"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    assert body["connected"] is True
    assert body["balance"] == 10000.0
    assert body["nickname"] == "tester"


@pytest.mark.asyncio
async def test_otp_submit_502_on_invalid_code(
        client, auth_headers, fake_quotex, otp_manager,
):
    """Broker rejects the PIN → connect task resolves False →
    ``/auth/otp`` should bubble that as 502."""
    _wire_otp_connect(fake_quotex, otp_manager, valid_code="123456")

    pre = client.post("/auth/connect", headers=auth_headers)
    assert pre.status_code == 202

    r = client.post(
        "/auth/otp", headers=auth_headers,
        json={"code": "000000"},
    )
    assert r.status_code == 502
    assert "bad code" in r.json()["detail"]


def test_otp_submit_400_when_nothing_pending(client, auth_headers):
    """No prior ``/auth/connect`` → no callback armed → 400."""
    r = client.post(
        "/auth/otp", headers=auth_headers, json={"code": "123456"},
    )
    assert r.status_code == 400
    assert "no OTP currently pending" in r.json()["detail"].lower() \
        or "No OTP currently pending" in r.json()["detail"]


def test_otp_submit_empty_code_rejected(client, auth_headers):
    """Pydantic min_length=1 should already reject empty before the
    route runs, returning 422."""
    r = client.post("/auth/otp", headers=auth_headers, json={"code": ""})
    assert r.status_code == 422


def test_otp_endpoint_requires_auth(client):
    """The OTP endpoint is auth-gated like the rest."""
    r = client.post("/auth/otp", json={"code": "123456"})
    assert r.status_code == 401


def test_connect_502_on_broker_exception(
        client, auth_headers, fake_quotex, otp_manager,
):
    """A raw exception from ``client.connect()`` must surface as 502
    rather than 500. The previous implementation leaked Internal
    Server Error when the broker login flow raised — bug fix."""
    fake_quotex.check_connect.return_value = False

    async def boom():
        raise RuntimeError("HTTP 403: Forbidden")

    fake_quotex.connect.side_effect = boom

    r = client.post("/auth/connect", headers=auth_headers)
    assert r.status_code == 502, r.text
    assert "403" in r.json()["detail"] or "Forbidden" in r.json()["detail"]


def test_connect_already_connected_fast_path_skips_task(
        client, auth_headers, fake_quotex, otp_manager,
):
    """When ``check_connect()`` returns True the route returns 200
    immediately without spawning a task or calling ``connect()``."""
    fake_quotex.check_connect.return_value = True

    r = client.post("/auth/connect", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["otp_required"] is False
    fake_quotex.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_otp_re_prompt_on_invalid_format(
        client, auth_headers, fake_quotex, otp_manager,
):
    """If the broker re-prompts (the library does this internally
    when the code is non-digits), the OTP manager re-arms and
    ``/auth/otp`` should surface that as a 400 telling the caller to
    re-submit."""
    fake_quotex.check_connect.return_value = False

    re_prompted = asyncio.Event()

    async def fake_connect_with_retry():
        # First prompt
        code = await otp_manager.callback("Insira o PIN")
        # Library's awaiting_pin recurses on non-digits.
        if not code.isdigit():
            re_prompted.set()
            code = await otp_manager.callback("Insira o PIN (retry)")
        if code == "123456":
            fake_quotex.check_connect.return_value = True
            return (True, "Connected")
        return (False, "Login failed.")

    fake_quotex.connect.side_effect = fake_connect_with_retry

    pre = client.post("/auth/connect", headers=auth_headers)
    assert pre.status_code == 202

    r = client.post(
        "/auth/otp", headers=auth_headers, json={"code": "abcdef"},
    )
    # The library re-prompted; the route returns 400 telling caller
    # to resubmit the correct numeric code.
    assert r.status_code == 400
    assert "re-prompt" in r.json()["detail"].lower() or \
        "invalid format" in r.json()["detail"].lower()

    # And submitting the correct code now completes the flow.
    r2 = client.post(
        "/auth/otp", headers=auth_headers, json={"code": "123456"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["connected"] is True
