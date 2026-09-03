"""A python wrapper for Quotex API."""
import logging

from .qxtypes import (
    AssetInfo,
    Balance,
    Candle,
    ProfileInfo,
    ReconnectPolicy,
    Subscription,
    TradeDirection,
    TradeResult,
    TradeStatus,
)


def _prepare_logging() -> None:
    """Prepare logger for module Quotex API."""
    logger = logging.getLogger(__name__)
    # logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.NullHandler())

    websocket_logger = logging.getLogger("websockets")
    websocket_logger.setLevel(logging.DEBUG)
    websocket_logger.addHandler(logging.NullHandler())


_prepare_logging()


__all__ = [
    "AssetInfo",
    "Balance",
    "Candle",
    "ProfileInfo",
    "ReconnectPolicy",
    "Subscription",
    "TradeDirection",
    "TradeResult",
    "TradeStatus",
]
