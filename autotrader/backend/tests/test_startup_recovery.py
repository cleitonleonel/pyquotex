"""Regression tests for stale-encryption recovery on startup.

A common operator footgun: the ``AUTOTRADER_FERNET_KEY`` in ``.env``
gets rotated (or accidentally regenerated) while the SQLite volume
already contains rows encrypted with the old key. Without recovery
the lifespan crashes during ``decrypt`` and the whole API container
won't start. We've seen this in the wild — the fix is to clear the
unreadable row and continue, surfacing the situation in logs.

We exercise the lifespan in two passes: in pass 1 the running app
encrypts a row with the *current* key; we then overwrite the row's
ciphertext with garbage (simulates a key change without bouncing
SQLModel's metadata) and re-enter the lifespan to assert the
unreadable row is cleared and the app boots cleanly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fake_quotex(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stub Quotex so the lifespan auto-connect doesn't actually call out."""
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )
    yield


@pytest.fixture(autouse=True)
def _wipe_after_each_test() -> Iterator[None]:
    """Strip any rows seeded by these tests so they don't leak into the
    later test_stats / test_pipeline modules — those count rows
    globally and a stray ``trade_attempts`` row is enough to flip an
    assertion."""
    yield

    async def _wipe() -> None:
        from sqlmodel import delete  # noqa: PLC0415

        from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
        from autotrader.models.broker_credentials import (  # noqa: PLC0415
            BrokerCredentials,
        )
        from autotrader.models.martingale_state import (  # noqa: PLC0415
            MartingaleState,
        )
        from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
        from autotrader.models.telegram_session import (  # noqa: PLC0415
            TelegramSession,
        )
        from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

        async with AsyncSessionLocal() as s:
            for model in (
                TradeAttempt,
                MartingaleState,
                ParserConfig,
                BrokerCredentials,
                TelegramSession,
            ):
                await s.exec(delete(model))  # type: ignore[call-overload]
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_wipe())


def test_corrupt_broker_creds_do_not_crash_startup(
    fake_quotex: None,
) -> None:
    """A row whose ciphertext can't be decrypted is cleared on startup."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.broker_credentials import (  # noqa: PLC0415
        load_credentials,
        upsert_credentials,
    )

    # Bootstrap the schema by entering the lifespan once, then seed.
    with TestClient(app):
        async def _seed() -> None:
            async with AsyncSessionLocal() as s:
                await upsert_credentials(s, "x@y.com", "secret", "PRACTICE")

        asyncio.new_event_loop().run_until_complete(_seed())

    # Corrupt the ciphertext directly (simulates a key change).
    async def _corrupt() -> None:
        async with AsyncSessionLocal() as s:
            row = await load_credentials(s)
            assert row is not None
            row.email_enc = b"not-a-valid-fernet-token"
            row.password_enc = b"not-a-valid-fernet-token"
            s.add(row)
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_corrupt())

    # Re-enter the lifespan: must NOT raise; row is wiped.
    with TestClient(app):
        async def _check() -> None:
            async with AsyncSessionLocal() as s:
                assert await load_credentials(s) is None

        asyncio.new_event_loop().run_until_complete(_check())


def test_corrupt_telegram_session_does_not_crash_startup() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.telegram_session import (  # noqa: PLC0415
        load_session,
        upsert_session,
    )

    with TestClient(app):
        async def _seed() -> None:
            async with AsyncSessionLocal() as s:
                await upsert_session(
                    s,
                    phone="+15550100",
                    session_string="FAKE_SESSION",
                    user_id=1,
                    username="me",
                    first_name="Me",
                )

        asyncio.new_event_loop().run_until_complete(_seed())

    async def _corrupt() -> None:
        async with AsyncSessionLocal() as s:
            row = await load_session(s)
            assert row is not None
            row.session_string_enc = b"not-a-valid-fernet-token"
            s.add(row)
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_corrupt())

    with TestClient(app):
        async def _check() -> None:
            async with AsyncSessionLocal() as s:
                assert await load_session(s) is None

        asyncio.new_event_loop().run_until_complete(_check())


def _seed_pending(
    *,
    placed_at: datetime | None,
    duration_seconds: int,
    parser_config_id: int = 1,
) -> int:
    """Insert a pending TradeAttempt row and return its id."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt, insert_attempt  # noqa: PLC0415

    async def _do() -> int:
        async with AsyncSessionLocal() as s:
            row = await insert_attempt(
                s,
                TradeAttempt(
                    chat_id=-1001,
                    parser_config_id=parser_config_id,
                    asset="EURUSD",
                    asset_raw="EURUSD",
                    direction="call",
                    duration_seconds=duration_seconds,
                    stake=1.0,
                    trade_mode="live",
                    fire_at=None,
                    status="pending",
                    placed_at=placed_at,
                ),
            )
            return int(row.id or 0)

    return asyncio.new_event_loop().run_until_complete(_do())


