"""A python wrapper for Quotex API."""
import logging

from .stable_api import Quotex
from .utils.multilogin import (
    MultiloginClient,
    MultiloginConfig,
    MultiloginProfile,
)
from .utils.proxy_config import ProxyConfig
from .utils.sentiment import (
    SentimentMonitor,
    SentimentSignal,
    SentimentSnapshot,
    SentimentThresholds,
)

__all__ = [
    "Quotex",
    "ProxyConfig",
    "MultiloginConfig",
    "MultiloginProfile",
    "MultiloginClient",
    "SentimentMonitor",
    "SentimentThresholds",
    "SentimentSignal",
    "SentimentSnapshot",
]


def _prepare_logging() -> None:
    """Prepare logger for module Quotex API."""
    logger = logging.getLogger(__name__)
    # logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.NullHandler())

    websocket_logger = logging.getLogger("websockets")
    websocket_logger.setLevel(logging.DEBUG)
    websocket_logger.addHandler(logging.NullHandler())


_prepare_logging()
