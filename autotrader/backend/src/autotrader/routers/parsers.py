"""Parser configuration + live-tester endpoints.

Each watched chat can host multiple named parser configs (priorities
break ties — lower runs first). The dashboard never instantiates
parsers itself: it POSTs the config to ``/parsers/test`` with a
sample message run and gets back the structured outcome. That keeps
regex compilation, normalisation, aggregation, mode pinning, and
martingale shaping on the backend, where they're tested.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from autotrader.auth import require_auth
from autotrader.dependencies import ManagerDep, SessionDep
from autotrader.models.parser_config import (
    ParserConfig,
    create_config,
    delete_config,
    get_config,
    list_configs,
    update_config,
)
from autotrader.services.parsers.aggregator import parse_via_aggregator
from autotrader.services.parsers.base import (
    ParsedSignal,
    ParseError,
    ParseOutcome,
    RawMessage,
)
from autotrader.services.parsers.factory import ParserBuildError, build_parser
from autotrader.services.parsers.template import BUILTIN_TEMPLATES

router = APIRouter(
    prefix="/parsers",
    tags=["parsers"],
    dependencies=[Depends(require_auth)],
)

ParserType = Literal["template", "regex", "prep_trigger", "batch"]
TradeMode = Literal["live", "scheduled", "auto"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TemplateInfo(BaseModel):
    id: str
    label: str
    template: str
    example: str


class MartingalePayload(BaseModel):
    enabled: bool = False
    multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    max_streak: int = Field(default=5, ge=0, le=20)
    reset_on_win: bool = True


class ConfigPayload(BaseModel):
    """Body shape used by both upsert and the live tester."""

    name: str = ""
    priority: int = Field(default=100, ge=0, le=10_000)

    parser_type: ParserType = "template"
    parser_config: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    timezone_offset_minutes: int = Field(default=0, ge=-12 * 60, le=14 * 60)
    asset_aliases: dict[str, str] = Field(default_factory=dict)
    aggregate_window_seconds: int = Field(default=0, ge=0, le=300)

    default_stake: float = Field(default=1.0, ge=0.0)
    default_duration_seconds: int = Field(default=60, ge=1, le=86_400)
    trade_mode: TradeMode = "auto"

    martingale: MartingalePayload = Field(default_factory=MartingalePayload)

    enabled: bool = True


class CreateConfigRequest(ConfigPayload):
    chat_id: int


class ConfigResponse(ConfigPayload):
    id: int
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
    trade_mode: TradeMode
    # Asset auto-resolution diagnostics
    asset_raw: str = ""
    asset_via: str = ""


class TestResponse(BaseModel):
    matched: bool
    signal: SignalResponse | None = None      # first match (back-compat)
    signals: list[SignalResponse] = Field(default_factory=list)  # all matches
    error: str | None = None
    error_detail: dict[str, Any] | None = None


class OkResponse(BaseModel):
    ok: Literal[True] = True


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def _safe_parser_type(value: str) -> ParserType:
    return value if value in ("template", "regex") else "template"


def _safe_trade_mode(value: str) -> TradeMode:
    return value if value in ("live", "scheduled", "auto") else "auto"  # type: ignore[return-value]


def _to_response(row: ParserConfig) -> ConfigResponse:
    return ConfigResponse(
        id=row.id or 0,
        chat_id=row.chat_id,
        name=row.name,
        priority=row.priority,
        parser_type=_safe_parser_type(row.parser_type),
        parser_config=row.parser_config_dict(),
        timezone=row.timezone,
        timezone_offset_minutes=row.timezone_offset_minutes,
        asset_aliases=row.asset_aliases(),
        aggregate_window_seconds=row.aggregate_window_seconds,
        default_stake=row.default_stake,
        default_duration_seconds=row.default_duration_seconds,
        trade_mode=_safe_trade_mode(row.trade_mode),
        martingale=MartingalePayload(
            enabled=row.martingale_enabled,
            multiplier=row.martingale_multiplier,
            max_streak=row.martingale_max_streak,
            reset_on_win=row.martingale_reset_on_win,
        ),
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _payload_to_dict(p: ConfigPayload) -> dict[str, Any]:
    """Flatten the request body into the kwargs ``create_config`` /
    ``update_config`` expect (martingale block is denormalised)."""
    return {
        "name": p.name,
        "priority": p.priority,
        "parser_type": p.parser_type,
        "parser_config": p.parser_config,
        "timezone": p.timezone,
        "timezone_offset_minutes": p.timezone_offset_minutes,
        "asset_aliases": p.asset_aliases,
        "aggregate_window_seconds": max(0, p.aggregate_window_seconds),
        "default_stake": p.default_stake,
        "default_duration_seconds": p.default_duration_seconds,
        "trade_mode": p.trade_mode,
        "martingale_enabled": p.martingale.enabled,
        "martingale_multiplier": p.martingale.multiplier,
        "martingale_max_streak": p.martingale.max_streak,
        "martingale_reset_on_win": p.martingale.reset_on_win,
        "enabled": p.enabled,
    }


def _gap_seconds(p: ConfigPayload) -> int:
    """Resolve the prep-to-trigger gap.

    For prep_trigger parsers we reuse ``aggregate_window_seconds`` as the
    gap when the user explicitly set it; otherwise default to 120s
    (matches the typical prep+sticker channel cadence).
    """
    return p.aggregate_window_seconds if p.aggregate_window_seconds > 0 else 120


def _validate_compiles(p: ConfigPayload) -> None:
    """Fail fast with 400 if the parser config doesn't compile."""
    try:
        build_parser(
            parser_type=p.parser_type,
            parser_config=p.parser_config,
            timezone_offset_minutes=p.timezone_offset_minutes,
            asset_aliases=p.asset_aliases,
            default_duration_seconds=p.default_duration_seconds,
            gap_seconds=_gap_seconds(p),
        )
    except ParserBuildError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _signal_to_response(signal: ParsedSignal, trade_mode: TradeMode) -> SignalResponse:
    return SignalResponse(
        asset=signal.asset,
        direction=signal.direction,
        duration_seconds=signal.duration_seconds,
        stake=signal.stake,
        fire_at=signal.fire_at,
        raw_text=signal.raw_text,
        parser_id=signal.parser_id,
        matched_groups=signal.matched_groups,
        trade_mode=trade_mode,
        asset_raw=signal.asset_raw,
        asset_via=signal.asset_via,
    )


