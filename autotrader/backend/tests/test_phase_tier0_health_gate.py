"""Pre-trade WS health gate tests (audit 2026-05-14, Task 5).

Spec §3.5: before the executor dispatches a broker order,
``QuotexManager.assert_live(asset)`` must verify:
  1. manager._state == "connected"
  2. client.api.state.auth_status == AuthStatus.AUTHENTICATED
  3. at least one tick for the asset has been seen
  4. the latest tick is fresh (within settings.broker_stale_feed_max_age_seconds)

If any check fails, ``BrokerNotLive`` is raised and the executor
must mark the attempt ``broker_error`` WITHOUT advancing the
martingale ladder.

Landmine-aware fakes:
  * _FakeApiState carries ``auth_status`` so the test would fail if
    someone used the flat ``is_authenticated`` attribute path.
  * realtime_price entries are shaped exactly as pyquotex writes them:
    ``{"time": unix_seconds, "price": float}``.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Fake pyquotex structures — mirror the REAL paths confirmed in source.
# ---------------------------------------------------------------------------


class _FakeApiState:
    """Mirrors pyquotex.global_value.ConnectionState.
    Has ``auth_status`` (the real attribute) — NOT ``is_authenticated``.
    A flat-attr revert would leave this class without ``auth_status``
    and the tests would blow up on AttributeError.
    """
    def __init__(self, auth_status: int) -> None:
        self.auth_status = auth_status


class _FakeApi:
    """Minimal stand-in for pyquotex.api.QuotexAPI.

    Carries ``state`` (with real ``auth_status`` attribute) and
    ``realtime_price`` (defaultdict-like dict keyed by asset, each
    value a deque of ``{"time": float, "price": float}`` — exactly
    what api.py:700 stores).
    """

    def __init__(
        self,
        *,
        auth_status: int,
        realtime_price: dict | None = None,
    ) -> None:
        self.state = _FakeApiState(auth_status=auth_status)
        # Default: a fresh tick for any asset we care about in the test.
        if realtime_price is None:
            d: dict = {}
            self.realtime_price = d
        else:
            self.realtime_price = realtime_price


class _FakeClient:
    """Stand-in for pyquotex.stable_api.Quotex."""

    def __init__(self, api: _FakeApi) -> None:
        self.api = api


def _make_manager(state: str, api: _FakeApi) -> object:
    """Return a QuotexManager whose _state and _client are stubbed."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager.__new__(QuotexManager)
    # Minimal attribute set needed by assert_live.
    mgr._state = state
    mgr._client = _FakeClient(api)
    return mgr


# ---------------------------------------------------------------------------
# Per-test DB wipe (mirrors test_phase2_idempotency pattern)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _wipe_health_gate_rows() -> AsyncIterator[None]:
    yield
    from sqlalchemy import text  # noqa: PLC0415

    from autotrader.db import engine, init_db  # noqa: PLC0415

    await init_db()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM trade_attempts"))


# ---------------------------------------------------------------------------
# Unit tests: assert_live raising on each failure mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_live_raises_not_connected_when_state_is_not_connected() -> None:
    """assert_live raises BrokerNotLive(reason='not_connected')
    when manager._state != 'connected', regardless of auth state."""
    from pyquotex.global_value import AuthStatus  # noqa: PLC0415

    from autotrader.services.quotex_manager import BrokerNotLive  # noqa: PLC0415

    api = _FakeApi(auth_status=int(AuthStatus.AUTHENTICATED))
    mgr = _make_manager("reconnecting", api)

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD_otc")  # type: ignore[attr-defined]

    assert exc_info.value.reason == "not_connected"
    assert exc_info.value.detail.get("state") == "reconnecting"


