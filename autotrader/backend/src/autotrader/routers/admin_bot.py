"""REST shim around the Admin Telegram Bot.

Two endpoints:

* ``GET /admin-bot/status`` — used by the dashboard to render the
  "Admin bot offline / running / error" badge.
* ``POST /admin-bot/unbind`` — escape hatch when the operator can no
  longer access the bound Telegram account; clears ``admin_telegram_user_id``
  so the next ``/start`` from any account re-binds.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.db import get_session
from autotrader.models.base import utc_now
from autotrader.models.settings import GlobalSettings
from autotrader.routers.auth import require_auth

router = APIRouter(prefix="/admin-bot", tags=["admin-bot"])


class StatusResponse(BaseModel):
    state: str
    bound_user_id: int | None
    last_error: str | None


class UnbindResponse(BaseModel):
    bound_user_id: None = None


@router.get("/status", response_model=StatusResponse)
async def status_endpoint(
    request: Request,
    _: None = Depends(require_auth),
) -> StatusResponse:
    bot = request.app.state.admin_bot
    s = bot.status()
    return StatusResponse(
        state=s.state,
        bound_user_id=s.bound_user_id,
        last_error=s.last_error,
    )


@router.post("/unbind", response_model=UnbindResponse)
async def unbind_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_auth),
) -> UnbindResponse:
    """Clear ``admin_telegram_user_id`` from the persisted settings row
    AND from the in-memory ``AdminBot`` instance. Both must move
    together — otherwise the next /start from any user is rejected as
    "bound to another admin" because the in-memory copy is stale."""
    gs = await session.get(GlobalSettings, 1)
    if gs is None:
        gs = GlobalSettings(id=1)
        session.add(gs)
    gs.admin_telegram_user_id = None
    gs.updated_at = utc_now()
    await session.commit()

    bot = request.app.state.admin_bot
    bot.set_bound_user_id(None)
    return UnbindResponse()
