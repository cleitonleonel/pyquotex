"""Broker (Quotex) endpoints.

The connect flow is non-blocking by design — pyquotex sometimes needs
an OTP / 2FA code, and we don't want a single HTTP request to block
for 30+ seconds while the user types it.

Flow:
  1. ``PUT /broker/credentials`` — store email + password (encrypted).
  2. ``POST /broker/connect``     — schedules a background connect.
                                    Returns 200 if it settled inside
                                    the brief grace window, else 202
                                    with a ``state`` the frontend
                                    polls on.
  3. ``GET /broker/status``       — frontend polls; if state is
                                    ``awaiting_otp`` it shows the
                                    code-entry UI.
  4. ``POST /broker/otp``         — submits the user-entered code,
                                    unparking the connect coroutine.
  5. ``POST /broker/cancel``      — abort an in-flight connect.

All routes require a valid bearer token.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from autotrader.auth import Principal, require_auth
from autotrader.dependencies import ManagerDep, SessionDep
from autotrader.models.broker_credentials import (
    delete_credentials,
    load_credentials,
    upsert_credentials,
)
from autotrader.services.quotex_manager import (
    AccountMode,
    ConnectState,
    QuotexManagerError,
)

router = APIRouter(prefix="/broker", tags=["broker"], dependencies=[Depends(require_auth)])

_Auth = Annotated[Principal, Depends(require_auth)]

# How long ``POST /broker/connect`` will wait for a no-OTP login to
# settle before returning 202 and letting the frontend poll. 1.5s
# covers the warm-WS path with comfortable headroom.
_CONNECT_WAIT_SECONDS = 1.5


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    configured: bool
    connected: bool
    state: ConnectState
    awaiting_otp: bool
    otp_prompt: str | None = None
    email_masked: str | None = None
    account_mode: AccountMode
    connected_at: datetime | None = None
    last_error: str | None = None


class CredentialsRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)
    account_mode: AccountMode = "PRACTICE"


class AccountModeRequest(BaseModel):
    mode: AccountMode


class OTPRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=12)


class ConnectResponse(BaseModel):
    connected: bool
    state: ConnectState
    detail: str
    otp_prompt: str | None = None


class BalanceResponse(BaseModel):
    balance: float
    account_mode: AccountMode


class AssetsResponse(BaseModel):
    assets: list[str]
    count: int


class OkResponse(BaseModel):
    ok: Literal[True] = True


def _status_payload(manager: ManagerDep) -> StatusResponse:
    s = manager.status()
    return StatusResponse(
        configured=s.configured,
        connected=s.connected,
        state=s.state,
        awaiting_otp=s.awaiting_otp,
        otp_prompt=s.otp_prompt,
        email_masked=s.email_masked,
        account_mode=s.account_mode,
        connected_at=s.connected_at,
        last_error=s.last_error,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
async def status_endpoint(manager: ManagerDep) -> StatusResponse:
    return _status_payload(manager)


@router.put("/credentials", response_model=OkResponse)
async def put_credentials(
    body: CredentialsRequest,
    session: SessionDep,
    manager: ManagerDep,
) -> OkResponse:
    """Store / replace the broker credentials."""
    try:
        manager._enforce_live_gate(body.account_mode)
    except QuotexManagerError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await upsert_credentials(session, body.email, body.password, body.account_mode)
    if manager.connected:
        await manager.disconnect()
    manager.set_credentials(body.email, body.password, body.account_mode)
    return OkResponse()


@router.delete("/credentials", response_model=OkResponse)
async def delete_credentials_endpoint(
    session: SessionDep,
    manager: ManagerDep,
) -> OkResponse:
    if manager.connected:
        await manager.disconnect()
    manager.clear_credentials()
    await delete_credentials(session)
    return OkResponse()


@router.post("/connect", response_model=ConnectResponse)
async def connect_endpoint(
    response: Response,
    manager: ManagerDep,
    session: SessionDep,
) -> ConnectResponse:
    """Kick off a connect attempt.

    Returns 200 on the no-OTP fast path (login completed within the
    grace window) and 202 otherwise — the frontend then polls
    ``/broker/status`` and submits via ``/broker/otp`` when needed.
    """
    if not manager.configured:
        creds = await load_credentials(session)
        if creds is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no broker credentials configured",
            )
        manager.set_credentials(
            creds.email(),
            creds.password(),
            creds.account_mode if creds.account_mode in ("PRACTICE", "REAL") else "PRACTICE",
        )

    try:
        manager.begin_connect()
    except QuotexManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    # Give the fast path a brief window so non-OTP logins feel
    # synchronous. OTP-bearing logins block on the user inside this
    # window and bail out via state==awaiting_otp.
    await manager.wait_settled(_CONNECT_WAIT_SECONDS)

    s = manager.status()

    if s.state == "connected":
        return ConnectResponse(
            connected=True,
            state="connected",
            detail="connected",
        )
    if s.state == "error":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=s.last_error or "connect failed",
        )

    response.status_code = status.HTTP_202_ACCEPTED
    return ConnectResponse(
        connected=False,
        state=s.state,
        detail=(
            "broker requested an OTP — submit it via POST /broker/otp"
            if s.state == "awaiting_otp"
            else "still connecting — poll GET /broker/status"
        ),
        otp_prompt=s.otp_prompt,
    )


@router.post("/otp", response_model=StatusResponse)
async def submit_otp_endpoint(
    body: OTPRequest,
    manager: ManagerDep,
) -> StatusResponse:
    try:
        await manager.submit_otp(body.code)
    except QuotexManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    # Same brief settle so the response usually reflects the final
    # state (connected / error) rather than the optimistic
    # "connecting" flip.
    await manager.wait_settled(_CONNECT_WAIT_SECONDS)
    return _status_payload(manager)


@router.post("/cancel", response_model=OkResponse)
async def cancel_endpoint(manager: ManagerDep) -> OkResponse:
    await manager.cancel_connect()
    return OkResponse()


@router.post("/disconnect", response_model=OkResponse)
async def disconnect_endpoint(manager: ManagerDep) -> OkResponse:
    await manager.disconnect()
    return OkResponse()


@router.post("/account-mode", response_model=StatusResponse)
async def set_account_mode_endpoint(
    body: AccountModeRequest,
    manager: ManagerDep,
    session: SessionDep,
) -> StatusResponse:
    try:
        await manager.set_account_mode(body.mode)
    except QuotexManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    creds = await load_credentials(session)
    if creds is not None and creds.account_mode != body.mode:
        creds.account_mode = body.mode
        await session.commit()

    return _status_payload(manager)


@router.get("/assets", response_model=AssetsResponse)
async def assets_endpoint(
    manager: ManagerDep,
    refresh: bool = False,
) -> AssetsResponse:
    """Broker's asset universe.

    Returns the cached snapshot the manager populated at connect time.
    Pass ``?refresh=true`` to force-pull a fresh list (only meaningful
    while connected).
    """
    if refresh and manager.connected:
        try:
            await manager.refresh_assets()
        except QuotexManagerError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
    codes = list(manager.assets)
    return AssetsResponse(assets=codes, count=len(codes))


@router.get("/balance", response_model=BalanceResponse)
async def balance_endpoint(manager: ManagerDep) -> BalanceResponse:
    if not manager.connected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="broker is not connected",
        )
    try:
        bal = await manager.get_balance(timeout=10)
    except QuotexManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return BalanceResponse(balance=bal, account_mode=manager.status().account_mode)
