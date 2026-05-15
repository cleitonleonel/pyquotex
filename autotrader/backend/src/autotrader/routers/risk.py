"""Risk module endpoints — daily caps + martingale streaks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select

from autotrader.auth import require_auth
from autotrader.dependencies import SessionDep
from autotrader.models.martingale_state import MartingaleState, reset_state
from autotrader.models.parser_config import ParserConfig
from autotrader.models.settings import GlobalSettings
from autotrader.services.risk_gate import compute_budget

router = APIRouter(prefix="/risk", tags=["risk"], dependencies=[Depends(require_auth)])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RiskCaps(BaseModel):
    daily_max_loss: float = Field(default=0.0, ge=0.0)
    daily_max_stake: float = Field(default=0.0, ge=0.0)
    max_concurrent_trades: int = Field(default=0, ge=0)


class BudgetSnapshot(BaseModel):
    realised_pnl: float
    committed_stake: float
    open_attempts: int


class StreakRow(BaseModel):
    parser_config_id: int
    parser_name: str
    chat_id: int
    martingale_enabled: bool
    multiplier: float
    max_streak: int
    current_streak: int
    last_outcome: str
    last_stake: float
    updated_at: datetime | None = None
    # Winning-streak ladder.
    winning_streak_enabled: bool = False
    winning_streak_max_level: int = 2
    current_win_streak: int = 0
    last_payout: float = 0.0


class RiskOverview(BaseModel):
    caps: RiskCaps
    budget: BudgetSnapshot
    streaks: list[StreakRow]


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=RiskOverview)
async def overview_endpoint(session: SessionDep) -> RiskOverview:
    s = await _get_or_create_settings(session)
    budget = await compute_budget(session)

    # Join parser configs with their martingale state. Configs without
    # a state row return zeros (no trades placed yet).
    cfgs = list((await session.exec(select(ParserConfig))).all())
    states = {
        st.parser_config_id: st
        for st in (await session.exec(select(MartingaleState))).all()
    }

    rows = [
        StreakRow(
            parser_config_id=cfg.id or 0,
            parser_name=cfg.name or f"parser #{cfg.id}",
            chat_id=cfg.chat_id,
            martingale_enabled=cfg.martingale_enabled,
            multiplier=cfg.martingale_multiplier,
            max_streak=cfg.martingale_max_streak,
            current_streak=(states[cfg.id].current_streak if cfg.id in states else 0),
            last_outcome=(states[cfg.id].last_outcome if cfg.id in states else ""),
            last_stake=(states[cfg.id].last_stake if cfg.id in states else 0.0),
            updated_at=(states[cfg.id].updated_at if cfg.id in states else None),
            winning_streak_enabled=cfg.winning_streak_enabled,
            winning_streak_max_level=cfg.winning_streak_max_level,
            current_win_streak=(states[cfg.id].current_win_streak if cfg.id in states else 0),
            last_payout=(states[cfg.id].last_payout if cfg.id in states else 0.0),
        )
        for cfg in cfgs
    ]

    return RiskOverview(
        caps=RiskCaps(
            daily_max_loss=s.daily_max_loss,
            daily_max_stake=s.daily_max_stake,
            max_concurrent_trades=s.max_concurrent_trades,
        ),
        budget=BudgetSnapshot(
            realised_pnl=budget.realised_pnl,
            committed_stake=budget.committed_stake,
            open_attempts=budget.open_attempts,
        ),
        streaks=rows,
    )


@router.put("/caps", response_model=RiskCaps)
async def update_caps_endpoint(body: RiskCaps, session: SessionDep) -> RiskCaps:
    s = await _get_or_create_settings(session)
    s.daily_max_loss = body.daily_max_loss
    s.daily_max_stake = body.daily_max_stake
    s.max_concurrent_trades = body.max_concurrent_trades
    await session.commit()
    return body


@router.post("/streaks/{parser_config_id}/reset", response_model=OkResponse)
async def reset_streak_endpoint(
    parser_config_id: int,
    session: SessionDep,
) -> OkResponse:
    """Manually reset a parser's martingale ladder to step 0."""
    cfg = await session.get(ParserConfig, parser_config_id)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="parser config not found",
        )
    await reset_state(session, parser_config_id)
    return OkResponse()
