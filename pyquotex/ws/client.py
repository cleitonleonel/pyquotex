"""Async WebSocket client using websockets library for better performance."""
import asyncio
import logging
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class WebsocketClient:
    """Pure async WebSocket client — no threads, no blocking."""

    def __init__(self, api):
        self.api = api
        self.state = api.state
        self._ws = None

    @property
    def wss(self):
        return self

    def send(self, data: str):
        """Sync-compatible send: schedule coroutine on the running loop."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(self._send(data))
        else:
            loop.run_until_complete(self._send(data))

    async def _send(self, data: str):
        if self._ws and not self._ws.closed:
            await self._ws.send(data)
            logger.debug("Sent: %s", data)

    async def run_forever(self, url: str, extra_headers=None, ssl=None, **kwargs):
        """Connect and receive messages indefinitely."""
        headers = extra_headers or {}
        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                ssl=ssl,
                ping_interval=24,
                ping_timeout=20,
                max_size=2 ** 23,  # 8MB
                compression=None,  # disable per-frame compression for speed
            ) as ws:
                self._ws = ws
                self.api._on_open()
                async for raw in ws:
                    self.api._on_message(raw)
        except ConnectionClosed as e:
            logger.info("WebSocket closed: %s", e)
            self.api._on_close(getattr(e, 'code', None), str(getattr(e, 'reason', e)))
        except Exception as e:
            logger.error("WebSocket error: %s", e)
            self.api._on_error(e)

    def close(self):
        if self._ws:
            asyncio.ensure_future(self._ws.close())

    def is_alive(self):
        return self._ws is not None and not self._ws.closed
