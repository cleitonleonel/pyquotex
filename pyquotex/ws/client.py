"""Async WebSocket client using a websockets library for Quotex API."""
import logging
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

logger = logging.getLogger(__name__)


class WebsocketClient:
    """Pure async WebSocket client — no threads, no blocking."""

    def __init__(self, api: Any):
        """
        Initializes the WebSocket client.

        Args:
            api (QuotexAPI): The API instance this client belongs to.
        """
        self.api = api
        self.state = api.state
        self._ws: websockets.WebSocketClientProtocol | None = None

    @property
    def wss(self) -> "WebsocketClient":
        """
        Returns the low-level WebSocket instance wrapper.

        Returns:
            WebsocketClient: self.
        """
        return self

    async def send(self, data: str) -> None:
        """Send data through the websocket connection.

        Fully async — must be awaited. Handles connection state checks
        and logs errors instead of silently dropping messages.
        """
        if self._ws and self._ws.state is State.OPEN:
            try:
                await self._ws.send(data)
                logger.debug("Sent: %s", data)
            except ConnectionClosed as e:
                logger.warning("Cannot send, connection closed: %s", e)
            except Exception as e:
                logger.error("Error sending WebSocket message: %s", e)

    async def run_forever(
            self,
            url: str,
            extra_headers: dict[str, str] | None = None,
            ssl: Any = None,
            **kwargs: Any
    ) -> None:
        """
        Connects to the WebSocket and enters a message processing loop.

        Args:
            url (str): The WebSocket URL.
            extra_headers (dict, optional): Custom HTTP headers.
            ssl (SSLContext, optional): SSL context for secure connection.
        """
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
                await self.api._on_open()
                async for raw in ws:
                    await self.api._on_message(raw)
        except ConnectionClosed as e:
            logger.info("WebSocket closed: %s", e)
            self.api._on_close(
                getattr(e, 'code', None),
                str(getattr(e, 'reason', e))
            )
        except Exception as e:
            logger.error("WebSocket error: %s", e)
            self.api._on_error(e)

    async def close(self) -> None:
        """Close the websocket connection gracefully."""
        if self._ws and self._ws.state is not State.CLOSED:
            await self._ws.close()

    def is_alive(self) -> bool:
        """
        Checks if the WebSocket connection is currently active.

        Returns:
            bool: True if connected and open, False otherwise.
        """
        return self._ws is not None and self._ws.state is State.OPEN
