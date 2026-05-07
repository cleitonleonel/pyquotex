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
