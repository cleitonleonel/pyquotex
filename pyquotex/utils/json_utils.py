"""
JSON utility providing a graceful fallback from orjson to standard json.
This ensures compatibility with platforms like Termux/Android where
native extensions are difficult to compile.
"""
import logging

logger = logging.getLogger(__name__)

try:
    import orjson

    HAS_ORJSON = True
except ImportError:
    import json

    HAS_ORJSON = False
    logger.warning(
        "orjson not found or could not be loaded. Falling back to standard json library. "
        "Performance may be reduced."
    )


def loads(data):
    """Parses JSON data (bytes or str)."""
    if HAS_ORJSON:
        return orjson.loads(data)
    return json.loads(data)


def dumps(obj, indent=None) -> bytes:
    """Serializes an object to JSON bytes."""
    if HAS_ORJSON:
        # orjson doesn't support indent in the same way, but we don't use it in the lib
        return orjson.dumps(obj)

    # json.dumps returns str, convert to bytes for consistency
    return json.dumps(obj).encode()


def dumps_str(obj) -> str:
    """Serializes an object to JSON string."""
    if HAS_ORJSON:
        return orjson.dumps(obj).decode()
    return json.dumps(obj)
