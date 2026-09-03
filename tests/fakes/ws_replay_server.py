"""Minimal replay server for Quotex's engine.io v3 + socket.io 2 protocol.

The real ``ws2.qxbroker.com`` speaks engine.io v3 framing on top of WebSocket:

* Server sends ``0{"sid":"...","upgrades":[],"pingInterval":25000,"pingTimeout":5000}``
  immediately on connect (engine.io OPEN packet).
* Client replies ``40`` (engine.io MESSAGE + socket.io CONNECT for the
  default namespace).
* Server replies ``40``.
* From then on, both sides exchange ``42["event-name", payload]`` frames
  (socket.io EVENT) and engine.io ``2``/``3`` ping/pong.
* The broker uses placeholder pattern ``451-["event",{"_placeholder":true,"num":0}]``
  followed by the raw binary payload (we skip binary in tests since the
  client tolerates JSON-only frames for the events we exercise).

This class implements a script-driven version of that protocol so
``QuotexAPI`` / ``Quotex`` can be exercised offline without touching the
broker. Test authors register canned responses against an event name and
optionally on every connect (greeting frames).

Usage
-----

>>> server = WSReplayServer()
>>> await server.start()
>>> server.on_event("instruments/get", reply=[
...     '42["instruments/list",[[1,"EURUSD","EUR/USD","forex",4,84,60,30,3,1,0,0,[],1,true]]]',
... ])
>>> # ... point the client at ``server.url`` ...
>>> await server.stop()

If many tests use the same canned protocol, use :func:`default_handlers`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

import websockets
from websockets.asyncio.server import ServerConnection, serve

logger = logging.getLogger(__name__)

Reply = str | bytes
Handler = Callable[[ServerConnection, str], Awaitable[None]]


def _free_port() -> int:
    """Allocate an ephemeral TCP port and release it back to the OS.

    Reasonable for tests; race window with ``serve()`` is negligible.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WSReplayServer:
    """Scriptable engine.io/socket.io WebSocket server for tests.

    Parameters
    ----------
    greeting:
        Frames the server pushes to every new connection, in order, before
        any client message. Defaults to a minimal engine.io handshake +
        ``s_authorization`` ack + an empty balance + an empty instruments
        list, which is enough to satisfy :meth:`QuotexAPI._on_open`.
    """

    def __init__(self, greeting: list[Reply] | None = None) -> None:
        self.port = _free_port()
        self.host = "127.0.0.1"
        self._server: websockets.Server | None = None
        self._handlers: dict[str, list[Reply]] = {}
        self._dyn_handlers: dict[str, Handler] = {}
        self.received: list[str] = []
        self.connections: list[ServerConnection] = []
        self.greeting: list[Reply] = (
            greeting
            if greeting is not None
            else default_greeting_frames()
        )

    @property
    def url(self) -> str:
        """The ``ws://...`` URL clients should connect to."""
        return f"ws://{self.host}:{self.port}/socket.io/?EIO=3&transport=websocket"

    # ------------------------------------------------------------------
    # Scripting
    # ------------------------------------------------------------------
    def on_event(self, event_name: str, *, reply: list[Reply]) -> None:
        """Reply with these frames when the client sends ``42["event_name",...]``."""
        self._handlers[event_name] = list(reply)

    def on_event_dynamic(self, event_name: str, handler: Handler) -> None:
        """Register a coroutine handler that gets the raw message verbatim."""
        self._dyn_handlers[event_name] = handler

    async def push(self, frame: Reply) -> None:
        """Broadcast a frame to all currently-connected clients."""
        for conn in list(self.connections):
            try:
                await conn.send(frame)
            except Exception as e:  # pragma: no cover - test transient
                logger.debug("push() failed for %s: %s", conn, e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        # ``compression=None`` matches the client (WebsocketClient passes
        # the same), otherwise the server would accept ``permessage-deflate``
        # offered in headers and the client would reject the response.
        self._server = await serve(
            self._connection_handler,
            self.host,
            self.port,
            compression=None,
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @asynccontextmanager
    async def running(self) -> AsyncIterator["WSReplayServer"]:
        await self.start()
        try:
            yield self
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    async def _connection_handler(self, ws: ServerConnection) -> None:
        self.connections.append(ws)
        try:
            for frame in self.greeting:
                await ws.send(frame)
            async for raw in ws:
                msg = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
                self.received.append(msg)
                await self._dispatch(ws, msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            try:
                self.connections.remove(ws)
            except ValueError:
                pass

    async def _dispatch(self, ws: ServerConnection, msg: str) -> None:
        # engine.io PING (`2`) -> PONG (`3`)
        if msg == "2":
            await ws.send("3")
            return
        # socket.io CONNECT (`40`)
        if msg == "40":
            await ws.send("40")
            return
        # Standard socket.io EVENT: `42["event-name", payload]`
        event = _extract_event_name(msg)
        if event is None:
            return
        if event in self._dyn_handlers:
            await self._dyn_handlers[event](ws, msg)
            return
        for frame in self._handlers.get(event, []):
            await ws.send(frame)


def _extract_event_name(msg: str) -> str | None:
    """Return the socket.io event name from ``42["name", ...]`` style frames."""
    if not msg.startswith("42"):
        return None
    body = msg[2:]
    try:
        payload = json.loads(body)
    except Exception:
        return None
    if isinstance(payload, list) and payload and isinstance(payload[0], str):
        return payload[0]
    return None


# ----------------------------------------------------------------------
# Canned default frames
# ----------------------------------------------------------------------
def default_greeting_frames() -> list[Reply]:
    """Frames sent on every new connection.

    Mirrors what the real broker sends right after a successful
    SSID-authorized handshake: engine.io OPEN, an ``s_authorization``
    ACK, an empty balance event, and an empty instruments/list.
    """
    return [
        '0{"sid":"replay-sid","upgrades":[],"pingInterval":25000,"pingTimeout":5000}',
        '40',
        '42["s_authorization"]',
        '42["balance",{"demoBalance":10000.0,"liveBalance":0.0,"currencyCode":"USD"}]',
        '451-["instruments/list",{"_placeholder":true,"num":0}]',
        # Followed by the data payload (list of instrument rows). The
        # client's unwrap step would collapse a single-row message into
        # a flat row, so we ship two rows to keep the shape intact.
        '['
        '[1,"EURUSD","EUR/USD","forex",4,84,60,30,3,1,0,0,[],1,true],'
        '[2,"GBPUSD","GBP/USD","forex",4,84,60,30,3,1,0,0,[],1,true]'
        ']',
    ]


def candle_history_frames(
    asset: str = "EURUSD",
    period: int = 60,
    n: int = 60,
    base_price: float = 1.10,
) -> list[Reply]:
    """Generate a deterministic ``history/load`` reply.

    The broker's ``history/load`` payload contains positional ticks
    ``[timestamp, price, direction]`` under the ``candles`` key. The
    client's :func:`calculate_candles` groups those ticks by period
    into dict candles. We emit several ticks per period bucket so
    each one yields a usable OHLC candle (with high != low).
    """
    ticks: list[list[Any]] = []
    ticks_per_period = 4
    for i in range(n):
        bucket_start = 1_700_000_000 + i * period
        # Tiny deterministic oscillation so indicators have something to chew on.
        for j in range(ticks_per_period):
            ts = bucket_start + j * (period // ticks_per_period)
            price = base_price + ((i + j) % 5) * 0.0001
            ticks.append([ts, price, 0])
    payload = {
        "asset": asset,
        "index": 1,
        "period": period,
        "candles": ticks,
    }
    return [
        '451-["history/load",{"_placeholder":true,"num":0}]',
        json.dumps(payload),
    ]


__all__ = [
    "WSReplayServer",
    "candle_history_frames",
    "default_greeting_frames",
]
