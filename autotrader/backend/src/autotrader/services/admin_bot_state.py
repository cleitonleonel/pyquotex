"""Lightweight resolver for app.state references used by command handlers.

Handlers shouldn't depend on FastAPI's request context — they live one
layer below, driven by the bot client. This module is set up by
``main.py``'s lifespan and provides typed accessors for the few
``app.state`` objects the handlers need (pipeline ring buffer, broker
manager, notifier). Keeps handlers easy to unit-test by allowing
``monkeypatch.setattr`` on a single function.
"""

from __future__ import annotations

from typing import Any

_pipeline: Any | None = None
_quotex: Any | None = None
_admin_bot: Any | None = None
_notifier: Any | None = None


def attach(
    *,
    pipeline: Any,
    quotex: Any,
    admin_bot: Any | None = None,
    notifier: Any | None = None,
) -> None:
    global _pipeline, _quotex, _admin_bot, _notifier  # noqa: PLW0603
    _pipeline = pipeline
    _quotex = quotex
    if admin_bot is not None:
        _admin_bot = admin_bot
    if notifier is not None:
        _notifier = notifier


def get_pipeline() -> Any | None:
    return _pipeline


def get_quotex() -> Any | None:
    return _quotex


def get_admin_bot() -> Any | None:
    return _admin_bot


def get_notifier() -> Any | None:
    return _notifier
