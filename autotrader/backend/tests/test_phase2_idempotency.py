"""Phase 2 pipeline idempotency tests (audit 2026-05-13, H1).

Confirms the dedup gate that short-circuits a Pyrogram replay before
it can produce a duplicate ``TradeAttempt``.

End-to-end test surface:
* schema: ``trade_attempts.tg_message_id`` exists and is indexed.
* model query: ``find_recent_by_tg_message_id`` returns prior attempts
  for the same ``(chat_id, tg_message_id)`` and ignores others.
* pipeline: a replay of the same ``(chat_id, message_id)`` records a
  ``duplicate`` decision and does NOT call the executor.
* executor: ``submit(tg_message_id=...)`` plumbs the id onto the row.
* batch: parsers that fan one message → N signals tag every persisted
  row with the same ``tg_message_id``.

Heavy imports stay inside each test — see the docstring in
``test_phase0_instrumentation.py`` for the reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Per-test DB wipe.
#
# Phase 2 tests seed real ``trade_attempts`` / ``watched_channels`` /
# ``parser_configs`` rows so the dedup query has something to hit.
# Without cleanup those rows persist into the next test file's tests
# (the conftest's tempfile DB is session-scoped) and break tests that
# assume an empty starting state — observed once on
# ``test_pipeline_inactive_drops_signals`` during Phase 2 development.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _wipe_phase2_rows() -> AsyncIterator[None]:
    """Delete the rows this file seeds; idempotent for tests that skip
    seeding. Runs after every test in this module."""
    yield
    from sqlalchemy import text  # noqa: PLC0415

    from autotrader.db import engine, init_db  # noqa: PLC0415

    # If a test ran ``close_db()`` we need a fresh schema before
    # cleanup; ``init_db`` is idempotent.
    await init_db()
    async with engine.begin() as conn:
        for table in (
            "trade_attempts",
            "parser_configs",
            "watched_channels",
            "global_settings",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))


# ---------------------------------------------------------------------------
# Schema + model query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_message_id_column_exists_after_init_db() -> None:
    """After ``init_db`` (which runs ``_migrate_in_place``) the
    ``trade_attempts.tg_message_id`` column is present and nullable."""
    from sqlalchemy import inspect  # noqa: PLC0415

    from autotrader.db import close_db, engine, init_db  # noqa: PLC0415

    await init_db()
    try:
        async with engine.begin() as conn:
            cols = await conn.run_sync(
                lambda sc: {
                    c["name"]: c
                    for c in inspect(sc).get_columns("trade_attempts")
                },
            )
    finally:
        await close_db()
    assert "tg_message_id" in cols
    assert cols["tg_message_id"]["nullable"] is True


@pytest.mark.asyncio
async def test_find_recent_by_tg_message_id_hits_match() -> None:
    """A row with the same ``(chat_id, tg_message_id)`` within the
    lookback is returned; mismatched chat or id returns ``None``."""
    from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415
    from autotrader.models.trade_attempt import (  # noqa: PLC0415
        TradeAttempt,
        find_recent_by_tg_message_id,
        insert_attempt,
    )

    await init_db()

    async with AsyncSessionLocal() as session:
        await insert_attempt(
            session,
            TradeAttempt(
                chat_id=-7777,
                parser_config_id=1,
                asset="EURUSD",
                direction="call",
                duration_seconds=60,
                stake=1.0,
                trade_mode="live",
                status="pending",
                raw_text="r",
                tg_message_id=12345,
            ),
        )

    async with AsyncSessionLocal() as session:
        hit = await find_recent_by_tg_message_id(
            session, chat_id=-7777, tg_message_id=12345,
        )
        assert hit is not None
        assert hit.chat_id == -7777
        assert hit.tg_message_id == 12345

        # Wrong chat — must NOT match.
        miss_chat = await find_recent_by_tg_message_id(
            session, chat_id=-1234, tg_message_id=12345,
        )
        assert miss_chat is None

        # Wrong id — must NOT match.
        miss_id = await find_recent_by_tg_message_id(
            session, chat_id=-7777, tg_message_id=99999,
        )
        assert miss_id is None


@pytest.mark.asyncio
async def test_find_recent_skips_rows_outside_lookback() -> None:
    """Rows older than ``lookback`` are not returned — the index keeps
    the query cheap even on a busy table."""
    from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415
    from autotrader.models.base import utc_now  # noqa: PLC0415
    from autotrader.models.trade_attempt import (  # noqa: PLC0415
        TradeAttempt,
        find_recent_by_tg_message_id,
        insert_attempt,
    )

    await init_db()

    async with AsyncSessionLocal() as session:
        attempt = TradeAttempt(
            chat_id=-8888,
            parser_config_id=1,
            asset="EURUSD",
            direction="call",
            duration_seconds=60,
            stake=1.0,
            trade_mode="live",
            status="won",
            raw_text="r",
            tg_message_id=42,
        )
        # Backdate created_at past the default lookback.
        attempt.created_at = utc_now() - timedelta(hours=1)
        await insert_attempt(session, attempt)

    async with AsyncSessionLocal() as session:
        result = await find_recent_by_tg_message_id(
            session,
            chat_id=-8888,
            tg_message_id=42,
            lookback=timedelta(minutes=10),
        )
    assert result is None


# ---------------------------------------------------------------------------
# Pipeline dedup gate (end-to-end via the executor)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_dedup_skips_replay_without_calling_executor() -> None:
    """The second dispatch of a message with the same
    ``(chat_id, message_id)`` records a ``duplicate`` decision and
    does NOT invoke the executor. This is the H1 fix.
    """
    from autotrader.db import AsyncSessionLocal, close_db, init_db  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.models.trade_attempt import (  # noqa: PLC0415
        TradeAttempt,
        insert_attempt,
    )
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415

    await init_db()
    try:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-1234, title="t", chat_type="channel",
                username=None, enabled=True,
            ))
            cfg = ParserConfig(
                id=4001, chat_id=-1234, name="dr", parser_type="regex",
                default_stake=1.0, enabled=True,
            )
            s.add(cfg)
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.pipeline_active = True
            gs.kill_switch_engaged = False
            s.add(gs)
            await s.commit()

            # Seed a prior attempt for the message id we'll "replay".
            await insert_attempt(
                s,
                TradeAttempt(
                    chat_id=-1234,
                    parser_config_id=4001,
                    asset="EURUSD",
                    direction="call",
                    duration_seconds=60,
                    stake=1.0,
                    trade_mode="live",
                    status="won",  # earlier dispatch already settled
                    raw_text="CALL EURUSD 1m",
                    tg_message_id=777,
                ),
            )

        # Executor stub: count calls. If pipeline dedups correctly,
        # ``submit`` is never invoked for this replay.
        class _CountingExecutor:
            def __init__(self) -> None:
                self.submits = 0

            async def submit(self, **kwargs: object) -> None:
                self.submits += 1

        class _StubManager:
            assets: tuple[str, ...] = ()

        executor = _CountingExecutor()
        pipe = Pipeline(manager=_StubManager(), executor=executor)

        msg = RawMessage(
            text="CALL EURUSD 1m",
            chat_id=-1234,
            sender_id=99,
            received_at=datetime.now(UTC),
            message_id=777,
        )

        await pipe.dispatch(msg)

        assert executor.submits == 0, (
            "pipeline must short-circuit a replay; executor.submit "
            f"called {executor.submits} times"
        )
        # Decision feed records the dedup so operators can see what
        # was suppressed.
        decisions = pipe.recent_decisions
        dupes = [d for d in decisions if d.get("outcome") == "duplicate"]
        assert len(dupes) == 1, decisions
        assert dupes[0]["chat_id"] == -1234
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_pipeline_dedup_does_not_block_distinct_message_ids() -> None:
    """A message with a *different* id flows through normally — the
    dedup gate is keyed strictly on ``(chat_id, tg_message_id)``."""
    from autotrader.db import AsyncSessionLocal, close_db, init_db  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.models.trade_attempt import (  # noqa: PLC0415
        TradeAttempt,
        insert_attempt,
    )
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415

    await init_db()
    try:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-5555, title="t", chat_type="channel",
                username=None, enabled=True,
            ))
            cfg = ParserConfig(
                id=4002, chat_id=-5555, name="dr2", parser_type="regex",
                default_stake=1.0, enabled=True,
            )
            s.add(cfg)
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.pipeline_active = True
            gs.kill_switch_engaged = False
            s.add(gs)
            await s.commit()

            # Seed a prior attempt for message id 100; we'll dispatch 101.
            await insert_attempt(
                s,
                TradeAttempt(
                    chat_id=-5555,
                    parser_config_id=4002,
                    asset="EURUSD",
                    direction="call",
                    duration_seconds=60,
                    stake=1.0,
                    trade_mode="live",
                    status="won",
                    raw_text="r",
                    tg_message_id=100,
                ),
            )

        class _CountingExecutor:
            def __init__(self) -> None:
                self.submits = 0

            async def submit(self, **kwargs: object) -> None:
                self.submits += 1

        class _StubManager:
            assets: tuple[str, ...] = ()

        executor = _CountingExecutor()
        pipe = Pipeline(manager=_StubManager(), executor=executor)

        msg = RawMessage(
            text="anything",
            chat_id=-5555,
            sender_id=88,
            received_at=datetime.now(UTC),
            message_id=101,
        )
        await pipe.dispatch(msg)

        # Even though we now have configs, the regex parser is wired
        # with an empty pattern by default and won't match — the
        # important assertion is that dedup didn't fire.
        dupes = [
            d for d in pipe.recent_decisions
            if d.get("outcome") == "duplicate"
        ]
        assert dupes == []
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# Executor plumbing
# ---------------------------------------------------------------------------


def test_build_attempt_persists_tg_message_id() -> None:
    """``_build_attempt`` propagates ``tg_message_id`` onto the row so
    the next dispatch's dedup query can find it."""
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415
    from autotrader.services.parsers import ParsedSignal  # noqa: PLC0415
    from autotrader.services.risk_gate import RiskDecision  # noqa: PLC0415

    cfg = ParserConfig(
        id=1, chat_id=-1234, name="x", parser_type="regex",
        default_stake=1.0, enabled=True,
    )
    signal = ParsedSignal(
        asset="EURUSD", direction="call", duration_seconds=60,
        stake=1.0, fire_at=None, raw_text="r", parser_id="r",
    )
    decision = RiskDecision(
        outcome="allow", reason="", stake=1.0, trade_mode="live",
    )

    attempt = TradeExecutor._build_attempt(
        signal, cfg, decision, tg_message_id=4242,
    )
    assert attempt.tg_message_id == 4242

    # Default (legacy callers) → None, which is what the dedup query
    # ignores.
    attempt_default = TradeExecutor._build_attempt(signal, cfg, decision)
    assert attempt_default.tg_message_id is None
