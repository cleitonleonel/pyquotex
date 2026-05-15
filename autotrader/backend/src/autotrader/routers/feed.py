"""Live WebSocket trade feed.

Single endpoint that streams every ``TradeEvent`` from the in-process
:class:`autotrader.services.event_bus.TradeEventBus` to the browser
as JSON frames. Each subscriber gets a bounded queue (drops on
backpressure rather than wedging the executor) and unsubscribes
automatically on disconnect.

Auth: browsers can't easily attach an ``Authorization`` header to a
``new WebSocket(url)`` call, so the bearer token rides on the
``?token=`` query param. The same Fernet decoder the REST stack uses
gates entry — invalid / expired tokens get a clean 1008 close.
"""

from __future__ import annotations

from typing import Any

import structlog
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from autotrader.auth import _decode  # type: ignore[attr-defined]
from autotrader.dependencies import EventBusDep

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/feed", tags=["feed"])


@router.websocket("/ws")
async def feed_ws(
    websocket: WebSocket,
    bus: EventBusDep,
    token: str | None = Query(default=None),
) -> None:
    """Stream trade-row events as JSON until the client disconnects."""
    if not token:
        # Phase 0 instrumentation (audit 2026-05-13, M4): operators
        # report "WS won't connect" with no log breadcrumb. Emit a
        # structured warning at every rejection point — Phase 1 will
        # narrow the ``except`` clause below so unexpected exceptions
        # surface as ``log.exception`` instead of being silently
        # swallowed.
        log.warning("feed.ws.auth_rejected", reason="no_token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        _decode(token)
    except (HTTPException, InvalidToken) as exc:
        # Phase 1 (audit 2026-05-13, M4): narrow the catch to the
        # auth-rejection class so unexpected exceptions surface as a
        # framework-level ``log.exception`` instead of being silently
        # swallowed by a bare ``except Exception``. Auth.py's
        # ``_decode`` only raises ``HTTPException`` today; we include
        # the lower-level Fernet ``InvalidToken`` for defence in
        # depth in case the call surface ever bypasses ``_decode``.
        log.warning(
            "feed.ws.auth_rejected",
            reason=type(exc).__name__,
            detail=str(exc),
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    # Tell the client we're live so a UI can flip from "polling" to
    # "live" before the first event arrives.
    await websocket.send_json({"type": "feed.ready", "payload": {}})

    try:
        async for event in bus.subscribe():
            payload: dict[str, Any] = {
                "type": event.type,
                "payload": event.payload,
            }
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        return
