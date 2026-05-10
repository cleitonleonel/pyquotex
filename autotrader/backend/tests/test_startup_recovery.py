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


def _table_columns(table: str) -> dict[str, dict[str, object]]:
    """Return ``{col_name: {dflt_value, notnull, type}}`` from PRAGMA table_info."""
    from sqlalchemy import text  # noqa: PLC0415

    from autotrader.db import engine  # noqa: PLC0415

    async def _do() -> dict[str, dict[str, object]]:
        async with engine.begin() as conn:
            rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        return {
            r[1]: {"type": r[2], "notnull": r[3], "dflt_value": r[4]}
            for r in rows
        }

    return asyncio.new_event_loop().run_until_complete(_do())


def test_migration_adds_winning_streak_columns_to_legacy_parser_configs(
    fake_quotex: None,
) -> None:
    """A pre-existing ``parser_configs`` table missing the
    ``winning_streak_*`` columns gets ALTERed in place when ``init_db``
    re-runs — no data loss, no operator action.

    Repro: enter the lifespan once (creates the current-shape table),
    seed a parser_configs row, ``DROP COLUMN`` the two new columns to
    fake a pre-Task-1 install, then re-enter the lifespan and assert
    the new columns are back with the right defaults applied to the
    seeded row.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.parser_config import create_config  # noqa: PLC0415

    # Step A: bootstrap fresh schema + seed a row.
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
                    "martingale_enabled": False,
                    "martingale_multiplier": 2.0,
                    "martingale_max_streak": 0,
                    "martingale_reset_on_win": True,
                    "martingale_auto_recovery": False,
                    "enabled": True,
                    "asset_aliases": {},
                    "aggregate_window_seconds": 0,
                    "timezone": "UTC",
                    "timezone_offset_minutes": 0,
                },
            )
            return int(cfg.id or 0)

    with TestClient(app):
        cfg_id = asyncio.new_event_loop().run_until_complete(_seed())

    # Step B: drop the new columns to simulate a legacy DB.
    async def _drop_cols() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE parser_configs DROP COLUMN winning_streak_enabled"),
            )
            await conn.execute(
                text("ALTER TABLE parser_configs DROP COLUMN winning_streak_max_level"),
            )

    asyncio.new_event_loop().run_until_complete(_drop_cols())

    # Sanity: confirm the drop took effect before we re-run init_db.
    pre = _table_columns("parser_configs")
    assert "winning_streak_enabled" not in pre
    assert "winning_streak_max_level" not in pre

    # Step C: re-enter the lifespan; this fires _migrate_in_place.
    with TestClient(app):
        pass

    # Step D: assert the columns are back with the right defaults, and
    # the existing row was backfilled to those defaults.
    post = _table_columns("parser_configs")
    assert "winning_streak_enabled" in post, (
        "migration must re-add winning_streak_enabled"
    )
    assert "winning_streak_max_level" in post, (
        "migration must re-add winning_streak_max_level"
    )

    async def _read_row() -> tuple[int, int]:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT winning_streak_enabled, winning_streak_max_level "
                        "FROM parser_configs WHERE id = :id",
                    ),
                    {"id": cfg_id},
                )
            ).one()
        return int(row[0]), int(row[1])

    enabled, max_level = asyncio.new_event_loop().run_until_complete(_read_row())
    # SQLite stores BOOLEAN as INTEGER; default 0 == False.
    assert enabled == 0, (
        f"winning_streak_enabled must default to 0/False on legacy rows; got {enabled}"
    )
    assert max_level == 2, (
        f"winning_streak_max_level must default to 2 on legacy rows; got {max_level}"
    )


def test_migration_adds_winning_streak_columns_to_legacy_martingale_states(
    fake_quotex: None,
) -> None:
    """A pre-existing ``martingale_states`` table missing
    ``current_win_streak`` / ``last_payout`` gets ALTERed in place — the
    Paroli ladder runtime columns Task 1 added must materialise on
    upgrade without losing the existing losing-streak counters.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import create_config  # noqa: PLC0415

    # Step A: bootstrap + seed a parser_configs row + a martingale_states row.
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
            cfg_id = int(cfg.id or 0)
            row = MartingaleState(
                parser_config_id=cfg_id,
                current_streak=2,
                last_stake=4.0,
                last_outcome="lost",
            )
            s.add(row)
            await s.commit()
            return cfg_id

    with TestClient(app):
        cfg_id = asyncio.new_event_loop().run_until_complete(_seed())

    # Step B: drop the new columns to simulate a legacy DB.
    async def _drop_cols() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE martingale_states DROP COLUMN current_win_streak"),
            )
            await conn.execute(
                text("ALTER TABLE martingale_states DROP COLUMN last_payout"),
            )

    asyncio.new_event_loop().run_until_complete(_drop_cols())

    pre = _table_columns("martingale_states")
    assert "current_win_streak" not in pre
    assert "last_payout" not in pre

    # Step C: re-enter the lifespan; this fires _migrate_in_place.
    with TestClient(app):
        pass

    # Step D: assert the columns are back; the legacy row's existing
    # losing-streak counters survive; the new columns default to 0.
    post = _table_columns("martingale_states")
    assert "current_win_streak" in post, (
        "migration must re-add current_win_streak"
    )
    assert "last_payout" in post, "migration must re-add last_payout"

    async def _read_row() -> tuple[int, int, float, int, float]:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT current_streak, current_win_streak, last_payout, "
                        "       last_stake, last_stake "
                        "FROM martingale_states WHERE parser_config_id = :id",
                    ),
                    {"id": cfg_id},
                )
            ).one()
        return (
            int(row[0]),
            int(row[1]),
            float(row[2]),
            int(row[3]),
            float(row[4]),
        )

    (
        current_streak,
        current_win_streak,
        last_payout,
        _,
        _,
    ) = asyncio.new_event_loop().run_until_complete(_read_row())
    # Existing data preserved.
    assert current_streak == 2, (
        f"legacy current_streak must survive migration; got {current_streak}"
    )
    # New columns default to 0 / 0.0.
    assert current_win_streak == 0, (
        f"current_win_streak must default to 0 on legacy rows; got {current_win_streak}"
    )
    assert last_payout == 0.0, (
        f"last_payout must default to 0.0 on legacy rows; got {last_payout}"
    )
