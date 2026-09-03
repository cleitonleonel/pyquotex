import asyncio
import logging
from typing import Any, Callable

from ._api.account import AccountMixin
from ._api.assets import AssetsMixin
from ._api.history import HistoryMixin
from ._api.realtime import RealtimeMixin
from ._api.trading import TradingMixin
from .api import QuotexAPI
from .config import load_session, resource_path, update_session
from .global_value import AuthStatus
from .qxtypes import ReconnectPolicy
from .utils.account_type import AccountType
from .utils.optimization import OptimizedQuotexMixin

logger = logging.getLogger(__name__)


class Quotex(
    AccountMixin,
    TradingMixin,
    HistoryMixin,
    RealtimeMixin,
    AssetsMixin,
    OptimizedQuotexMixin,
):

    def __init__(
            self,
            email: str,
            password: str,
            host: str = "qxbroker.com",
            lang: str = "pt",
            user_agent: str = "Quotex/1.0",
            root_path: str = ".",
            user_data_dir: str = "browser",
            asset_default: str = "EURUSD",
            period_default: int = 60,
            proxies: dict[str, str] | None = None,
            on_otp_callback: Callable | None = None,
            reconnect_policy: ReconnectPolicy | None = None,
            wss_url_override: str | None = None,
    ):
        """
        Initializes the Quotex stable API wrapper.

        Args:
            email (str): User email.
            password (str): User password.
            host (str): Broker hostname. Defaults to "qxbroker.com".
            lang (str): Language code. Defaults to "pt".
            user_agent (str): Browser User-Agent. Defaults to "Quotex/1.0".
            root_path (str): Root directory for local storage. Defaults to ".".
            user_data_dir (str): Directory for browser profile data.
                Defaults to "browser".
            asset_default (str): Default asset to use. Defaults to "EURUSD".
            period_default (int): Default candle period in seconds.
                Defaults to 60.
            proxies (dict, optional): Proxy configuration.
            on_otp_callback (callable, optional): Callback for 2FA/OTP input.
            reconnect_policy (ReconnectPolicy, optional): Auto-reconnect /
                stale-detection configuration. Defaults to enabled with
                exponential backoff. Pass ``ReconnectPolicy(enabled=False)``
                to opt out.
        """
        self.size = [
            5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
            3600, 7200, 14400, 86400
        ]
        self.email = email
        self.password = password
        self.host = host
        self.lang = lang
        self.proxies = proxies
        self.resource_path = root_path
        self.user_data_dir = user_data_dir
        self.asset_default = asset_default
        self.period_default = period_default
        self.subscribe_candle: list[str] = []
        self.subscribe_candle_all_size: list[str] = []
        self.subscribe_mood: list[str] = []
        self.account_is_demo: int = AccountType.DEMO
        self.suspend: float = 0.2
        self.codes_asset: dict[str, str] = {}
        self.api: QuotexAPI | None = None
        self.duration: int | None = None
        self.websocket_client: Any = None
        self.websocket_thread: Any = None
        self.debug_ws_enable: bool = False
        self.resource_path = resource_path(root_path)
        session = load_session(self.email, user_agent)
        self.session_data = session
        self.on_otp_callback = on_otp_callback
        self.reconnect_policy = reconnect_policy or ReconnectPolicy()
        self.wss_url_override = wss_url_override

    @property
    def websocket(self) -> Any:
        """Property to get websocket.
        :returns: The active WebSocket instance.
        """
        return self.api.websocket if self.api else None

    @staticmethod
    async def _check_connect(state: Any) -> bool:
        """Check connection using the per-instance state object.

        Waits up to ~7s for the state to settle on AUTHENTICATED; returns
        as soon as the predicate is satisfied (event-driven path) or False
        on timeout.
        """
        from pyquotex._api._waits import wait_until
        try:
            await wait_until(
                lambda: state.auth_status == AuthStatus.AUTHENTICATED or state.auth_status == AuthStatus.FAILED,
                timeout=7,
                poll_interval=0.05,
            )
            return state.auth_status == AuthStatus.AUTHENTICATED
        except asyncio.TimeoutError:
            return state.auth_status == AuthStatus.AUTHENTICATED

    async def check_connect(self) -> bool:
        """Check connection using the current API's state."""
        if self.api is None:
            return False
        return await self._check_connect(self.api.state)

    def set_session(
            self,
            user_agent: str,
            cookies: str | None = None,
            ssid: str | None = None
    ) -> None:
        """
        Manually sets the session data.

        Args:
            user_agent (str): The User-Agent string.
            cookies (str, optional): The raw cookie string.
            ssid (str, optional): The SSID token.
        """
        session = {
            "cookies": cookies,
            "token": ssid,
            "user_agent": user_agent
        }
        self.session_data = update_session(self.email, session)

    async def re_subscribe_stream(self) -> None:
        """Re-subscribes to all active candle and mood streams."""
        try:
            for ac in self.subscribe_candle:
                sp = ac.split(",")
                await self.start_candles_one_stream(sp[0], int(sp[1]))
        except Exception as e:
            logger.warning("Failed to re-subscribe candle stream: %s", e)
        try:
            for ac in self.subscribe_candle_all_size:
                await self.start_candles_all_size_stream(ac)
        except Exception as e:
            logger.warning("Failed to re-subscribe all_size stream: %s", e)
        try:
            for ac in self.subscribe_mood:
                await self.start_mood_stream(ac)
        except Exception as e:
            logger.warning("Failed to re-subscribe mood stream: %s", e)

    async def close(self) -> bool:
        """Closes the API connection and stops all tasks."""
        if self.api:
            return await self.api.close()
        return True

    async def __aenter__(self) -> "Quotex":
        """Async context manager: connects on enter."""
        ok, reason = await self.connect()
        if not ok:
            raise ConnectionError(f"Quotex connect failed: {reason}")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Async context manager: closes the connection on exit."""
        await self.close()
