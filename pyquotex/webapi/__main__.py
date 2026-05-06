"""Entrypoint: ``python -m pyquotex.webapi``.

Loads settings from environment variables and serves via uvicorn.
"""
from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import Settings


# Loggers that produce a frame per WS message / per HTTP request when
# left at their library defaults. Pinned one notch quieter than the
# user-selected root level so they never dominate stdout, but still
# surface when the operator explicitly asks for DEBUG.
_NOISY_LOGGERS = ("websockets", "httpx", "httpcore", "asyncio")


def _configure_logging(level_name: str) -> int:
    level = getattr(logging, level_name, logging.WARNING)
    if not isinstance(level, int):
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        force=True,
    )
    quieter = max(level, logging.WARNING)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(quieter)
    return level


def main() -> None:
    settings = Settings.from_env()
    level = _configure_logging(settings.log_level)
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=logging.getLevelName(level).lower(),
        access_log=settings.access_log,
    )


if __name__ == "__main__":
    main()