@pytest.mark.asyncio
async def test_assert_live_raises_ws_not_authed_when_auth_status_not_authenticated() -> None:
    """assert_live raises BrokerNotLive(reason='ws_not_authed')
    when state==connected but api.state.auth_status != AUTHENTICATED.

    The fake uses a _FakeApiState with an explicit auth_status int — a
    flat-attr revert (is_authenticated=True) would make this test pass
    incorrectly, proving the fakes enforce the correct path.
    """
    from pyquotex.global_value import AuthStatus  # noqa: PLC0415

    from autotrader.services.quotex_manager import BrokerNotLive  # noqa: PLC0415

    # Deliberately set auth_status to NOT_AUTHENTICATED (0)
    api = _FakeApi(auth_status=int(AuthStatus.NOT_AUTHENTICATED))
    mgr = _make_manager("connected", api)

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD_otc")  # type: ignore[attr-defined]

    assert exc_info.value.reason == "ws_not_authed"


@pytest.mark.asyncio
async def test_assert_live_raises_no_tick_seen_when_no_realtime_price_entry() -> None:
    """assert_live raises BrokerNotLive(reason='no_tick_seen')
    when asset has no entry in realtime_price."""
    from pyquotex.global_value import AuthStatus  # noqa: PLC0415

    from autotrader.services.quotex_manager import BrokerNotLive  # noqa: PLC0415

    api = _FakeApi(
        auth_status=int(AuthStatus.AUTHENTICATED),
        realtime_price={"OTHER_ASSET_otc": deque([{"time": time.time(), "price": 1.23}])},
    )
    mgr = _make_manager("connected", api)

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD_otc")  # type: ignore[attr-defined]

    assert exc_info.value.reason == "no_tick_seen"
    assert exc_info.value.detail.get("asset") == "EURUSD_otc"


@pytest.mark.asyncio
async def test_assert_live_raises_no_tick_seen_when_realtime_price_deque_is_empty() -> None:
    """assert_live raises BrokerNotLive(reason='no_tick_seen')
    when the deque for the asset is empty (present but no ticks)."""
    from pyquotex.global_value import AuthStatus  # noqa: PLC0415

    from autotrader.services.quotex_manager import BrokerNotLive  # noqa: PLC0415

    api = _FakeApi(
        auth_status=int(AuthStatus.AUTHENTICATED),
        realtime_price={"EURUSD_otc": deque()},
    )
    mgr = _make_manager("connected", api)

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD_otc")  # type: ignore[attr-defined]

    assert exc_info.value.reason == "no_tick_seen"


@pytest.mark.asyncio
async def test_assert_live_raises_stale_feed_when_tick_is_old() -> None:
    """assert_live raises BrokerNotLive(reason='stale_feed') when the
    latest tick is older than settings.broker_stale_feed_max_age_seconds.

    Default threshold is 10s; we insert a tick that is 30s old.
    detail must contain age_seconds and threshold.
    """
    from pyquotex.global_value import AuthStatus  # noqa: PLC0415

    from autotrader.services.quotex_manager import BrokerNotLive  # noqa: PLC0415

    stale_ts = time.time() - 30.0  # 30 seconds old — beyond default 10s threshold

    api = _FakeApi(
        auth_status=int(AuthStatus.AUTHENTICATED),
        realtime_price={"EURUSD_otc": deque([{"time": stale_ts, "price": 1.08}])},
    )
    mgr = _make_manager("connected", api)

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD_otc")  # type: ignore[attr-defined]

    err = exc_info.value
    assert err.reason == "stale_feed"
    assert "age_seconds" in err.detail
    assert "threshold" in err.detail
    assert err.detail["age_seconds"] >= 20.0    # generous; wall-clock won't jump 20s
    assert err.detail["threshold"] == 10.0


@pytest.mark.asyncio
async def test_assert_live_does_not_raise_when_all_checks_pass() -> None:
    """assert_live returns None (no raise) when state==connected,
    auth==AUTHENTICATED, and a fresh tick is present."""
    from pyquotex.global_value import AuthStatus  # noqa: PLC0415

    fresh_ts = time.time() - 2.0  # 2 seconds old — within 10s threshold

    api = _FakeApi(
        auth_status=int(AuthStatus.AUTHENTICATED),
        realtime_price={"EURUSD_otc": deque([{"time": fresh_ts, "price": 1.08}])},
    )
    mgr = _make_manager("connected", api)

    # Must not raise.
    result = await mgr.assert_live("EURUSD_otc")  # type: ignore[attr-defined]
    assert result is None


