"""JSON utility with a graceful fallback from ``orjson`` to stdlib ``json``.

Keeps compatibility with platforms (Termux/Android) where native
extensions cannot be compiled. ``orjson`` is the hot-path winner because
it returns ``bytes`` directly — no extra encode step.

Public surface
--------------
``HAS_ORJSON`` : ``True`` when the fast path is available.
``loads(data)`` : Parse JSON from bytes or str.
``dumps(obj)`` -> ``bytes`` : Serialize to bytes (hot path).
``dumps_str(obj)`` -> ``str`` : Serialize to str (Socket.IO frames built by concat).
``dumps_bytes(obj)`` -> ``bytes`` : Explicit alias for :func:`dumps`.
"""

from __future__ import annotations

import json as _stdlib_json
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import orjson as _orjson  # type: ignore[import-not-found]

    HAS_ORJSON: bool = True
except ImportError:  # pragma: no cover - exercised only without orjson
    _orjson = None  # type: ignore[assignment]
    HAS_ORJSON = False
    logger.info(
        "orjson not installed; falling back to stdlib json. "
        "Install pyquotex[fast] for a ~3x speedup on hot paths."
    )


def loads(data: bytes | str) -> Any:
    """Parse JSON from bytes or str."""
    if HAS_ORJSON:
        return _orjson.loads(data)
    return _stdlib_json.loads(data)


def dumps(obj: Any) -> bytes:
    """Serialize ``obj`` to JSON bytes."""
    if HAS_ORJSON:
        return _orjson.dumps(obj)
    return _stdlib_json.dumps(obj, separators=(",", ":")).encode("utf-8")


def dumps_bytes(obj: Any) -> bytes:
    """Explicit alias for :func:`dumps` — emphasizes the bytes hot path."""
    return dumps(obj)


def dumps_str(obj: Any) -> str:
    """Serialize ``obj`` to a JSON ``str`` (used to build Socket.IO frames)."""
    if HAS_ORJSON:
        return _orjson.dumps(obj).decode("utf-8")
    return _stdlib_json.dumps(obj, separators=(",", ":"))


__all__ = ["HAS_ORJSON", "dumps", "dumps_bytes", "dumps_str", "loads"]
