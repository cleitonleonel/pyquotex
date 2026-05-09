"""Shared model utilities."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Timezone-aware UTC ``now`` — use as a SQLModel ``default_factory``."""
    return datetime.now(UTC)
