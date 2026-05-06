"""Pydantic request/response models for the web API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ----- Common -----

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    connected: bool
    auth_status: str
    last_reconnect_at: float | None = None
    reconnect_attempts: int = 0
    successful_reconnects: int = 0
    in_flight_pendings: int = 0


# ----- Auth / account -----

class ConnectResponse(BaseModel):
    """Response from ``POST /auth/connect``.

    ``otp_required=True`` means the broker emailed a PIN and the
    library is blocked waiting for it. The caller should ``POST
    /auth/otp`` with the code, then re-call ``POST /auth/connect`` (or
    just wait — the original connect task will complete on its own).

    HTTP status ``200`` when ``connected=True``; ``202`` when an OTP
    is pending; ``502`` on broker error.
    """
    connected: bool
    message: str
    balance: float | None = None
    profile_id: int | None = None
    nickname: str | None = None
    currency: str | None = None
    # OTP / 2FA flow
    otp_required: bool = False
    otp_prompt: str | None = Field(
        default=None,
        description="Human-readable prompt forwarded from the broker.",
    )


class OtpRequest(BaseModel):
    code: str = Field(
        ..., min_length=1,
        description="The PIN code received by email.",
    )


class OtpResponse(BaseModel):
    accepted: bool
    connected: bool
    message: str
    balance: float | None = None
    profile_id: int | None = None
    nickname: str | None = None
    currency: str | None = None


class BalanceResponse(BaseModel):
    balance: float
    currency: str | None = None
    account_mode: str


class ProfileResponse(BaseModel):
    profile_id: int | None = None
    nickname: str | None = None
    currency_code: str | None = None
    currency_symbol: str | None = None
    country: str | None = None
    timezone_offset: int | None = None
    demo_balance: float | None = None
    live_balance: float | None = None


# ----- Market data -----

class Candle(BaseModel):
    time: int
    open: float
    close: float
    high: float
    low: float


class CandlesResponse(BaseModel):
    asset: str
    period: int
    count: int
    candles: list[Candle]


class HistoricalCandlesResponse(BaseModel):
    asset: str
    period: int
    amount_of_seconds: int
    count: int
    oldest_time: int | None = None
    newest_time: int | None = None
    candles: list[Candle]


class SentimentResponse(BaseModel):
    asset: str
    bullish: float | None = None
    bearish: float | None = None
    raw: dict[str, Any]


# ----- Trades -----

Direction = Literal["call", "put", "up", "down"]
TimeMode = Literal["TIME", "TURBO"]


class BuyRequest(BaseModel):
    amount: float = Field(gt=0)
    asset: str
    direction: Direction
    duration: int = Field(gt=0, description="Trade duration in seconds")
    time_mode: TimeMode = "TIME"
    confirm_timeout: float = Field(default=10.0, gt=0, le=60)


class BuyResponse(BaseModel):
    accepted: bool
    order_id: str | int | None = None
    detail: dict[str, Any] | str | None = None


class PendingRequest(BaseModel):
    amount: float = Field(gt=0)
    asset: str
    direction: Direction
    duration: int = Field(gt=0, description="Trade duration in seconds")
    open_time: str | None = Field(
        default=None,
        description=(
            'None → auto-schedule next "duration" boundary ≥ 90s ahead. '
            'Otherwise ISO 8601 UTC ("YYYY-MM-DDTHH:MM:SS.000Z") or '
            'user-local "DD/MM HH:MM".'
        ),
    )
    confirm_timeout: float = Field(default=30.0, gt=0, le=120)


class PendingAtPriceRequest(BaseModel):
    amount: float = Field(gt=0)
    asset: str
    direction: Direction
    quote: float = Field(description="Trigger price level")
    period: str = Field(
        default="M1",
        description='Chart period: "M1", "M5", "M15", "M30", "H1", "H4", "D1"',
    )
    timeout: float = Field(default=30.0, gt=0, le=120)


class PendingResponse(BaseModel):
    accepted: bool
    pending_id: str | None = None
    detail: dict[str, Any] | str | None = None


class TradeResultResponse(BaseModel):
    order_id: str
    status: Literal["win", "loss", "pending", "timeout", "unknown"]
    profit: float = 0.0


class CancelResponse(BaseModel):
    cancelled: bool
    detail: dict[str, Any] | str | None = None


# ----- Errors -----

class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
