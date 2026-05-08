"""SQLite online backups.

Single-host autotraders are I/O-cheap to back up — SQLite ships an
official "online backup" API that copies pages from a live database
without acquiring a write lock for the whole copy. We call it through
``sqlite3.Connection.backup`` on a worker thread so a long copy
doesn't block the asyncio event loop.

The scheduler runs as a background task owned by the FastAPI lifespan
and stays off by default: a stock install never touches the disk for
backups until the operator opts in via
``AUTOTRADER_BACKUP_INTERVAL_SECONDS``. Retention is per-file count;
older files are pruned after each successful backup so the volume
size stays bounded.

Failure modes (disk full, permission denied, target ``.db`` missing)
log loudly and continue — a broken backup must never crash the live
trading process.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# File pattern — sortable so retention can drop the lexicographically
# smallest entries safely.
_BACKUP_PREFIX = "autotrader-"
_BACKUP_SUFFIX = ".db"


def _db_path_from_url(url: str) -> Path | None:
    """Extract the on-disk path from an aiosqlite SQLAlchemy URL.

    Returns ``None`` for memory / non-SQLite URLs — the caller should
    skip backups in those cases.
    """
    if not url.startswith("sqlite") or ":memory:" in url:
        return None
    raw = url.split("///", 1)[-1]
    if not raw:
        return None
    return Path(raw).resolve()


def _timestamped_name(now: datetime) -> str:
    """Return an ISO-8601 backup filename suitable for sorted listing."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{_BACKUP_PREFIX}{stamp}{_BACKUP_SUFFIX}"


def _existing_backups(target_dir: Path) -> list[Path]:
    if not target_dir.is_dir():
        return []
    files = [
        p for p in target_dir.iterdir()
        if p.is_file()
        and p.name.startswith(_BACKUP_PREFIX)
        and p.name.endswith(_BACKUP_SUFFIX)
    ]
    files.sort()
    return files


def _prune(target_dir: Path, retain: int) -> int:
    """Delete the oldest backups beyond ``retain``. Returns count removed."""
    if retain <= 0:
        return 0
    files = _existing_backups(target_dir)
    excess = len(files) - retain
    if excess <= 0:
        return 0
    removed = 0
    for path in files[:excess]:
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
    return removed


def _run_backup_sync(source: Path, dest: Path) -> None:
    """Blocking online-backup. Caller runs us on a worker thread."""
    src = sqlite3.connect(str(source))
    try:
        # Direct connect to the dest path — sqlite creates an empty file.
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


async def run_backup(
    *,
    db_url: str,
    target_dir: Path | str,
    retain: int = 24,
    now: datetime | None = None,
) -> Path | None:
    """Take one online backup. Returns the new file's path (or ``None``).

    Returns ``None`` when the source database isn't a file-backed
    SQLite (in-memory test runs, alternative engines).
    """
    source = _db_path_from_url(db_url)
    if source is None:
        return None
    if not source.exists():
        log.warning("backup.source.missing", path=str(source))
        return None

    target = Path(target_dir)
    # Run filesystem prep on the worker thread so the async hot path
    # never touches blocking I/O (also why ASYNC240 isn't suppressed
    # — we comply rather than silence the lint).
    await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
    dest = target / _timestamped_name(now or datetime.now(UTC))

    # ``Connection.backup`` blocks on file I/O. Run on a worker so the
    # event loop keeps serving requests; aiosqlite uses the same
    # pattern internally.
    await asyncio.to_thread(_run_backup_sync, source, dest)
    pruned = _prune(target, retain)
    log.info(
        "backup.taken",
        path=str(dest),
        bytes=dest.stat().st_size,
        retained=retain,
        pruned=pruned,
    )
    return dest


class BackupScheduler:
    """Periodic backup loop, owned by the FastAPI lifespan."""

    def __init__(
        self,
        *,
        db_url: str,
        target_dir: Path | str,
        interval_seconds: int,
        retain: int,
    ) -> None:
        self._db_url = db_url
        self._target_dir = Path(target_dir)
        self._interval = interval_seconds
        self._retain = retain
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        with contextlib.suppress(BaseException):
            await self._task
        self._task = None

    async def _run(self) -> None:
        log.info(
            "backup.scheduler.started",
            interval_s=self._interval,
            retain=self._retain,
            target=str(self._target_dir),
        )
        while not self._stopping.is_set():
            try:
                await run_backup(
                    db_url=self._db_url,
                    target_dir=self._target_dir,
                    retain=self._retain,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - log + continue
                log.exception("backup.scheduler.failed", error=str(exc))
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._interval,
                )
            except TimeoutError:
                continue
        log.info("backup.scheduler.stopped")
