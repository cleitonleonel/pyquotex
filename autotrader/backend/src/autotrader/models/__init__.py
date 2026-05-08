"""SQLModel database models.

Importing this package registers every model on ``SQLModel.metadata``,
which ``db.init_db`` then uses to create the tables.
"""

from autotrader.models.broker_credentials import BrokerCredentials
from autotrader.models.parser_config import ParserConfig
from autotrader.models.settings import GlobalSettings
from autotrader.models.telegram_session import TelegramSession
from autotrader.models.trade_attempt import TradeAttempt
from autotrader.models.watched_channel import WatchedChannel

__all__ = [
    "BrokerCredentials",
    "GlobalSettings",
    "ParserConfig",
    "TelegramSession",
    "TradeAttempt",
    "WatchedChannel",
]
