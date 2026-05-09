"""Execution-pipeline endpoints — status, master toggle, trade history."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from autotrader.auth import require_auth
from autotrader.config import settings as env_settings
from autotrader.dependencies import ManagerDep, PipelineDep, SessionDep, TelegramDep
from autotrader.models.parser_config import list_configs as list_parser_configs
from autotrader.models.settings import GlobalSettings
from autotrader.models.trade_attempt import TradeAttempt, list_recent
from autotrader.models.watched_channel import list_watched

router = APIRouter(
    prefix="/pipeline",
    tags=["pipeline"],
    dependencies=[Depends(require_auth)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PipelineStatus(BaseModel):
    active: bool
    kill_switch_engaged: bool
    live_trading_enabled_env: bool
    broker_connected: bool
    telegram_logged_in: bool
    watched_chat_count: int
    enabled_parser_count: int
    cached_parser_count: int
    # Telegram-side health gauges. ``last_message_received_at`` is
    # ``None`` when no message has flowed through ``_handle_incoming``
    # since the process started; the dashboard treats that as "no
    # signal yet" rather than an error. ``subscribed_chat_count`` is
    # how many of the ``watched_chat_count`` channels survived the
    # post-prime ``get_chat_history`` touch — a mismatch surfaces the
    # subscription bug we fixed in ``_prime_peer_cache``.
    last_message_received_at: datetime | None = None
    subscribed_chat_count: int = 0


class ParserDecisionResponse(BaseModel):
    """One pipeline-dispatch decision exposed to the dashboard.

    Mirrors the shape published on the WS bus as ``pipeline.decision``
    payloads. ``parser_config_id`` / ``parser_name`` / ``parser_type``
    are nullable for chat-level decisions (e.g. ``no_configs`` —
    message arrived in a chat with no bound parser).
    """

    ts: datetime
    chat_id: int
    parser_config_id: int | None = None
    parser_name: str | None = None
    parser_type: str | None = None
    outcome: Literal[
        "matched", "no_match", "build_failed",
        "no_configs", "pipeline_inactive",
    ]
    reasons: list[str] = []
    signals: int = 0
    text_preview: str = ""


class ActivateRequest(BaseModel):
    active: bool


class TradeAttemptResponse(BaseModel):
    id: int
    chat_id: int
    parser_config_id: int
    asset: str
    asset_raw: str
    direction: str
    duration_seconds: int
    stake: float
    trade_mode: str
    fire_at: datetime | None = None
    status: str
    broker_order_id: str | None = None
    profit: float | None = None
    error: str | None = None
    received_at: datetime
    placed_at: datetime | None = None
    settled_at: datetime | None = None


class OkResponse(BaseModel):
    ok: Literal[True] = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_create_settings(session: SessionDep) -> GlobalSettings:
    row = await session.get(GlobalSettings, 1)
    if row is None:
        row = GlobalSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def _to_response(row: TradeAttempt) -> TradeAttemptResponse:
    return TradeAttemptResponse(
        id=row.id or 0,
        chat_id=row.chat_id,
        parser_config_id=row.parser_config_id,
        asset=row.asset,
        asset_raw=row.asset_raw,
        direction=row.direction,
        duration_seconds=row.duration_seconds,
        stake=row.stake,
        trade_mode=row.trade_mode,
        fire_at=row.fire_at,
        status=row.status,
        broker_order_id=row.broker_order_id,
        profit=row.profit,
        error=row.error,
        received_at=row.received_at,
        placed_at=row.placed_at,
        settled_at=row.settled_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=PipelineStatus)
async def status_endpoint(
    session: SessionDep,
    manager: ManagerDep,
    telegram: TelegramDep,
    pipeline: PipelineDep,
) -> PipelineStatus:
    s = await _get_or_create_settings(session)
    watched = await list_watched(session)
    configs = await list_parser_configs(session)
    return PipelineStatus(
        active=s.pipeline_active,
        kill_switch_engaged=s.kill_switch_engaged,
        live_trading_enabled_env=env_settings.live_trading_enabled,
        broker_connected=manager.connected,
        telegram_logged_in=telegram.logged_in,
        watched_chat_count=sum(1 for w in watched if w.enabled),
        enabled_parser_count=sum(1 for c in configs if c.enabled),
        cached_parser_count=len(pipeline._parsers),
        last_message_received_at=telegram.last_message_at,
        subscribed_chat_count=telegram.subscribed_chat_count,
    )


@router.post("/activate", response_model=PipelineStatus)
async def activate_endpoint(
    body: ActivateRequest,
    session: SessionDep,
    manager: ManagerDep,
    telegram: TelegramDep,
    pipeline: PipelineDep,
) -> PipelineStatus:
    """Flip the master execution switch.

    Even when active, individual safety gates still apply (kill
    switch, REAL-mode env flag, parser ``enabled`` flag). Activation
    is rejected when the broker isn't connected or Telegram isn't
    logged in — those are prerequisites the executor can't work
    around.
    """
    if body.active:
        if not manager.connected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot activate: broker is not connected",
            )
        if not telegram.logged_in:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot activate: telegram is not logged in",
            )

    s = await _get_or_create_settings(session)
    s.pipeline_active = body.active
    await session.commit()
    await session.refresh(s)

    return await status_endpoint(session, manager, telegram, pipeline)


@router.post("/kill-switch", response_model=PipelineStatus)
async def kill_switch_endpoint(
    body: ActivateRequest,
    session: SessionDep,
    manager: ManagerDep,
    telegram: TelegramDep,
    pipeline: PipelineDep,
) -> PipelineStatus:
    """Engage / disengage the hard pause.

    ``active=true`` engages the kill switch; ``active=false`` releases
    it. The pipeline checks this on every dispatch so flipping it
    takes effect immediately.
    """
    s = await _get_or_create_settings(session)
    s.kill_switch_engaged = body.active
    await session.commit()
    await session.refresh(s)
    return await status_endpoint(session, manager, telegram, pipeline)


@router.get("/trades", response_model=list[TradeAttemptResponse])
async def trades_endpoint(
    session: SessionDep,
    chat_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[TradeAttemptResponse]:
    rows = await list_recent(session, limit=limit, chat_id=chat_id)
    return [_to_response(r) for r in rows]


@router.get("/decisions", response_model=list[ParserDecisionResponse])
async def decisions_endpoint(
    pipeline: PipelineDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ParserDecisionResponse]:
    """Most-recent parsing decisions (newest first), bounded to ``limit``.

    Backed by an in-memory ring buffer on the live ``Pipeline``
    instance — there's no persistence layer because dispatch decisions
    aren't audit material, just a live observability feed. WebSocket
    subscribers also receive each decision as a ``pipeline.decision``
    frame as it happens; this HTTP endpoint exists so a late-loading
    dashboard can backfill the table before the socket starts streaming.
    """
    snapshot = pipeline.recent_decisions[:limit]
    return [ParserDecisionResponse(**d) for d in snapshot]