# ---------------------------------------------------------------------------
# Routes — templates
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateInfo])
async def templates_endpoint() -> list[TemplateInfo]:
    """Built-in templates the dashboard offers as click-to-pick."""
    return [TemplateInfo(**t) for t in BUILTIN_TEMPLATES]


# ---------------------------------------------------------------------------
# Routes — config CRUD
# ---------------------------------------------------------------------------


@router.get("/configs", response_model=list[ConfigResponse])
async def list_configs_endpoint(
    session: SessionDep,
    chat_id: int | None = Query(default=None),
) -> list[ConfigResponse]:
    return [_to_response(r) for r in await list_configs(session, chat_id=chat_id)]


@router.get("/configs/{config_id}", response_model=ConfigResponse)
async def get_config_endpoint(
    config_id: int,
    session: SessionDep,
) -> ConfigResponse:
    row = await get_config(session, config_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="parser config not found",
        )
    return _to_response(row)


@router.post(
    "/configs",
    response_model=ConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_endpoint(
    body: CreateConfigRequest,
    session: SessionDep,
) -> ConfigResponse:
    _validate_compiles(body)
    payload = _payload_to_dict(body)
    # Drop the validated chat_id from the body — create_config takes
    # it as a separate kwarg. Mirrors how update_config strips
    # immutable fields.
    payload.pop("chat_id", None)
    row = await create_config(
        session,
        chat_id=body.chat_id,
        payload=payload,
    )
    return _to_response(row)


@router.put("/configs/{config_id}", response_model=ConfigResponse)
async def update_config_endpoint(
    config_id: int,
    body: ConfigPayload,
    session: SessionDep,
) -> ConfigResponse:
    _validate_compiles(body)
    row = await update_config(
        session,
        config_id=config_id,
        payload=_payload_to_dict(body),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="parser config not found",
        )
    return _to_response(row)


@router.delete("/configs/{config_id}", response_model=OkResponse)
async def delete_config_endpoint(
    config_id: int,
    session: SessionDep,
) -> OkResponse:
    await delete_config(session, config_id)
    return OkResponse()


# ---------------------------------------------------------------------------
# Routes — live tester
# ---------------------------------------------------------------------------


@router.post("/test", response_model=TestResponse)
async def test_endpoint(body: TestRequest, manager: ManagerDep) -> TestResponse:
    """Run a parser config against a sample message run.

    No DB writes; the request payload contains the entire config so
    the user can iterate before saving. The broker's asset cache is
    threaded through so the tester reflects the auto-resolution that
    Phase 4's executor will apply at trade time.
    """
    cfg = body.config

    try:
        parser = build_parser(
            parser_type=cfg.parser_type,
            parser_config=cfg.parser_config,
            timezone_offset_minutes=cfg.timezone_offset_minutes,
            asset_aliases=cfg.asset_aliases,
            known_assets=manager.assets,
            default_duration_seconds=cfg.default_duration_seconds,
            gap_seconds=_gap_seconds(cfg),
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

    outcomes: list[ParseOutcome]
    if cfg.parser_type == "batch":
        # Batch parser emits N rows from one message.
        outcomes = parser.parse_all(messages)
    elif cfg.parser_type == "prep_trigger":
        # PrepTriggerParser is stateful — feed messages in order and
        # return the first emitted signal.
        outcomes = [parser.parse(messages)]
    elif cfg.aggregate_window_seconds > 0:
        outcomes = [
            parse_via_aggregator(
                parser,
                messages,
                window_seconds=cfg.aggregate_window_seconds,
            ),
        ]
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
        outcomes = [outcome]

    signals = [o for o in outcomes if isinstance(o, ParsedSignal)]
    if signals:
        # Trade-mode pin: scheduled rejects live-only signals; live
        # strips any extracted fire_at; auto leaves them alone. Apply
        # uniformly across all rows in the batch case.
        if cfg.trade_mode == "scheduled" and any(s.fire_at is None for s in signals):
            return TestResponse(
                matched=False,
                error="trade_mode=scheduled but at least one signal has no fire_at",
            )
        if cfg.trade_mode == "live":
            signals = [
                ParsedSignal(
                    asset=s.asset,
                    direction=s.direction,
                    duration_seconds=s.duration_seconds,
                    stake=s.stake,
                    fire_at=None,
                    raw_text=s.raw_text,
                    parser_id=s.parser_id,
                    matched_groups=s.matched_groups,
                    asset_raw=s.asset_raw,
                    asset_via=s.asset_via,
                )
                for s in signals
            ]

        first = signals[0]
        return TestResponse(
            matched=True,
            signal=_signal_to_response(first, cfg.trade_mode),
            signals=[_signal_to_response(s, cfg.trade_mode) for s in signals],
        )

    # No signal — return the last (most-informative) error.
    error = next(
        (o for o in reversed(outcomes) if isinstance(o, ParseError)),
        outcomes[-1] if outcomes else ParseError(reason="no outcomes"),
    )
    assert isinstance(error, ParseError)
    return TestResponse(
        matched=False,
        error=error.reason,
        error_detail=error.detail or None,
    )
