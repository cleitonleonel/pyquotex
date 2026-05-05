"""WebSocket relay endpoints for live prices + sentiment.

Auth check happens on the connection upgrade — the client must include
its API key as a query parameter (``?api_key=…``) or in the
``X-API-Key`` header. Browsers can't send custom headers on WS
upgrades from JavaScript, so the query-param fallback exists.

Wire format: each message is a JSON object. Subscribers receive
deltas as the broker emits them, with backpressure handled by the
relay (oldest-tick-dropped on full queue).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ..deps import get_relay, get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["streams"])


async def _authorise_ws(ws: WebSocket, api_key_qs: str | None) -> bool:
    """Check the API key from query string or X-API-Key header. Close
    with policy-violation (1008) if missing/wrong."""
    settings = get_settings(ws)  # type: ignore[arg-type]
    candidate = api_key_qs
    if not candidate:
        candidate = ws.headers.get("x-api-key")
    if not candidate:
        auth = ws.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            candidate = auth[7:].strip()
    if not candidate or candidate != settings.api_key:
        await ws.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing or invalid API key",
        )
        return False
    return True


@router.websocket("/stream/prices")
async def prices_stream(
        ws: WebSocket,
        asset: str = Query(..., description="e.g. EURUSD or EURUSD_otc"),
        api_key: str | None = Query(
            default=None,
            description="API key — alternative to X-API-Key / Bearer header",
        ),
):
    """Stream live price ticks for a single asset.

    Each frame is JSON::

        {"asset": "EURUSD_otc", "time": 1746450123, "price": 1.0852}

    On upstream error::

        {"error": "…", "asset": "…"}

    Close with policy-violation if the API key is missing/wrong.
    """
    await ws.accept()
    if not await _authorise_ws(ws, api_key):
        return

    relay = get_relay(ws)  # type: ignore[arg-type]
    try:
        async for tick in relay.subscribe_prices(asset):
            await ws.send_text(json.dumps(tick))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("prices stream crashed")
        try:
            await ws.send_text(json.dumps({"error": str(e), "asset": asset}))
            await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass


@router.websocket("/stream/sentiment")
async def sentiment_stream(
        ws: WebSocket,
        asset: str = Query(...),
        api_key: str | None = Query(default=None),
):
    """Stream live sentiment updates for a single asset.

    Each frame is JSON::

        {"asset": "EURUSD_otc", "bullish": 0.62, "bearish": 0.38, "raw": {…}}
    """
    await ws.accept()
    if not await _authorise_ws(ws, api_key):
        return

    relay = get_relay(ws)  # type: ignore[arg-type]
    try:
        async for snap in relay.subscribe_sentiment(asset):
            await ws.send_text(json.dumps(snap))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("sentiment stream crashed")
        try:
            await ws.send_text(json.dumps({"error": str(e), "asset": asset}))
            await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass
