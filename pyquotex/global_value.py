"""Per-connection state for Quotex API.

Replaces the old module-level global variables with an instanciable
dataclass so that multiple QuotexAPI instances can coexist without
sharing state (multi-account support).
"""
from dataclasses import dataclass


@dataclass
class ConnectionState:
    """Mutable state scoped to a single QuotexAPI connection."""

    SSID: str | None = None
    check_websocket_if_connect: int | None = None
    ssl_Mutual_exclusion: bool = False
    ssl_Mutual_exclusion_write: bool = False
    started_listen_instruments: bool = True
    check_rejected_connection: bool = False
    check_accepted_connection: bool = False
    check_websocket_if_error: bool = False
    websocket_error_reason: str | None = None
    balance_id: int | None = None
