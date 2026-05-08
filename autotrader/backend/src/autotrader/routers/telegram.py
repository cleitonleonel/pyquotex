"""Telegram (Pyrogram) endpoints.

The login flow has up to three HTTP turns: phone -> code -> 2FA
password (when enabled). Each turn corresponds to a state transition
in :class:`TelegramManager`; the frontend polls ``GET /telegram/status``
(or, more efficiently, reads the response payload of the last call)
to know what to render next.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from autotrader.auth import require_auth
from autotrader.dependencies import SessionDep, TelegramDep
from autotrader.models.telegram_session import (
    delete_session,
    upsert_session,
)
from autotrader.models.watched_channel import (
    list_watched,
    remove_watch,
    upsert_watch,
)
from autotrader.services.telegram_manager import (
    LoginState,
    TelegramManagerError,
)

router = APIRouter(
    prefix="/telegram",
    tags=["telegram"],
    dependencies=[Depends(require_auth)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    state: LoginState
    logged_in: bool
    awaiting_code: bool
    awaiting_password: bool
    phone_masked: str | None = None
    user_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    last_error: str | None = None


class LoginRequest(BaseModel):
    phone: str = Field(..., min_length=4, max_length=20)


class CodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=12)


class PasswordRequest(BaseModel):
    password: str = Field(..., min_length=1)


class DialogResponse(BaseModel):
    chat_id: int
    title: str
    chat_type: str
    username: str | None = None
    members_count: int | None = None
    is_verified: bool = False
    watched: bool = False


class WatchRequest(BaseModel):
    chat_id: int
    title: str = Field(..., min_length=1)
    chat_type: str = Field(..., min_length=1)
    username: str | None = None
    enabled: bool = True


class MessageResponse(BaseModel):
    id: int
    text: str
    sender_id: int
    date: datetime | None = None


class OkResponse(BaseModel):
    ok: Literal[True] = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_payload(manager: TelegramDep) -> StatusResponse:
    s = manager.status()
    return StatusResponse(
        state=s.state,
        logged_in=s.logged_in,
        awaiting_code=s.awaiting_code,
        awaiting_password=s.awaiting_password,
        phone_masked=s.phone_masked,
        user_id=s.user_id,
        username=s.username,
        first_name=s.first_name,
        last_error=s.last_error,
    )


# ---------------------------------------------------------------------------
# Status / Login flow
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
async def status_endpoint(manager: TelegramDep) -> StatusResponse:
    return _status_payload(manager)


@router.post("/login", response_model=StatusResponse)
async def login_endpoint(
    body: LoginRequest,
    manager: TelegramDep,
) -> StatusResponse:
    try:
        await manager.begin_login(body.phone.strip())
    except TelegramManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _status_payload(manager)


@router.post("/code", response_model=StatusResponse)
async def code_endpoint(
    body: CodeRequest,
    manager: TelegramDep,
    session: SessionDep,
) -> StatusResponse:
    try:
        await manager.submit_code(body.code.strip())
    except TelegramManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if manager.logged_in:
        await _persist_after_login(manager, session)
    return _status_payload(manager)


@router.post("/password", response_model=StatusResponse)
async def password_endpoint(
    body: PasswordRequest,
    manager: TelegramDep,
    session: SessionDep,
) -> StatusResponse:
    try:
        await manager.submit_password(body.password)
    except TelegramManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if manager.logged_in:
        await _persist_after_login(manager, session)
    return _status_payload(manager)


@router.post("/cancel", response_model=OkResponse)
async def cancel_endpoint(manager: TelegramDep) -> OkResponse:
    await manager.cancel_login()
    return OkResponse()


@router.post("/logout", response_model=OkResponse)
async def logout_endpoint(
    manager: TelegramDep,
    session: SessionDep,
) -> OkResponse:
    await manager.logout()
    await delete_session(session)
    return OkResponse()


# ---------------------------------------------------------------------------
# Dialogs (channels / groups)
# ---------------------------------------------------------------------------


@router.get("/dialogs", response_model=list[DialogResponse])
async def dialogs_endpoint(
    manager: TelegramDep,
    session: SessionDep,
    q: str | None = None,
    limit: int = 200,
) -> list[DialogResponse]:
    if not manager.logged_in:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="not logged in to telegram",
        )
    if limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be between 1 and 1000",
        )

    try:
        dialogs = await manager.list_dialogs(query=q, limit=limit)
    except TelegramManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    watched = {w.chat_id for w in await list_watched(session) if w.enabled}
    return [
        DialogResponse(
            chat_id=d.chat_id,
            title=d.title,
            chat_type=d.chat_type,
            username=d.username,
            members_count=d.members_count,
            is_verified=d.is_verified,
            watched=d.chat_id in watched,
        )
        for d in dialogs
    ]


# ---------------------------------------------------------------------------
# Watch list
# ---------------------------------------------------------------------------


@router.get("/watched", response_model=list[DialogResponse])
async def watched_endpoint(session: SessionDep) -> list[DialogResponse]:
    return [
        DialogResponse(
            chat_id=w.chat_id,
            title=w.title,
            chat_type=w.chat_type,
            username=w.username,
            members_count=None,
            is_verified=False,
            watched=w.enabled,
        )
        for w in await list_watched(session)
    ]


@router.post("/watch", response_model=OkResponse)
async def watch_endpoint(
    body: WatchRequest,
    session: SessionDep,
) -> OkResponse:
    await upsert_watch(
        session,
        chat_id=body.chat_id,
        title=body.title,
        chat_type=body.chat_type,
        username=body.username,
        enabled=body.enabled,
    )
    return OkResponse()


@router.delete("/watch/{chat_id}", response_model=OkResponse)
async def unwatch_endpoint(
    chat_id: int,
    session: SessionDep,
) -> OkResponse:
    await remove_watch(session, chat_id)
    return OkResponse()


# ---------------------------------------------------------------------------
# Recent messages (sample fodder for the parser builder)
# ---------------------------------------------------------------------------


@router.get("/messages", response_model=list[MessageResponse])
async def messages_endpoint(
    chat_id: int,
    manager: TelegramDep,
    session: SessionDep,
    limit: int = 20,
) -> list[MessageResponse]:
    """Recent messages from a watched chat — useful sample fodder when
    designing a regex/template in the parser builder.

    Restricted to chats the user has explicitly watched so the dashboard
    doesn't accidentally expose private DMs.
    """
    if not manager.logged_in:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="not logged in to telegram",
        )
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be between 1 and 100",
        )

    watched = {w.chat_id for w in await list_watched(session)}
    if chat_id not in watched:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="chat is not in the watched list",
        )

    try:
        msgs = await manager.recent_messages(chat_id, limit=limit)
    except TelegramManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return [MessageResponse(**m) for m in msgs]


# ---------------------------------------------------------------------------
# Internal: persist the Pyrogram session after a successful login
# ---------------------------------------------------------------------------


async def _persist_after_login(
    manager: TelegramDep,
    session: SessionDep,
) -> None:
    """Encrypt the Pyrogram session string and save the row."""
    # ``_persist_session`` above stripped stars from the masked phone
    # for storage — here we want the raw phone the user typed. The
    # manager keeps it as ``_phone``; status().phone_masked is for the
    # UI. We expose the raw via a small accessor below.
    string = await manager.export_session_string()
    s = manager.status()
    await upsert_session(
        session,
        phone=manager._phone or "",
        session_string=string,
        user_id=s.user_id,
        username=s.username,
        first_name=s.first_name,
    )
