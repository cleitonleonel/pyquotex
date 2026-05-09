"""Structured logging via structlog over stdlib logging."""

from __future__ import annotations

import logging
import sys

import structlog


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
    # If you actively need to see one of these (e.g. debugging a peer-
    # cache miss), bump the specific logger explicitly in a one-off:
    #   import logging
    #   logging.getLogger("pyrogram.session.session").setLevel(logging.DEBUG)
    for noisy in (
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
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
