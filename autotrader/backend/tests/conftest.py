"""Pytest configuration.

Sets the env vars the app reads at *import* time (passcode + Fernet key)
so tests don't need a real ``.env`` file. We do this here, not in a
fixture, because pydantic-settings reads env on module load.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

os.environ.setdefault("AUTOTRADER_PASSCODE", "test-passcode")
os.environ.setdefault("AUTOTRADER_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("AUTOTRADER_DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTOTRADER_LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("AUTOTRADER_CORS_ORIGINS", "*")
