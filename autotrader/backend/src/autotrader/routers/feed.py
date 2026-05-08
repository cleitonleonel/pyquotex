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

import asyncio
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from autotrader.auth import _decode  # type: ignore[attr-defined]
from autotrader.dependencies import EventBusDep

router = APIRouter(prefix="/feed", tags=["feed"])


@router.websocket("/ws")
async def feed_ws(
    websocket: WebSocket,
    bus: EventBusDep,
    token: str | None = Query(default=None),
) -> None:
    """Stream trade-row events as JSON until the client disconnects."""
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        _decode(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    # Tell the client we're live so a UI can flip from "polling" to
    # "live" before the first event arrives.
    await websocket.send_json({"type": "feed.ready", "payload": {}})

    consumer_task: asyncio.Task[None] | None = None
    try:
        async for event in bus.subscribe():
            payload: dict[str, Any] = {
                "type": event.type,
                "payload": event.payload,
            }
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        return
    finally:
        if consumer_task is not None and not consumer_task.done():
            consumer_task.cancel()
