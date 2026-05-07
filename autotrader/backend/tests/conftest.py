"""Pytest configuration.

Sets the env vars the app reads at *import* time (passcode + Fernet key)
so tests don't need a real ``.env`` file. We do this here, not in a
fixture, because pydantic-settings reads env on module load.

A file-backed SQLite (under ``tempfile``) is used instead of
``:memory:`` because ``aiosqlite`` with ``:memory:`` gives each
connection its own database — tables created in ``init_db`` would not
be visible to subsequent route requests.
"""

from __future__ import annotations

import atexit
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_db_dir = Path(tempfile.mkdtemp(prefix="autotrader-tests-"))
_db_path = _db_dir / "test.db"

os.environ.setdefault("AUTOTRADER_PASSCODE", "test-passcode")
os.environ.setdefault("AUTOTRADER_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("AUTOTRADER_DB_URL", f"sqlite+aiosqlite:///{_db_path}")
os.environ.setdefault("AUTOTRADER_LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("AUTOTRADER_CORS_ORIGINS", "*")


@atexit.register
def _cleanup_test_db() -> None:
    try:
        _db_path.unlink(missing_ok=True)
        _db_dir.rmdir()
    except OSError:
        pass
