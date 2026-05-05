"""FastAPI dependency providers.

Both the singleton :class:`Settings` and the singleton :class:`Quotex`
client are stashed on ``app.state`` during the lifespan-managed
startup. These helpers retrieve them; both use ``Depends(...)`` in
endpoint signatures.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from pyquotex.stable_api import Quotex

    from .config import Settings
    from .relays import StreamRelay


def get_settings(request: Request) -> "Settings":
    return request.app.state.settings


def get_client(request: Request) -> "Quotex":
    client = getattr(request.app.state, "quotex_client", None)
    if client is None:
        raise RuntimeError(
            "Quotex client is not initialised yet. The app lifespan "
            "should have created it on startup."
        )
    return client


def get_relay(request: Request) -> "StreamRelay":
    relay = getattr(request.app.state, "stream_relay", None)
    if relay is None:
        raise RuntimeError("Stream relay not initialised.")
    return relay
