"""Parser configuration + live-tester endpoints.

The dashboard never instantiates parsers itself — it POSTs the config
to ``/parsers/test`` with a sample message run and gets back the
structured outcome. That keeps regex compilation, normalisation, and
aggregation semantics on the backend, where they're tested.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from autotrader.auth import require_auth
from autotrader.dependencies import SessionDep
from autotrader.models.parser_config import (
    ParserConfig,
    delete_config,
    get_config,
    list_configs,
    upsert_config,
)
from autotrader.services.parsers.aggregator import parse_via_aggregator
from autotrader.services.parsers.base import ParsedSignal, ParseError, RawMessage
from autotrader.services.parsers.factory import ParserBuildError, build_parser
from autotrader.services.parsers.template import BUILTIN_TEMPLATES

router = APIRouter(
    prefix="/parsers",
    tags=["parsers"],
    dependencies=[Depends(require_auth)],
)

ParserType = Literal["template", "regex"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TemplateInfo(BaseModel):
    id: str
    label: str
    template: str
    example: str


class ConfigPayload(BaseModel):
    """Body shape used by both upsert and the live tester."""

    parser_type: ParserType
    parser_config: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    timezone_offset_minutes: int = 0
    asset_aliases: dict[str, str] = Field(default_factory=dict)
    default_stake: float = 1.0
    default_duration_seconds: int = 60
    aggregate_window_seconds: int = 0
    enabled: bool = True


class ConfigResponse(ConfigPayload):
    chat_id: int
    created_at: datetime
    updated_at: datetime


class TestMessage(BaseModel):
    text: str = Field(..., min_length=1)
    sender_id: int = 0
    received_at: datetime | None = None


class TestRequest(BaseModel):
    config: ConfigPayload
    messages: list[TestMessage] = Field(..., min_length=1, max_length=64)


class SignalResponse(BaseModel):
    asset: str
    direction: str
    duration_seconds: int
    stake: float | None
    fire_at: datetime | None
    raw_text: str
    parser_id: str
    matched_groups: dict[str, str]


class TestResponse(BaseModel):
    matched: bool
    signal: SignalResponse | None = None
    error: str | None = None
    error_detail: dict[str, Any] | None = None


class OkResponse(BaseModel):
    ok: Literal[True] = True


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def _to_response(row: ParserConfig) -> ConfigResponse:
    return ConfigResponse(
        chat_id=row.chat_id,
        parser_type=_safe_parser_type(row.parser_type),
        parser_config=row.parser_config_dict(),
        timezone=row.timezone,
        timezone_offset_minutes=row.timezone_offset_minutes,
        asset_aliases=row.asset_aliases(),
        default_stake=row.default_stake,
        default_duration_seconds=row.default_duration_seconds,
        aggregate_window_seconds=row.aggregate_window_seconds,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _safe_parser_type(value: str) -> ParserType:
    return value if value in ("template", "regex") else "template"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateInfo])
async def templates_endpoint() -> list[TemplateInfo]:
    """Built-in templates the dashboard offers as click-to-pick."""
    return [TemplateInfo(**t) for t in BUILTIN_TEMPLATES]


@router.get("/configs", response_model=list[ConfigResponse])
async def list_configs_endpoint(session: SessionDep) -> list[ConfigResponse]:
    return [_to_response(r) for r in await list_configs(session)]


@router.get("/configs/{chat_id}", response_model=ConfigResponse)
async def get_config_endpoint(
    chat_id: int,
    session: SessionDep,
) -> ConfigResponse:
    row = await get_config(session, chat_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no parser config for chat",
        )
    return _to_response(row)


@router.put("/configs/{chat_id}", response_model=ConfigResponse)
async def upsert_config_endpoint(
    chat_id: int,
    body: ConfigPayload,
    session: SessionDep,
) -> ConfigResponse:
    # Validate the parser config compiles before we persist — gives
    # users immediate feedback on bad regex / unknown placeholders.
    try:
        build_parser(
            parser_type=body.parser_type,
            parser_config=body.parser_config,
            timezone_offset_minutes=body.timezone_offset_minutes,
            asset_aliases=body.asset_aliases,
            default_duration_seconds=body.default_duration_seconds,
        )
    except ParserBuildError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    row = await upsert_config(
        session,
        chat_id=chat_id,
        parser_type=body.parser_type,
        parser_config=body.parser_config,
        timezone=body.timezone,
        timezone_offset_minutes=body.timezone_offset_minutes,
        asset_aliases=body.asset_aliases,
        default_stake=body.default_stake,
        default_duration_seconds=body.default_duration_seconds,
        aggregate_window_seconds=max(0, body.aggregate_window_seconds),
        enabled=body.enabled,
    )
    return _to_response(row)


@router.delete("/configs/{chat_id}", response_model=OkResponse)
async def delete_config_endpoint(
    chat_id: int,
    session: SessionDep,
) -> OkResponse:
    await delete_config(session, chat_id)
    return OkResponse()


@router.post("/test", response_model=TestResponse)
async def test_endpoint(body: TestRequest) -> TestResponse:
    """Run a parser config against a sample message run.

    No DB writes; the request payload contains the entire config so
    the user can iterate before saving.
    """
    cfg = body.config

    try:
        parser = build_parser(
            parser_type=cfg.parser_type,
            parser_config=cfg.parser_config,
            timezone_offset_minutes=cfg.timezone_offset_minutes,
            asset_aliases=cfg.asset_aliases,
            default_duration_seconds=cfg.default_duration_seconds,
        )
    except ParserBuildError as exc:
        return TestResponse(matched=False, error=str(exc))

    messages = [
        RawMessage(
            text=m.text,
            chat_id=0,
            sender_id=m.sender_id,
            received_at=m.received_at or datetime.now(UTC),
        )
        for m in body.messages
    ]

    if cfg.aggregate_window_seconds > 0:
        outcome = parse_via_aggregator(
            parser,
            messages,
            window_seconds=cfg.aggregate_window_seconds,
        )
    else:
        # Non-aggregator: parse each message independently and return
        # the first signal (or the last error if none matched).
        outcome = parser.parse([messages[-1]])
        if isinstance(outcome, ParseError) and len(messages) > 1:
            for m in messages[:-1]:
                alt = parser.parse([m])
                if isinstance(alt, ParsedSignal):
                    outcome = alt
                    break

    if isinstance(outcome, ParsedSignal):
        return TestResponse(
            matched=True,
            signal=SignalResponse(
                asset=outcome.asset,
                direction=outcome.direction,
                duration_seconds=outcome.duration_seconds,
                stake=outcome.stake,
                fire_at=outcome.fire_at,
                raw_text=outcome.raw_text,
                parser_id=outcome.parser_id,
                matched_groups=outcome.matched_groups,
            ),
        )
    return TestResponse(
        matched=False,
        error=outcome.reason,
        error_detail=outcome.detail or None,
    )
