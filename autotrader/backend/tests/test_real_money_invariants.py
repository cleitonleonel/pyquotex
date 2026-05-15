"""Real-money load-bearing invariant tests (audit 2026-05-14, Task 7).

These three tests are THE gate before flipping
``AUTOTRADER_LIVE_TRADING_ENABLED=true`` for real money.  Each test
MUST fail if its protection is removed -- no trivial passes, no stubs.

Heavy imports are deferred inside each test function to avoid import-
order side-effects that break unrelated tests (same rule as every other
tier-0 test in this suite -- see ``test_phase0_instrumentation.py``
module docstring).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Autouse DB-wipe fixture (mirrors test_phase2_idempotency.py pattern).
#
# Seeded rows must not leak into neighbouring tests / files because the
# SQLite DB is session-scoped.  Delete AFTER the test body (yield first)
# so the rows are visible during execution.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _wipe_invariant_rows() -> AsyncIterator[None]:
    """Wipe tables that the invariant tests write; idempotent for tests
    that don't seed anything."""
    yield
    from sqlalchemy import text  # noqa: PLC0415

    from autotrader.db import engine, init_db  # noqa: PLC0415

    await init_db()
    async with engine.begin() as conn:
        for table in (
            "trade_attempts",
            "martingale_states",
            "parser_configs",
            "watched_channels",
            "global_settings",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608


# ===========================================================================
# Test 1 -- kill switch blocks all signals and records a rejected row
# ===========================================================================


@pytest.mark.asyncio
async def test_kill_switch_blocks_all_signals_with_decision_row() -> None:
    """Kill switch engaged => the real executor produces a 'rejected'
    TradeAttempt whose error contains 'kill switch'.

    The invariant: if kill_switch_engaged were set to False (or the
    check were removed from risk_gate.evaluate), submit() would proceed
    to _place() and the attempt would end up as 'pending' -- the test
    catches this because it asserts status=='rejected' and checks the
    kill-switch reason string.
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415
    from autotrader.services.parsers import ParsedSignal  # noqa: PLC0415
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    await init_db()

    # Seed: kill switch ON, pipeline active, one enabled parser config.
    cfg = ParserConfig(
        id=8801, chat_id=-8801, name="ks-parser",
        parser_type="template",
        parser_config_json='{"template": "{DIRECTION} {ASSET} {DURATION}"}',
        default_stake=5.0, enabled=True, trade_mode="live",
    )
    gs = GlobalSettings(id=1)
    gs.kill_switch_engaged = True
    gs.pipeline_active = True
    async with AsyncSessionLocal() as s:
        s.add(cfg)
        s.add(gs)
        await s.commit()

    # Build a real executor whose manager is stubbed (never actually
    # reaches the broker -- the risk gate blocks before _place is called).
    mgr = MagicMock(spec=QuotexManager)
    mgr.connected = True
    mgr.assets = ()
    status_obj = MagicMock()
    status_obj.account_mode = "DEMO"
    mgr.status.return_value = status_obj

    executor = TradeExecutor(manager=mgr, live_trading_enabled_env=False)

    async with AsyncSessionLocal() as s:
        cfg_row = await s.get(ParserConfig, 8801)
        settings_row = await s.get(GlobalSettings, 1)
    assert cfg_row is not None
    assert settings_row is not None

    signal = ParsedSignal(
        asset="EURUSD",
        direction="call",
        duration_seconds=60,
        stake=5.0,
        fire_at=None,
        raw_text="BUY EURUSD 1m",
        parser_id="cfg-8801",
    )

    attempt = await executor.submit(
        signal=signal,
        parser_config=cfg_row,
        settings=settings_row,
    )

    # --- Invariant assertions ---

    assert attempt.status == "rejected", (
        "kill switch must produce a rejected attempt; "
        f"got status={attempt.status!r}"
    )
    assert attempt.error is not None
    assert "kill switch" in attempt.error.lower(), (
        f"error must mention 'kill switch'; got: {attempt.error!r}"
    )
    # Confirm the broker was never reached (assert_live is never called
    # when the risk gate blocks before _place).
    mgr.assert_live.assert_not_called()


# ===========================================================================
# Test 2 -- daily_max_loss blocks even mid-ladder recovery trades
# ===========================================================================


@pytest.mark.asyncio
async def test_daily_max_loss_blocks_mid_ladder() -> None:
    """Martingale mid-ladder AND daily_max_loss already reached =>
    the risk gate blocks even the 'due' recovery trade.

    This is the most dangerous moment to fail to block: the operator
    has already lost enough for the day, and the martingale ladder
    is pressuring a LARGER recovery stake.  If the daily_max_loss
    check were removed from risk_gate.evaluate, decision.allowed would
    be True -- the test catches this.
    """
    from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt, insert_attempt  # noqa: PLC0415
    from autotrader.services.parsers.base import ParsedSignal  # noqa: PLC0415
    from autotrader.services.risk_gate import evaluate  # noqa: PLC0415

    await init_db()

    # Seed config: martingale enabled, multiplier 2x, max_streak 3.
    cfg = ParserConfig(
        id=8802, chat_id=-8802, name="ml-parser",
        parser_type="template",
        parser_config_json='{"template": "{DIRECTION} {ASSET} {DURATION}"}',
        default_stake=10.0, enabled=True, trade_mode="live",
        martingale_enabled=True,
        martingale_multiplier=2.0,
        martingale_max_streak=3,
        martingale_reset_on_win=True,
    )
    async with AsyncSessionLocal() as s:
        s.add(cfg)
        await s.commit()

    # Seed MartingaleState: already 1 loss in (mid-ladder, last_stake=10).
    async with AsyncSessionLocal() as s:
        s.add(MartingaleState(
            parser_config_id=8802,
            current_streak=1,
            last_stake=10.0,
            last_outcome="lost",
        ))
        await s.commit()

    # Seed a settled LOSING TradeAttempt today with profit=-15.0.
    # daily_max_loss=15.0 => -(-15.0) >= 15.0 => limit reached.
    async with AsyncSessionLocal() as s:
        await insert_attempt(
            s,
            TradeAttempt(
                chat_id=-8802,
                parser_config_id=8802,
                asset="EURUSD",
                asset_raw="EURUSD",
                direction="call",
                duration_seconds=60,
                stake=10.0,
                trade_mode="live",
                status="lost",
                profit=-15.0,
                raw_text="BUY EURUSD 1m",
            ),
        )

    # GlobalSettings: kill switch off, pipeline active,
    # daily_max_loss=15.0 (exactly the loss already taken).
    gs = GlobalSettings(id=1)
    gs.kill_switch_engaged = False
    gs.pipeline_active = True
    gs.daily_max_loss = 15.0
    async with AsyncSessionLocal() as s:
        s.add(gs)
        await s.commit()

    signal = ParsedSignal(
        asset="EURUSD",
        direction="call",
        duration_seconds=60,
        stake=None,   # risk gate computes martingale stake from ladder
        fire_at=None,
        raw_text="BUY EURUSD 1m",
        parser_id="cfg-8802",
    )

    async with AsyncSessionLocal() as s:
        cfg_row = await s.get(ParserConfig, 8802)
        settings_row = await s.get(GlobalSettings, 1)
        assert cfg_row is not None
        assert settings_row is not None

        decision = await evaluate(
            session=s,
            signal=signal,
            parser_config=cfg_row,
            settings=settings_row,
            account_mode="DEMO",
            live_trading_enabled_env=False,
            broker_connected=True,
        )

    # --- Invariant assertions ---
    assert decision.allowed is False, (
        "daily_max_loss must block even a mid-ladder recovery trade; "
        f"got outcome={decision.outcome!r}, reason={decision.reason!r}"
    )
    assert "daily loss" in decision.reason.lower(), (
        f"block reason must mention 'daily loss'; got: {decision.reason!r}"
    )


# ===========================================================================
# Test 3 -- broker error mid-ladder does NOT advance MartingaleState
# ===========================================================================


@pytest.mark.asyncio
async def test_disconnect_mid_ladder_does_not_advance_state() -> None:
    """A broker problem mid-ladder ends as broker_error and does NOT
    advance MartingaleState.current_streak.

    A broker error must NEVER look like a loss to the ladder -- if it
    did, the operator would see their recovery sequence corrupted every
    time their feed went stale, and risk sizing would be wrong for the
    following real-money trades.

    Mirrors test_phase_tier0_health_gate.py::
    test_place_blocked_marks_broker_error_and_does_not_tick_martingale
    but frames the invariant explicitly.
    """
    from unittest.mock import AsyncMock, MagicMock, patch  # noqa: PLC0415

    from sqlmodel import select  # noqa: PLC0415

    from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt, insert_attempt  # noqa: PLC0415
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415
    from autotrader.services.parsers.base import ParsedSignal  # noqa: PLC0415
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        BrokerNotLive,
        QuotexManager,
    )
    from autotrader.services.risk_gate import RiskDecision  # noqa: PLC0415

    await init_db()

    # Seed MartingaleState: already 1 loss in (current_streak=1).
    async with AsyncSessionLocal() as s:
        s.add(MartingaleState(
            parser_config_id=8803,
            current_streak=1,
            last_stake=10.0,
            last_outcome="lost",
        ))
        await s.commit()

    # Seed a pending TradeAttempt (the recovery trade about to be placed).
    async with AsyncSessionLocal() as s:
        attempt = await insert_attempt(
            s,
            TradeAttempt(
                chat_id=-8803,
                parser_config_id=8803,
                asset="EURUSD_otc",
                asset_raw="EURUSD_otc",
                direction="call",
                duration_seconds=60,
                stake=20.0,   # martingale-multiplied stake
                trade_mode="live",
                status="pending",
                raw_text="BUY EURUSD_otc 1m",
            ),
        )
    assert attempt.id is not None

    # Build executor whose manager.assert_live raises BrokerNotLive.
    # Instance-scoped AsyncMock on the manager object -- NOT class-level
    # patching (avoids cross-test leakage as learned in Task 3).
    mgr = MagicMock(spec=QuotexManager)
    mgr.assert_live = AsyncMock(
        side_effect=BrokerNotLive(
            "stale_feed",
            asset="EURUSD_otc",
            age_seconds=30.0,
            threshold=10.0,
        )
    )
    # assets must include the resolved symbol so _place's _resolve_asset
    # passes it through to assert_live (the health gate under test). If
    # the broker symbol naming changes, update this or test 3 fails with
    # a misleading "asset unrecognized" instead of exercising the gate.
    mgr.assets = ("EURUSD_otc",)
    mgr._client = MagicMock()

    executor = TradeExecutor(manager=mgr, live_trading_enabled_env=False)

    signal = ParsedSignal(
        asset="EURUSD_otc",
        direction="call",
        duration_seconds=60,
        stake=20.0,
        fire_at=None,
        raw_text="BUY EURUSD_otc 1m",
        parser_id="cfg-8803",
    )
    # RiskDecision: outcome= (NOT allowed=); .allowed is a @property.
    decision = RiskDecision(
        outcome="allow",
        reason="passed",
        stake=20.0,
        trade_mode="live",
    )

    # Patch record_outcome at the executor module's import site so we
    # can confirm it is never called on the BrokerNotLive path.
    with patch(
        "autotrader.services.executor.record_outcome",
        new_callable=AsyncMock,
    ) as mock_record_outcome:
        await executor._place(attempt, signal, decision)

    # --- Invariant assertions ---

    # 1. Attempt status is broker_error with healthgate: prefix.
    async with AsyncSessionLocal() as s:
        stmt = select(TradeAttempt).where(TradeAttempt.id == attempt.id)
        result = await s.exec(stmt)  # type: ignore[call-overload]
        row = result.first()

    assert row is not None, "attempt row must still exist in DB"
    assert row.status == "broker_error", (
        f"broker error must set status=broker_error; got {row.status!r}"
    )
    assert row.error is not None
    assert "healthgate:" in row.error, (
        f"error must contain 'healthgate:' prefix; got: {row.error!r}"
    )
    assert "stale_feed" in row.error, (
        f"error must mention 'stale_feed'; got: {row.error!r}"
    )

    # 2. record_outcome was NEVER called -- the ladder must not advance.
    mock_record_outcome.assert_not_called()

    # 3. MartingaleState.current_streak is STILL 1 (unchanged in DB).
    async with AsyncSessionLocal() as s:
        state_after = await s.get(MartingaleState, 8803)
    assert state_after is not None
    assert state_after.current_streak == 1, (
        "broker error must NOT advance current_streak; "
        f"expected 1, got {state_after.current_streak}"
    )
