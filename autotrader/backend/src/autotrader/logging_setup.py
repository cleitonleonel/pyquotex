"""Structured logging via structlog over stdlib logging."""

from __future__ import annotations

import logging
import os
import sys

import structlog


# Phase 4 cleanup (audit 2026-05-13, L6): the noisy-library mute list
# was hardcoded in this file; adding a new transitive dependency that
# logs at INFO meant patching the source and redeploying. The list
# below is now the default — operators can override the WHOLE set or
# extend it via env. Two knobs:
#
# * ``AUTOTRADER_MUTE_LOGGERS_EXTRA=foo,bar.baz`` — extend the default.
# * ``AUTOTRADER_MUTE_LOGGERS=foo,bar.baz`` — REPLACE the default (use
#   sparingly; the defaults exist for production-safe noise control).
_DEFAULT_MUTED_LOGGERS = (
    "pyrogram",
    "pyrogram.session",
    "pyrogram.session.session",
    "pyrogram.session.auth",
    "pyrogram.connection",
    "pyrogram.connection.connection",
    "pyrogram.crypto",
    "pyrogram.dispatcher",
    "websockets",
    "websockets.client",
    "websockets.server",
    "websockets.protocol",
    "engineio",
    "engineio.client",
    "socketio",
    "socketio.client",
    "pyquotex",
    "httpx",
    "httpcore",
    "asyncio",
)


def _resolve_muted_loggers() -> tuple[str, ...]:
    """Read env-driven overrides; fall back to the production defaults."""
    override = os.environ.get("AUTOTRADER_MUTE_LOGGERS")
    if override is not None and override.strip():
        return tuple(
            name.strip() for name in override.split(",") if name.strip()
        )
    extra_csv = os.environ.get("AUTOTRADER_MUTE_LOGGERS_EXTRA", "")
    extra = tuple(name.strip() for name in extra_csv.split(",") if name.strip())
    return _DEFAULT_MUTED_LOGGERS + extra


def configure_logging(level: str = "INFO") -> None:
    """Initialise structlog + stdlib root logger to emit JSON to stdout."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )

    # Library loggers that dump transport-level chatter at INFO/DEBUG —
    # pin them to WARNING so they stay capped regardless of the app's
    # log level. Without this, ``AUTOTRADER_LOG_LEVEL=DEBUG`` (or even
    # an aggressive INFO from these libs) buries every autotrader log
    # under thousands of MTProto frames + websocket ticks per minute.
    #
    # * pyrogram.session.session emits "Received: ..." / "Sent: ..."
    #   JSON dumps for every MTProto message (pings, user-status
    #   updates, channel deltas — most uninteresting).
    # * websockets.protocol emits "> TEXT '42[\"tick\"]' [N bytes]"
    #   for every frame on the broker socket; pyquotex sends a
    #   socket.io tick every couple of seconds.
    # * pyquotex itself prints raw socket.io frames; the package
    #   logger sits under ``pyquotex``.
    # * The httpx noise on every outbound HTTP request is redundant
    #   with our own structured ``broker.*`` / ``telegram.*`` events.
    #
    # The default list is in ``_DEFAULT_MUTED_LOGGERS``; operators can
    # extend or replace it via ``AUTOTRADER_MUTE_LOGGERS_EXTRA`` /
    # ``AUTOTRADER_MUTE_LOGGERS`` (audit 2026-05-13, L6).
    #
    # If you actively need to see one of these (e.g. debugging a peer-
    # cache miss), bump the specific logger explicitly in a one-off:
    #   import logging
    #   logging.getLogger("pyrogram.session.session").setLevel(logging.DEBUG)
    for noisy in _resolve_muted_loggers():
        logging.getLogger(noisy).setLevel(logging.WARNING)
