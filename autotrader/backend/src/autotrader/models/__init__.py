"""SQLModel database models.

Importing this package registers every model on ``SQLModel.metadata``,
which ``db.init_db`` then uses to create the tables.
"""

from autotrader.models.broker_credentials import BrokerCredentials
from autotrader.models.settings import GlobalSettings

__all__ = ["BrokerCredentials", "GlobalSettings"]