# ---------------------------------------------------------------------------
# Executor integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_blocked_marks_broker_error_and_does_not_tick_martingale() -> None:
    """_place catches BrokerNotLive, persists broker_error with
    'healthgate:stale_feed' error string, and does NOT call record_outcome.

    The martingale ladder must be provably unreachable on the BrokerNotLive path.
    """
    from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415
    from autotrader.models.trade_attempt import (  # noqa: PLC0415
        TradeAttempt,
        insert_attempt,
    )
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415
    from autotrader.services.parsers.base import ParsedSignal  # noqa: PLC0415
    from autotrader.services.quotex_manager import BrokerNotLive, QuotexManager  # noqa: PLC0415
    from autotrader.services.risk_gate import RiskDecision  # noqa: PLC0415

    await init_db()

    # Seed a pending attempt row.
    async with AsyncSessionLocal() as session:
        attempt = await insert_attempt(
            session,
            TradeAttempt(
                chat_id=-9001,
                parser_config_id=1,
                asset="EURUSD_otc",
                direction="call",
                duration_seconds=60,
                stake=5.0,
                trade_mode="live",
                status="pending",
                raw_text="CALL EURUSD_otc 1m",
            ),
        )
    assert attempt.id is not None

    # Build an executor whose manager.assert_live raises BrokerNotLive.
    # Use an instance-scoped AsyncMock on the manager object — mirrors
    # the Task-3 lesson (class-level patch breaks other tests).
    mgr = MagicMock(spec=QuotexManager)
    mgr.assert_live = AsyncMock(
        side_effect=BrokerNotLive(
            "stale_feed",
            asset="EURUSD_otc",
            age_seconds=30.0,
            threshold=10.0,
        )
    )
    # Executor also reads _manager.assets and _manager._client in _resolve_asset;
    # stub those so it doesn't blow up before reaching assert_live.
    mgr.assets = ("EURUSD_otc",)
    mgr._client = MagicMock()

    executor = TradeExecutor(
        manager=mgr,
        live_trading_enabled_env=False,
    )

    signal = ParsedSignal(
        asset="EURUSD_otc",
        direction="call",
        duration_seconds=60,
        stake=5.0,
        fire_at=None,
        raw_text="CALL EURUSD_otc 1m",
        parser_id="test",
        asset_raw="EURUSD_otc",
    )
    # RiskDecision: use outcome= (the real constructor field).
    # .allowed is a @property; do NOT pass allowed= to the ctor.
    decision = RiskDecision(
        outcome="allow",
        reason="",
        stake=5.0,
        trade_mode="live",
    )

    # Patch record_outcome so we can verify it is never called.
    with patch(
        "autotrader.services.executor.record_outcome",
        new_callable=AsyncMock,
    ) as mock_record_outcome:
        await executor._place(attempt, signal, decision)

    # The attempt row must now be broker_error with healthgate: prefix.
    async with AsyncSessionLocal() as session:
        from sqlmodel import select  # noqa: PLC0415
        # Use SQLModel's session.exec() (not the raw SQLAlchemy .execute()).
        stmt = select(TradeAttempt).where(TradeAttempt.id == attempt.id)
        result = await session.exec(stmt)  # type: ignore[call-overload]
        row = result.first()

    assert row is not None, "attempt row must still exist in DB"
    assert row.status == "broker_error", f"expected broker_error, got {row.status!r}"
    assert row.error is not None
    assert "healthgate:" in row.error, (
        f"error must contain 'healthgate:' prefix, got: {row.error!r}"
    )
    assert "stale_feed" in row.error, (
        f"error must contain the reason 'stale_feed', got: {row.error!r}"
    )

    # Martingale ladder must NOT have been advanced.
    mock_record_outcome.assert_not_called()
