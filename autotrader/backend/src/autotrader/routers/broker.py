"""Broker (Quotex) endpoints.

All routes require a valid bearer token. The ``Manager``/``Session``
dependencies pull the singleton ``QuotexManager`` and a per-request DB
session, respectively.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
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
    QuotexManagerError,
)

router = APIRouter(prefix="/broker", tags=["broker"], dependencies=[Depends(require_auth)])

# Auth dependency injected at the router level above; we still take a
# ``Principal`` in handlers that want to log the caller. Most handlers
# don't need it.
_Auth = Annotated[Principal, Depends(require_auth)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    configured: bool
    connected: bool
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


class ConnectResponse(BaseModel):
    connected: bool
    detail: str


class BalanceResponse(BaseModel):
    balance: float
    account_mode: AccountMode


class OkResponse(BaseModel):
    ok: Literal[True] = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
async def status_endpoint(manager: ManagerDep) -> StatusResponse:
    s = manager.status()
    return StatusResponse(
        configured=s.configured,
        connected=s.connected,
        email_masked=s.email_masked,
        account_mode=s.account_mode,
        connected_at=s.connected_at,
        last_error=s.last_error,
    )


@router.put("/credentials", response_model=OkResponse)
async def put_credentials(
    body: CredentialsRequest,
    session: SessionDep,
    manager: ManagerDep,
) -> OkResponse:
    """Store / replace the broker credentials.

    Disconnects the manager if it was connected so the next ``connect``
    uses the freshly-saved values.
    """
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
async def connect_endpoint(manager: ManagerDep, session: SessionDep) -> ConnectResponse:
    if not manager.configured:
        # Manager loses its in-memory creds across container restarts;
        # fall back to the DB before refusing.
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
        ok, detail = await manager.connect()
    except QuotexManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )
    return ConnectResponse(connected=True, detail=detail)


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

    # Persist the choice so it survives container restarts.
    creds = await load_credentials(session)
    if creds is not None and creds.account_mode != body.mode:
        creds.account_mode = body.mode
        await session.commit()

    s = manager.status()
    return StatusResponse(
        configured=s.configured,
        connected=s.connected,
        email_masked=s.email_masked,
        account_mode=s.account_mode,
        connected_at=s.connected_at,
        last_error=s.last_error,
    )


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
