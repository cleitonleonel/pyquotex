"""Public, immutable dataclasses for the pyquotex API surface.

These types are returned by selected public methods (and accepted as
inputs where applicable). They exist so consumers get real IDE
completion and ``mypy`` coverage instead of opaque ``dict[str, Any]``.

Existing methods continue to return ``dict``/``list`` to preserve
backward compatibility; helper ``from_dict`` constructors are provided
so callers can opt into typed objects when they want them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

TradeStatus = Literal["win", "loss", "draw", "pending"]
TradeDirection = Literal["call", "put"]


@dataclass(slots=True, frozen=True)
class Candle:
    """A single OHLC(V) candle.

    Times are unix-epoch seconds matching the broker's server clock.
    """

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Candle":
        return cls(
            time=int(data.get("time", 0)),
            open=float(data.get("open", 0.0)),
            high=float(data.get("high", 0.0)),
            low=float(data.get("low", 0.0)),
            close=float(data.get("close", 0.0)),
            volume=float(data.get("volume", 0.0) or 0.0),
        )

    @classmethod
    def from_array(cls, arr: list[Any]) -> "Candle":
        """Build from broker's positional array form: [t, o, c, h, l]."""
        if len(arr) < 5:
            raise ValueError(f"candle array too short: {arr!r}")
        return cls(
            time=int(arr[0]),
            open=float(arr[1]),
            close=float(arr[2]),
            high=float(arr[3]),
            low=float(arr[4]),
            volume=float(arr[5]) if len(arr) > 5 else 0.0,
        )

    @property
    def color(self) -> Literal["green", "red", "doji"]:
        if self.close > self.open:
            return "green"
        if self.close < self.open:
            return "red"
        return "doji"


@dataclass(slots=True, frozen=True)
class TradeResult:
    """Outcome of a completed trade."""

    ticket: str
    status: TradeStatus
    profit: float
    asset: str | None = None
    amount: float | None = None
    direction: TradeDirection | None = None
    open_time: int | None = None
    close_time: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TradeResult":
        profit = float(data.get("profit", data.get("profitAmount", 0.0)) or 0.0)
        status_raw = str(data.get("win", "") or "").lower()
        if status_raw in ("win", "loss", "draw"):
            status: TradeStatus = status_raw  # type: ignore[assignment]
        else:
            status = "win" if profit > 0 else ("loss" if profit < 0 else "draw")
        return cls(
            ticket=str(data.get("ticket", data.get("id", ""))),
            status=status,
            profit=profit,
            asset=data.get("asset"),
            amount=(
                float(data["amount"]) if data.get("amount") is not None else None
            ),
            direction=data.get("direction") or data.get("action"),
            open_time=(
                int(data["openTime"]) if data.get("openTime") is not None else None
            ),
            close_time=(
                int(data["closeTime"]) if data.get("closeTime") is not None else None
            ),
        )


@dataclass(slots=True, frozen=True)
class Balance:
    """A snapshot of account balances at a moment in time."""

    demo: float
    live: float
    currency_code: str | None = None
    currency_symbol: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Balance":
        return cls(
            demo=float(data.get("demoBalance", 0.0) or 0.0),
            live=float(data.get("liveBalance", 0.0) or 0.0),
            currency_code=data.get("currencyCode"),
            currency_symbol=data.get("currencySymbol"),
        )


@dataclass(slots=True, frozen=True)
class ProfileInfo:
    """User profile information returned by ``Quotex.get_profile()``."""

    nickname: str | None
    profile_id: int | str | None
    demo_balance: float
    live_balance: float
    currency_code: str | None
    currency_symbol: str | None
    country_name: str | None
    offset: int | None

    @classmethod
    def from_profile(cls, profile: Any) -> "ProfileInfo":
        """Build from the legacy mutable ``Profile`` object."""
        return cls(
            nickname=getattr(profile, "nick_name", None),
            profile_id=getattr(profile, "profile_id", None),
            demo_balance=float(getattr(profile, "demo_balance", 0.0) or 0.0),
            live_balance=float(getattr(profile, "live_balance", 0.0) or 0.0),
            currency_code=getattr(profile, "currency_code", None),
            currency_symbol=getattr(profile, "currency_symbol", None),
            country_name=getattr(profile, "country_name", None),
            offset=getattr(profile, "offset", None),
        )


@dataclass(slots=True, frozen=True)
class AssetInfo:
    """Minimal asset descriptor: id, symbol, display name, open flag."""

    id: int | None
    symbol: str
    name: str
    is_open: bool

    @classmethod
    def from_instrument_row(cls, row: list[Any]) -> "AssetInfo":
        """Build from broker's positional row: [id, symbol, name, ...].

        The 14th column (index 14) typically holds the open/close flag.
        """
        return cls(
            id=int(row[0]) if row and row[0] is not None else None,
            symbol=str(row[1]) if len(row) > 1 else "",
            name=str(row[2]).replace("\n", "") if len(row) > 2 else "",
            is_open=bool(row[14]) if len(row) > 14 else False,
        )


@dataclass(slots=True, frozen=True)
class ReconnectPolicy:
    """Configures auto-reconnect behavior for :class:`WebsocketClient`.

    Parameters
    ----------
    enabled:
        Master toggle. When ``False``, the client behaves as it did before
        the resilience patch (one connection, no auto-reconnect).
    max_attempts:
        Stop after this many consecutive failed reconnects. ``0`` means
        infinite retries (recommended for long-lived bots).
    base_delay / max_delay / jitter:
        Exponential backoff parameters. Delay = ``base_delay * 2**attempt``,
        capped at ``max_delay`` seconds, with multiplicative ``jitter``.
    stale_timeout:
        Seconds without a single inbound frame before the connection is
        considered stale and forcibly recycled. ``0`` disables the
        watchdog (rely on websockets ping/pong only).
    """

    enabled: bool = True
    max_attempts: int = 0
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.1
    stale_timeout: float = 60.0


@dataclass(slots=True)
class Subscription:
    """Tracks an active stream so it can be resumed after reconnect."""

    kind: Literal["candle", "candle_all_size", "mood", "realtime_price"]
    asset: str
    period: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AssetInfo",
    "Balance",
    "Candle",
    "ProfileInfo",
    "ReconnectPolicy",
    "Subscription",
    "TradeDirection",
    "TradeResult",
    "TradeStatus",
]
