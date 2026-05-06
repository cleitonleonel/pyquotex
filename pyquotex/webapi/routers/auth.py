"""Auth + connection lifecycle endpoints.

Two-step flow when the broker requires a PIN (first login from a new
device/IP):

1. ``POST /auth/connect``
   Starts ``Quotex.connect()`` as a background task. Waits up to
   ``settings.connect_otp_grace`` seconds for the task to finish or
   for the broker to ask for a PIN.

   * Already connected → ``200`` with profile + balance.
   * Connect completed during the grace window → ``200`` (or ``502``
     on broker error).
   * Broker is waiting for the PIN → ``202`` with
     ``otp_required: true`` and the broker's prompt text.
   * Still connecting after the grace window → ``202`` with
     ``otp_required: false`` and ``message="connecting…"``; poll
     ``/health`` or call ``/auth/connect`` again.

2. ``POST /auth/otp`` ``{"code": "123456"}``
   Hands the PIN to the waiting callback and joins on the connect
   task. Returns the final result (``200`` connected / ``502`` /
   ``504``).

Most accounts won't see the PIN flow — once a session token is
cached in ``session.json`` the broker skips the password login on
subsequent connects.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..auth import verify_api_key
from ..deps import get_client, get_otp_manager, get_settings
from ..models import ConnectResponse, OtpRequest, OtpResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(verify_api_key)])


async def _profile_payload(client) -> dict[str, Any]:
    """Best-effort hydration of profile + balance for the response.
    Failures don't fail the connect — the dedicated endpoints can
    refetch."""
    out: dict[str, Any] = {}
    try:
        balance = await client.get_balance()
        out["balance"] = float(balance) if balance is not None else None
    except Exception:
        pass
    try:
        profile = await client.get_profile()
        out["profile_id"] = getattr(profile, "profile_id", None)
        out["nickname"] = getattr(profile, "nick_name", None)
        out["currency"] = getattr(profile, "currency_code", None)
    except Exception:
        pass
    return out


def _connect_task(request: Request) -> asyncio.Task | None:
    return getattr(request.app.state, "connect_task", None)


def _set_connect_task(request: Request, task: asyncio.Task | None) -> None:
    request.app.state.connect_task = task


def _resolve_task_result(
        task: asyncio.Task,
) -> tuple[bool | None, str | None]:
    """Return ``(ok, reason)`` for a completed task. ``None`` means
    the task raised; the caller should map that to a 502."""
    try:
        result = task.result()
    except asyncio.CancelledError:
        return None, "connect cancelled"
    except Exception as e:
        logger.warning("connect() raised: %r", e)
        return None, f"{type(e).__name__}: {e}"
    if isinstance(result, tuple) and len(result) == 2:
        ok, reason = result
        return bool(ok), str(reason) if reason is not None else ""
    return None, f"unexpected connect() result: {result!r}"


async def _build_connected_response(
        client, message: str
) -> ConnectResponse:
    payload = await _profile_payload(client)
    return ConnectResponse(
        connected=True,
        message=message,
        otp_required=False,
        otp_prompt=None,
        **payload,
    )


@router.post(
    "/connect",
    response_model=ConnectResponse,
    summary="Warm the broker WebSocket; surfaces OTP requirement if any",
    responses={
        200: {"description": "Connected"},
        202: {"description": "OTP required OR connect still in progress"},
        502: {"description": "Broker rejected the connect"},
    },
)
async def connect_endpoint(
        request: Request,
        response: Response,
        client = Depends(get_client),
        settings = Depends(get_settings),
        otp_mgr = Depends(get_otp_manager),
) -> ConnectResponse:
    # Fast path: already connected.
    if await client.check_connect():
        return await _build_connected_response(client, "Already connected.")

    task = _connect_task(request)

    # An in-flight connect task exists.
    if task is not None and not task.done():
        if otp_mgr.is_waiting:
            response.status_code = status.HTTP_202_ACCEPTED
            return ConnectResponse(
                connected=False,
                message="OTP required",
                otp_required=True,
                otp_prompt=otp_mgr.prompt,
            )
        # Wait the grace window for either OTP-arming or completion.
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=settings.connect_otp_grace,
            )
        except asyncio.TimeoutError:
            if otp_mgr.is_waiting:
                response.status_code = status.HTTP_202_ACCEPTED
                return ConnectResponse(
                    connected=False,
                    message="OTP required",
                    otp_required=True,
                    otp_prompt=otp_mgr.prompt,
                )
            response.status_code = status.HTTP_202_ACCEPTED
            return ConnectResponse(
                connected=False, message="Still connecting; retry shortly.",
            )
        # Task finished within the grace window — fall through to
        # the result-handling below.

    # No in-flight task, or the previous one finished. Inspect any
    # stale result before launching a fresh attempt.
    if task is not None and task.done():
        ok, reason = _resolve_task_result(task)
        if ok:
            return await _build_connected_response(client, reason or "Connected.")
        # Failed previous attempt — clear and let user retry below.
        _set_connect_task(request, None)
        otp_mgr.reset()

    # Spawn a fresh connect.
    client.set_account_mode(settings.account_mode)
    new_task = asyncio.create_task(_safe_connect(client))
    _set_connect_task(request, new_task)

    try:
        await asyncio.wait_for(
            asyncio.shield(new_task), timeout=settings.connect_otp_grace,
        )
    except asyncio.TimeoutError:
        if otp_mgr.is_waiting:
            response.status_code = status.HTTP_202_ACCEPTED
            return ConnectResponse(
                connected=False,
                message="OTP required",
                otp_required=True,
                otp_prompt=otp_mgr.prompt,
            )
        response.status_code = status.HTTP_202_ACCEPTED
        return ConnectResponse(
            connected=False, message="Connect started; check /health or retry.",
        )

    # Connect finished during the grace window.
    ok, reason = _resolve_task_result(new_task)
    if ok is True:
        return await _build_connected_response(client, reason or "Connected.")
    # Either ok=False (broker rejection) or ok=None (exception).
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Broker connect failed: {reason}",
    )


@router.post(
    "/otp",
    response_model=OtpResponse,
    summary="Submit the broker-emailed PIN to complete a pending connect",
    responses={
        200: {"description": "PIN accepted; connection completed"},
        400: {"description": "No OTP currently pending"},
        502: {"description": "Broker rejected the PIN or connect"},
        504: {"description": "Connect did not complete in time"},
    },
)
async def submit_otp_endpoint(
        body: OtpRequest,
        request: Request,
        client = Depends(get_client),
        otp_mgr = Depends(get_otp_manager),
) -> OtpResponse:
    if not otp_mgr.is_waiting:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No OTP currently pending. Call POST /auth/connect first; "
                "if the broker requests a PIN it will return 202 with "
                "otp_required=true."
            ),
        )
    code = body.code.strip()
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty OTP code.",
        )
    if not otp_mgr.submit(code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP submit failed (already resolved or absent).",
        )

    task = _connect_task(request)
    if task is None:
        # Shouldn't happen: OTP was waiting but no task tracked. Surface
        # as 502 rather than hang.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OTP accepted but no connect task is tracked.",
        )

    # Wait up to 20 s for the broker to finish the login, polling
    # ``otp_mgr.is_waiting`` so a re-prompt (the library recurses
    # ``awaiting_pin`` when the supplied code isn't all digits)
    # surfaces as 400 — the caller should resubmit a corrected code
    # — instead of waiting out the full grace window.
    deadline = asyncio.get_running_loop().time() + 20.0
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="OTP submitted but connect did not complete in 20 s.",
            )
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=min(remaining, 0.1),
            )
            break  # task completed
        except asyncio.TimeoutError:
            if otp_mgr.is_waiting:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Broker re-prompted for OTP — likely invalid format. "
                        "Re-submit the correct numeric code."
                    ),
                )
            # otherwise loop and keep waiting

    ok, reason = _resolve_task_result(task)
    if ok is True:
        payload = await _profile_payload(client)
        return OtpResponse(
            accepted=True,
            connected=True,
            message=reason or "Connected.",
            **payload,
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Broker connect failed after OTP: {reason}",
    )


async def _safe_connect(client) -> tuple[bool, str]:
    """Wrap ``Quotex.connect`` so it always returns a ``(bool, str)``
    tuple even if the underlying coroutine raises. Lets the connect-
    endpoint use ``task.result()`` uniformly without the route having
    to decide what an exception means."""
    try:
        ok, reason = await client.connect()
        return bool(ok), str(reason) if reason is not None else ""
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("connect() raised inside task")
        return False, f"{type(e).__name__}: {e}"
