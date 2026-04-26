"""Per-connection state for Quotex API.

Replaces the old module-level global variables with an instanciable
dataclass so that multiple QuotexAPI instances can coexist without
sharing state (multi-account support).
"""
from dataclasses import dataclass
from enum import IntEnum


class WebsocketStatus(IntEnum):
    """Enumeration for WebSocket connection status."""
    DISCONNECTED = 0
    CONNECTED = 1
    CONNECTING = 2
    ERROR = -1


class AuthStatus(IntEnum):
    """Enumeration for authentication status."""
    NOT_AUTHENTICATED = 0
    AUTHENTICATING = 1
    AUTHENTICATED = 2
    FAILED = -1


@dataclass
class ConnectionState:
    """Mutable state scoped to a single QuotexAPI connection."""

    SSID: str | None = None
    status: WebsocketStatus = WebsocketStatus.DISCONNECTED
    auth_status: AuthStatus = AuthStatus.NOT_AUTHENTICATED
    started_listen_instruments: bool = True
    websocket_error_reason: str | None = None
    balance_id: int | None = None

    @property
    def check_websocket_if_connect(self) -> int:
        """Legacy compatibility property."""
        return int(self.status == WebsocketStatus.CONNECTED)

    @property
    def check_accepted_connection(self) -> bool:
        """Legacy compatibility property."""
        return self.auth_status == AuthStatus.AUTHENTICATED

    @property
    def check_rejected_connection(self) -> bool:
        """Legacy compatibility property."""
        return self.auth_status == AuthStatus.FAILED

    @property
    def check_websocket_if_error(self) -> bool:
        """Legacy compatibility property."""
        return self.status == WebsocketStatus.ERROR
