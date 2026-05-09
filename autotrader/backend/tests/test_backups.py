"""SQLite online-backup tests.

Each test drives the public surface (``run_backup`` /
``BackupScheduler``) with a temporary on-disk SQLite database so
behaviour matches what production sees. The thread-pool offload is
left in place — its behaviour is covered indirectly by every test
that asserts a backup file exists after ``run_backup`` returns.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autotrader.services.backups import (
    BackupScheduler,
    _existing_backups,
    _timestamped_name,
    run_backup,
)


def _seed_db(path: Path) -> None:
    """Build a tiny SQLite DB at ``path`` with a known table + row."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE notes (msg TEXT)")
        conn.execute("INSERT INTO notes VALUES (?)", ("hello",))
        conn.commit()
    finally:
        conn.close()


def _read_first_note(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT msg FROM notes").fetchone()
        return row[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_backup — happy path + failure modes
# ---------------------------------------------------------------------------


async def test_run_backup_writes_a_consistent_copy(tmp_path: Path) -> None:
    """The dump is a real SQLite file with the same row count as the source."""
    src = tmp_path / "autotrader.db"
    target = tmp_path / "backups"
    _seed_db(src)

    url = f"sqlite+aiosqlite:///{src}"
    out = await run_backup(db_url=url, target_dir=target)

    assert out is not None
    assert out.exists()
    assert out.parent == target
    assert _read_first_note(out) == "hello"


async def test_run_backup_skips_in_memory_engine(tmp_path: Path) -> None:
    """Memory URLs are unbacked-up by design — return None, no writes."""
    out = await run_backup(
        db_url="sqlite+aiosqlite:///:memory:",
        target_dir=tmp_path / "backups",
    )
    assert out is None
    assert not (tmp_path / "backups").exists()


async def test_run_backup_handles_missing_source(tmp_path: Path) -> None:
    """A db_url whose file doesn't exist yet must not crash startup."""
    out = await run_backup(
        db_url=f"sqlite+aiosqlite:///{tmp_path}/never-created.db",
        target_dir=tmp_path / "backups",
    )
    assert out is None


async def test_run_backup_prunes_to_retention_count(tmp_path: Path) -> None:
    """Retention keeps only the latest ``retain`` files."""
    src = tmp_path / "autotrader.db"
    target = tmp_path / "backups"
    _seed_db(src)
    url = f"sqlite+aiosqlite:///{src}"

    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Pre-seed three "older" backups so we exercise the prune path
    # rather than wait for new files to roll in via the scheduler.
    for i in range(3):
        target.mkdir(parents=True, exist_ok=True)
        stale = target / _timestamped_name(base + timedelta(seconds=i))
        stale.write_bytes(b"")  # the prune step doesn't validate contents

    # The new backup is timestamped *now* so it sorts last; retain=2
    # should drop the two oldest of the four total files.
    out = await run_backup(db_url=url, target_dir=target, retain=2)
    assert out is not None

    remaining = _existing_backups(target)
    assert len(remaining) == 2
    # The newest (just-created) file must survive.
    assert out in remaining


async def test_run_backup_zero_retention_keeps_everything(tmp_path: Path) -> None:
    src = tmp_path / "autotrader.db"
    target = tmp_path / "backups"
    _seed_db(src)
    url = f"sqlite+aiosqlite:///{src}"

    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Pin the timestamps so two same-second invocations don't collide
    # on the second-resolution filename.
    out_a = await run_backup(db_url=url, target_dir=target, retain=0, now=base)
    out_b = await run_backup(
        db_url=url, target_dir=target, retain=0,
        now=base + timedelta(seconds=1),
    )
    assert out_a is not None
    assert out_b is not None
    # ``retain=0`` is documented as "no cap" — the older file stays.
    files = _existing_backups(target)
    assert len(files) == 2


# ---------------------------------------------------------------------------
# BackupScheduler — start / stop / disabled-by-default
# ---------------------------------------------------------------------------


async def test_scheduler_disabled_when_interval_is_zero(tmp_path: Path) -> None:
    sched = BackupScheduler(
        db_url=f"sqlite+aiosqlite:///{tmp_path}/x.db",
        target_dir=tmp_path / "backups",
        interval_seconds=0,
        retain=5,
    )
    assert sched.enabled is False
    sched.start()  # idempotent no-op
    await sched.stop()
    assert not (tmp_path / "backups").exists()


async def test_scheduler_runs_one_backup_then_stops(tmp_path: Path) -> None:
    """Starting the loop with a long interval still does the first run."""
    src = tmp_path / "autotrader.db"
    _seed_db(src)
    target = tmp_path / "backups"

    sched = BackupScheduler(
        db_url=f"sqlite+aiosqlite:///{src}",
        target_dir=target,
        interval_seconds=10,  # next run won't happen during the test
        retain=5,
    )
    sched.start()
    # Yield until the first backup lands. The loop runs on the same
    # event loop so a short ``wait_for`` is plenty.
    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        if _existing_backups(target):
            break
    await sched.stop()
    assert _existing_backups(target), "scheduler did not produce a backup"