def _read_status(attempt_id: int) -> tuple[str, str | None]:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    async def _do() -> tuple[str, str | None]:
        async with AsyncSessionLocal() as s:
            row = await s.get(TradeAttempt, attempt_id)
            assert row is not None
            return row.status, row.error

    return asyncio.new_event_loop().run_until_complete(_do())


def test_reconcile_pending_placed_at_none_expires_immediately(
    fake_quotex: None,
) -> None:
    """A pending row with placed_at=None means the broker never accepted
    the order. Today's behaviour — mark expired immediately — is
    correct for this case."""
    from autotrader.main import app  # noqa: PLC0415

    with TestClient(app):
        attempt_id = _seed_pending(placed_at=None, duration_seconds=60)

    # Re-enter lifespan to trigger reconcile_pending.
    with TestClient(app):
        pass

    status_, error = _read_status(attempt_id)
    assert status_ == "expired"
    assert error is not None
    assert "watcher lost on restart" in error


def test_reconcile_pending_in_flight_stays_pending(
    fake_quotex: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending row whose placed_at + duration is still in the future
    represents a trade the broker is still running. We must NOT mark
    it expired; the deferred reconciler waits for the natural settle
    window before deciding."""
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.services import executor as exec_mod  # noqa: PLC0415

    monkeypatch.setattr(exec_mod, "_RECONCILE_SLACK_SECONDS", 0)

    placed = datetime.now(UTC) - timedelta(seconds=10)
    with TestClient(app):
        attempt_id = _seed_pending(placed_at=placed, duration_seconds=300)

    with TestClient(app):
        pass

    status_, error = _read_status(attempt_id)
    assert status_ == "pending", (
        f"in-flight rows must stay pending; got status={status_!r}, error={error!r}"
    )


def test_reconcile_pending_post_window_marks_expired_with_clearer_note(
    fake_quotex: None,
) -> None:
    """A pending row whose settle window has clearly passed gets
    expired with a clearer note than today's "watcher lost on
    restart" — the broker has already settled but pyquotex can't tie
    it back."""
    from autotrader.main import app  # noqa: PLC0415

    placed = datetime.now(UTC) - timedelta(seconds=600)
    with TestClient(app):
        attempt_id = _seed_pending(placed_at=placed, duration_seconds=60)

    with TestClient(app):
        pass

    status_, error = _read_status(attempt_id)
    assert status_ == "expired"
    assert error is not None
    assert "settle window passed" in error
    assert "check broker history" in error.lower()


def test_reconcile_pending_does_not_tick_martingale(
    fake_quotex: None,
) -> None:
    """The deferred reconciler must NOT call martingale_state.record_outcome —
    we don't know the outcome, so the ladder stays where it was at
    the loss/win that preceded the restart."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import create_config  # noqa: PLC0415

    async def _seed() -> int:
        async with AsyncSessionLocal() as s:
            cfg = await create_config(
                s,
                chat_id=-1001,
                payload={
                    "name": "p",
                    "priority": 100,
                    "parser_type": "template",
                    "parser_config": {"template": "{DIRECTION} {ASSET}"},
                    "default_stake": 1.0,
                    "default_duration_seconds": 60,
                    "trade_mode": "live",
                    "martingale_enabled": True,
                    "martingale_multiplier": 2.0,
                    "martingale_max_streak": 3,
                    "martingale_reset_on_win": True,
                    "martingale_auto_recovery": False,
                    "enabled": True,
                    "asset_aliases": {},
                    "aggregate_window_seconds": 0,
                    "timezone": "UTC",
                    "timezone_offset_minutes": 0,
                },
            )
            row = MartingaleState(
                parser_config_id=cfg.id or 0,
                current_streak=2,
                last_outcome="lost",
                last_stake=4.0,
            )
            s.add(row)
            await s.commit()
            return int(cfg.id or 0)

    with TestClient(app):
        cfg_id = asyncio.new_event_loop().run_until_complete(_seed())
        placed = datetime.now(UTC) - timedelta(seconds=600)
        _seed_pending(placed_at=placed, duration_seconds=60, parser_config_id=cfg_id)

    with TestClient(app):
        pass

    async def _read_state() -> int:
        async with AsyncSessionLocal() as s:
            row = await s.get(MartingaleState, cfg_id)
            assert row is not None
            return row.current_streak

    streak = asyncio.new_event_loop().run_until_complete(_read_state())
    assert streak == 2, f"reconcile must not touch martingale ladder; streak={streak}"
