"""Module for Quotex websocket."""
import asyncio
import logging
import os
import ssl
import time
from collections import defaultdict, deque
from typing import Any, Callable

import certifi
import httpx

from .global_value import ConnectionState, WebsocketStatus, AuthStatus
from .network.history import GetHistory
from .network.login import Login
from .network.logout import Logout
from .network.navigator import Browser
from .network.settings import Settings
from .utils import json_utils as json
from .utils.account_type import AccountType
from .utils.async_utils import EventRegistry
from .utils.proxy_config import ProxyConfig
from .utils.reconnect import ReconnectPolicy, ReconnectSupervisor
from .utils.sentiment import SentimentMonitor
from .ws.channels.buy import Buy
from .ws.channels.candles import GetCandles
from .ws.channels.sell_option import SellOption
from .ws.channels.ssid import Ssid
from .ws.client import WebsocketClient
from .ws.objects.candles import Candles
from .ws.objects.listinfodata import ListInfoData
from .ws.objects.profile import Profile
from .ws.objects.timesync import TimeSync

logger = logging.getLogger(__name__)

cert_path = certifi.where()
os.environ['SSL_CERT_FILE'] = cert_path
os.environ['WEBSOCKET_CLIENT_CA_BUNDLE'] = cert_path

# Unified SSL context for both HTTP and WebSocket to ensure consistent JA3 fingerprint
unified_ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
unified_ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
unified_ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3
# Browser-like cipher suite to avoid JA3 detection
unified_ssl_context.set_ciphers(
    'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:'
    'ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:'
    'ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:'
    'DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384'
)
unified_ssl_context.load_verify_locations(cert_path)


class QuotexAPI:
    """Class for communication with Quotex API."""

    def __init__(
            self,
            host: str,
            username: str,
            password: str,
            lang: str,
            proxies: dict[str, str] | None = None,
            resource_path: str | None = None,
            user_data_dir: str = ".",
            on_otp_callback: Callable | None = None,
            proxy_config: ProxyConfig | None = None,
            sentiment_monitor: SentimentMonitor | None = None,
            reconnect_policy: ReconnectPolicy | None = None,
    ):
        """
        :param str host: The hostname or ip address of a Quotex server.
        :param str username: The username of a Quotex server.
        :param str password: The password of a Quotex server.
        :param str lang: The lang of a Quotex platform.
        :param proxies: The proxies of a Quotex server.
        :param user_data_dir: The path browser user data dir.
        :param on_otp_callback: Callback function for OTP (2FA) input.
        """
        self.state = ConnectionState()
        self.on_otp_callback = on_otp_callback
        self._ws_send_lock = asyncio.Lock()

        self.socket_option_opened: dict[str, Any] = {}
        self.buy_id: str | int | None = None
        self.pending_id: str | int | None = None
        self.trace_ws: bool = False
        self.buy_expiration: int | None = None
        self.current_asset: str | None = None
        self.current_period: int | None = None
        self.buy_successful: bool | None = None
        self.pending_successful: bool | None = None
        # Pending-order lifecycle bridge — see ``_temp_status`` handling
        # below. ``_active_pending`` maps a pending ticket UUID to the
        # asset it was placed against, populated when the broker sends
        # ``s_pending/create``. ``pending_ticket_map`` then maps the
        # pending ticket to the *executed* trade UUID once the pending
        # actually fires (``s_pending/opened``); when that executed
        # trade closes we mirror the ``order_closed`` event back to the
        # pending ticket so ``check_win(pending_id)`` actually returns.
        self._active_pending: dict[str, str] = {}
        self.pending_ticket_map: dict[str, str] = {}
        # Reverse index for the close-mirror lookup: executed_uuid →
        # pending_ticket. Without this the close handler had to scan
        # every entry in ``pending_ticket_map`` on each close
        # (O(n) per close, O(n²) over the session under high pending
        # volume). Always kept in sync with ``pending_ticket_map``.
        self._exec_to_pending: dict[str, str] = {}
        self.account_balance: dict[str, Any] | None = None
        self.account_type: int | None = AccountType.DEMO
        self.tournament_id: int = 0
        self.instruments: list[Any] = []
        self.training_balance_edit_request: dict[str, Any] | None = None
        self.profit_in_operation: float | None = None
        self.sold_options_respond: Any = None
        self.sold_digital_options_respond: Any = None
        self.listinfodata = ListInfoData()
        self.timesync = TimeSync()
        self.candles = Candles()
        self.profile = Profile()

        self.host = host
        self.https_url = f"https://{host}"
        self.wss_url = f"wss://ws2.{host}/socket.io/?EIO=3&transport=websocket"
        self.wss_message: str | None = None
        self.websocket_client: WebsocketClient | None = None
        self._websocket_task: asyncio.Task | None = None
        self.set_ssid: Any = None
        self.object_id: Any = None
        self.token_login2fa: str | None = None
        self.is_logged: bool = False
        self._temp_status: str = ""
        self.username = username
        self.password = password
        self.resource_path = resource_path
        self.user_data_dir = user_data_dir
        self.proxies = proxies
        self.proxy_config = proxy_config
        self.sentiment_monitor = sentiment_monitor
        self.reconnect_policy = reconnect_policy or ReconnectPolicy()
        self.reconnect_supervisor: ReconnectSupervisor | None = None
        self._client_ref: Any = None  # set by Quotex for state replay
        self.lang = lang
        self.settings_list: dict[str, Any] = {}
        self.signal_data: dict[str, Any] = {}
        self.get_candle_data: dict[str, Any] = {}
        self.historical_candles: dict[str, Any] = {}
        self.candle_v2_data: dict[str, Any] = {}
        # ``deque(maxlen=1000)`` evicts in O(1) — the previous
        # ``defaultdict(list)`` + ``list.pop(0)`` cap (in ``_on_message``
        # ~line 645) was O(n) per tick, which compounds badly under
        # bursty price streams on multi-asset clients.
        self.realtime_price: dict[str, deque[dict[str, Any]]] = (
            defaultdict(lambda: deque(maxlen=1000))
        )
        self.realtime_price_data: list[Any] = []
        self.realtime_candles: dict[str, Any] = {}
        self.realtime_sentiment: dict[str, Any] = {}
        self.traders_mood: dict[str, Any] = {}
        self.candle_generated_check = defaultdict(lambda: defaultdict(dict))
        self.candle_generated_all_size_check = defaultdict(dict)
        self.top_list_leader: dict[str, Any] = {}
        self.session_data: dict[str, Any] = {}
        proxy_url: str | None = None
        if proxy_config and proxy_config.url:
            proxy_url = proxy_config.url
        elif isinstance(proxies, str) and "://" in proxies:
            proxy_url = proxies
        self.browser = Browser(
            proxies=proxy_url,
            proxy_config=proxy_config,
        )
        self.browser.set_headers()
        self.settings = Settings(self)
        self.event_registry = EventRegistry()
        http_verify: Any = unified_ssl_context
        if proxy_config and not proxy_config.verify_ssl:
            http_verify = False
        self._http_client = httpx.AsyncClient(
            verify=http_verify,
            timeout=30.0,
            follow_redirects=True,
            proxy=proxy_url,
        )
        self.profit_today: float | None = None
        self.heartbeat_task: asyncio.Task | None = None

    async def _on_open(self) -> None:
        """Called when WebSocket connection is established."""
        logger.info("Websocket client connected.")
        self.state.status = WebsocketStatus.CONNECTED
        await self.event_registry.set_event("status_changed", self.state.status)

        # Start Heartbeat task to keep connection alive and stream active
        async def heartbeat() -> None:
            while self.state.status == WebsocketStatus.CONNECTED:
                try:
                    await self.websocket.send('42["tick"]')
                except Exception as e:
                    # A silent break would leave the supervisor blind:
                    # the WS may eventually drop on the broker side
                    # (server-timeout) but we can act faster by
                    # signalling ERROR ourselves so the reconnect
                    # supervisor wakes immediately.
                    logger.warning("Heartbeat task failing: %s", e)
                    self.state.status = WebsocketStatus.ERROR
                    self.state.websocket_error_reason = (
                        f"heartbeat send failed: {e}"
                    )
                    await self.event_registry.set_event(
                        "status_changed", self.state.status
                    )
                    return
                # Send it every 5 seconds as in legacy version
                await asyncio.sleep(5)

        self.heartbeat_task = asyncio.create_task(heartbeat())

        await self.websocket.send('42["indicator/list"]')
        await self.websocket.send('42["drawing/load"]')
        await self.websocket.send('42["pending/list"]')
        await self.websocket.send('42["chart_notification/get"]')
        await self.websocket.send('42["instruments/get"]')

    async def _on_message(self, msg: bytes | str) -> None:
        """Called for every WebSocket message received."""
        try:
            message: Any = None
            msg_str = (
                msg.decode("utf-8", errors="ignore")
                if isinstance(msg, bytes)
                else str(msg)
            )

            if "authorization/reject" in msg_str:
                self.state.websocket_error_reason = (
                    "Websocket connection rejected."
                )
                self.state.auth_status = AuthStatus.FAILED
                await self.event_registry.set_event(
                    "auth_changed", self.state.auth_status
                )
                return
            elif "s_authorization" in msg_str:
                self.state.auth_status = AuthStatus.AUTHENTICATED
                self.state.status = WebsocketStatus.CONNECTED
                await self.event_registry.set_event(
                    "auth_changed", self.state.auth_status
                )
                await self.event_registry.set_event(
                    "status_changed", self.state.status
                )

            # Detect Socket.IO prefix
            is_control = msg_str and msg_str[0].isdigit()

            # Clean JSON extraction
            try:
                # Find start of JSON
                start_idx = -1
                for idx, char in enumerate(msg_str):
                    if char in ('[', '{'):
                        start_idx = idx
                        break

                if start_idx != -1:
                    clean_json = msg_str[start_idx:]
                    data_json = json.loads(clean_json)
                    message = data_json
                    data = (
                        data_json[0]
                        if (
                                isinstance(data_json, list)
                                and len(data_json) == 1
                        )
                        else data_json
                    )

                    pass
                else:
                    pass
            except Exception as e:
                logger.debug("Failed to parse raw data payload: %s", e)

            # 1. Handle Control Messages (Placeholders)
            if is_control:
                if "51-" in msg_str and "_placeholder" in msg_str:
                    self._temp_status = msg_str
                    return

                # Standard Event Processing
                if (
                        isinstance(message, list)
                        and len(message) > 1
                        and isinstance(message[0], str)
                ):
                    event = message[0]
                    data = message[1]

                    if event == "s_authorization":
                        self.state.auth_status = AuthStatus.AUTHENTICATED
                        await self.event_registry.set_event(
                            "auth_changed", self.state.auth_status
                        )
                    elif event == "instruments/list":
                        if isinstance(data, dict) and data.get("_placeholder"):
                            self._temp_status = (
                                '451-["instruments/list",'
                                f'{json.dumps_str(data)}]'
                            )
                        else:
                            self.instruments = data
                            await self.event_registry.set_event(
                                'instruments_ready', data
                            )
                    elif event == "trader/history":
                        await self.event_registry.set_event(
                            'history_ready', data
                        )
                    elif event == "balance":
                        self.account_balance = data
                        await self.event_registry.set_event(
                            'balance_ready', data
                        )
                    elif event == "candle-generated":
                        asset = data.get("asset")
                        period = data.get("period")
                        if asset and period:
                            self.candle_generated_check[str(asset)][
                                int(period)
                            ] = data
                            self.candle_generated_all_size_check[
                                str(asset)
                            ] = data
                            # Wake any ``start_candles_one_stream`` /
                            # ``start_candles_all_size_stream`` waiter
                            # the instant data arrives — they used to
                            # poll every 200ms.
                            await self.event_registry.set_event(
                                f'candle_generated_{asset}_{int(period)}',
                                data,
                            )
                            await self.event_registry.set_event(
                                f'candle_generated_all_{asset}',
                                data,
                            )
                    elif event == "sentiment":
                        asset = data.get("asset")
                        if asset:
                            self.traders_mood[asset] = data
                            self.realtime_sentiment[asset] = data
                            # Wake ``start_realtime_sentiment`` —
                            # used to poll every 200ms.
                            await self.event_registry.set_event(
                                f'sentiment_ready_{asset}', data
                            )
                            if self.sentiment_monitor and isinstance(data, dict):
                                bullish, bearish = (
                                    self.sentiment_monitor.feed_raw(asset, data)
                                )
                                price_list = self.realtime_price.get(asset)
                                last_price = (
                                    price_list[-1]["price"]
                                    if price_list else None
                                )
                                asyncio.create_task(
                                    self.sentiment_monitor.feed(
                                        asset, bullish, bearish,
                                        price=last_price,
                                    )
                                )

            # 2. Handle Data Payloads (Placeholder fulfillment)
            elif message is not None and not is_control:
                data = (
                    message[0]
                    if isinstance(message, list) and len(message) == 1
                    else message
                )

                if self._temp_status and 'instruments/list' in self._temp_status:
                    if isinstance(data, list):
                        self.instruments = data
                    elif isinstance(data, dict) and "list" in data:
                        self.instruments = data["list"]

                    if self.instruments:
                        await self.event_registry.set_event(
                            'instruments_ready', self.instruments
                        )

                elif (
                        any(x in self._temp_status for x in ['history/list/v2', 'history/load'])
                        or (isinstance(data, dict) and (data.get("candles") or data.get("data")))
                ):
                    if isinstance(data, dict) and data.get("asset"):
                        asset = data["asset"]
                        self.candle_v2_data[asset] = data
                        await self.event_registry.set_event(
                            f'candles_ready_{asset}', data
                        )
                        if data.get("index") is not None:
                            await self.event_registry.set_event(
                                f'candles_ready_{asset}_{data["index"]}',
                                data
                            )
                    elif isinstance(data, list):
                        # Fallback for old history format if needed
                        await self.event_registry.set_event(
                            'history_ready', data
                        )

                elif self._temp_status and any(
                        x in self._temp_status
                        for x in [
                            'orders/open', 'orders/close', 'orders/opened',
                            'pending/create', 'pending/opened'
                        ]
                ):
                    logger.debug(
                        "Order event via placeholder! status=%s",
                        self._temp_status
                    )

                    # Handle both single dict and list of dicts
                    orders_to_process = []
                    if isinstance(data, list):
                        orders_to_process = data
                    elif isinstance(data, dict):
                        if data.get("deals"):
                            orders_to_process = data["deals"]
                        else:
                            orders_to_process = [data]

                    for order in orders_to_process:
                        order_id = order.get("id")
                        if order_id:
                            profit = order.get("profit", 0)
                            win = "win" if profit > 0 else "loss"
                            # Check if it's in a closed list or has a 
                            # close status
                            is_closed = (
                                    any(
                                        x in self._temp_status
                                        for x in ['closed', 'close']
                                    )
                                    or order.get("status") == "closed"
                            )
                            game_state = 1 if is_closed else 0

                            logger.debug(
                                "Processing order %s: win=%s, state=%s, "
                                "profit=%s",
                                order_id, win, game_state, profit
                            )
                            self.listinfodata.set(
                                win, game_state, order_id, profit
                            )
                            self.listinfodata.set(
                                win, game_state, str(order_id), profit
                            )

                            # When the order has closed, wake any
                            # check_win() / wait_for_order_close() that
                            # is awaiting this id. The other ``order``
                            # message format already fires this event
                            # in the explicit branch above; we mirror
                            # it here so both protocols are covered.
                            if is_closed:
                                await self.event_registry.set_event(
                                    f'order_closed_{order_id}', order
                                )
                                # Pending bridge: when the executed
                                # trade closes, mirror the result onto
                                # any pending ticket whose execution
                                # was correlated to this order_id.
                                # O(1) reverse-index lookup; the prior
                                # implementation scanned the whole map
                                # (O(n) per close, O(n²) over a session
                                # under high pending volume).
                                # Skip the listinfodata write + event
                                # fire when ``pticket == str(order_id)``
                                # (the typical ws2 same-UUID case) —
                                # the explicit ``order_closed_{order_id}``
                                # above already covered it.
                                pticket = self._exec_to_pending.pop(
                                    str(order_id), None
                                )
                                if pticket is None:
                                    # Defensive fallback: if anything
                                    # populated ``pending_ticket_map``
                                    # without going through the
                                    # ``s_pending/opened`` success path,
                                    # the reverse index won't have an
                                    # entry. Scan once to recover.
                                    for pt, ex in list(
                                            self.pending_ticket_map.items()
                                    ):
                                        if ex == str(order_id):
                                            pticket = pt
                                            break
                                if pticket is not None:
                                    self.pending_ticket_map.pop(pticket, None)
                                    if pticket != str(order_id):
                                        self.listinfodata.set(
                                            win, game_state, pticket, profit
                                        )
                                        self.listinfodata.set(
                                            win, game_state,
                                            str(pticket), profit
                                        )
                                        await self.event_registry.set_event(
                                            f'order_closed_{pticket}', order
                                        )

                            # Pending lifecycle bridge: ``f_pending/opened``
                            # with ``error=None`` is a *pre-fire*
                            # notification, not a rejection. Only
                            # ``f_pending/opened`` with a non-None error
                            # is a hard fail. ``s_pending/opened`` (or
                            # ``s_orders/open`` while a pending is
                            # active) carries the executed trade UUID
                            # we need to map back to the pending ticket.
                            if 'pending/opened' in self._temp_status:
                                is_hard_fail = (
                                    'f_pending/opened' in self._temp_status
                                    and order.get("error") is not None
                                )
                                is_success = (
                                    's_pending/opened' in self._temp_status
                                )
                                ticket_ref = (
                                    order.get("ticket")
                                    or order.get("pendingTicket")
                                )
                                # Match the pending ticket by:
                                #   1. an explicit ``ticket`` /
                                #      ``pendingTicket`` field, or
                                #   2. the same UUID being reused for
                                #      the executed trade (verified ws2
                                #      behaviour).
                                # The earlier asset-only fallback was
                                # unsafe — two pendings on the same
                                # asset back-to-back would cross-wire.
                                match = None
                                if (
                                        ticket_ref
                                        and ticket_ref in self._active_pending
                                ):
                                    match = ticket_ref
                                elif (
                                        order_id
                                        and str(order_id) in self._active_pending
                                ):
                                    match = str(order_id)
                                if is_hard_fail and match:
                                    # Resolve as loss immediately so
                                    # ``check_win(pending_id)`` returns.
                                    self.listinfodata.set(
                                        "loss", 1, match, 0
                                    )
                                    self.listinfodata.set(
                                        "loss", 1, str(match), 0
                                    )
                                    await self.event_registry.set_event(
                                        f'order_closed_{match}', order
                                    )
                                    self._active_pending.pop(match, None)
                                elif is_success and not is_closed and match:
                                    # Trade just executed — record the
                                    # ticket → executed UUID mapping
                                    # (and the reverse index) so the
                                    # close mirror above can do an O(1)
                                    # lookup when ``s_orders/close``
                                    # arrives.
                                    exec_uuid = str(order_id)
                                    self.pending_ticket_map[match] = exec_uuid
                                    self._exec_to_pending[exec_uuid] = match
                                    self._active_pending.pop(match, None)

                    # Always set buy_confirmed if it was an open request
                    if (
                            any(x in self._temp_status for x in ['orders/open', 'pending/create'])
                            and isinstance(data, dict)
                    ):
                        if 'pending' in self._temp_status:
                            # The ``s_pending/create`` payload wraps
                            # the ticket under ``{"pending": {...}}``;
                            # there's no top-level ``id`` field, which
                            # is why ``data.get("id")`` used to leave
                            # ``pending_id`` permanently None and the
                            # caller's poll loop always timed out.
                            pending_obj = data.get("pending")
                            if not isinstance(pending_obj, dict):
                                pending_obj = data
                            ticket = (
                                pending_obj.get("ticket")
                                or pending_obj.get("id")
                            )
                            self.pending_id = ticket
                            self.pending_successful = True
                            # Track the ticket → asset mapping so the
                            # pending/opened lifecycle handler can
                            # correlate the executed-trade UUID back
                            # to this pending ticket later.
                            asset_for_ticket = pending_obj.get("asset")
                            if ticket and asset_for_ticket:
                                self._active_pending[ticket] = (
                                    asset_for_ticket
                                )
                            await self.event_registry.set_event(
                                'pending_confirmed', data
                            )
                        else:
                            self.buy_id = data.get("id")
                            self.buy_successful = True
                            await self.event_registry.set_event(
                                'buy_confirmed', data
                            )

                self._temp_status = ""  # Clear after consuming data

            # 3. Handle Real-time and Profile Dicts
            if isinstance(message, dict):
                if message.get("liveBalance") or message.get("demoBalance"):
                    self.account_balance = message
                    await self.event_registry.set_event(
                        'balance_ready', message
                    )
                elif message.get("deals"):
                    # Handle real-time deals update (usually closed deals)
                    for order in message["deals"]:
                        order_id = order.get("id")
                        if order_id:
                            profit = order.get("profit", 0)
                            win = "win" if profit > 0 else "loss"
                            logger.debug(
                                "Real-time deal update for %s: "
                                "win=%s, profit=%s",
                                order_id, win, profit
                            )
                            self.listinfodata.set(win, 1, order_id, profit)
                            self.listinfodata.set(
                                win, 1, str(order_id), profit
                            )
                    await self.event_registry.set_event(
                        'history_ready', message
                    )
                elif (
                        "id" in message
                        and ("asset" in message or "amount" in message)
                ):
                    # Potential order confirmation
                    self.buy_id = message.get("id")
                    await self.event_registry.set_event(
                        'buy_confirmed', message
                    )

            elif (
                    isinstance(message, list)
                    and len(message) > 1
                    and message[0] == "order"
            ):
                # Explicit order event
                data = message[1]
                order_id = data.get("id")
                self.buy_id = order_id

                # Update listinfodata for check_win
                if "profit" in data and "status" in data:
                    profit = data.get("profit", 0)
                    win = "win" if profit > 0 else "loss"
                    game_state = 1 if data.get("status") == "closed" else 0
                    self.listinfodata.set(
                        win, game_state, str(order_id), profit
                    )

                await self.event_registry.set_event('buy_confirmed', data)
                await self.event_registry.set_event(
                    f'order_closed_{order_id}', data
                )

            elif (
                    isinstance(message, list)
                    and len(message) > 0
                    and isinstance(message[0], list)
            ):
                if len(message[0]) == 4:  # Price
                    asset, ts, price = (
                        message[0][0], message[0][1], message[0][2]
                    )
                    self.timesync.server_timestamp = ts  # Sync server clock

                    # Bounded ring buffer (deque maxlen=1000) — eviction
                    # is O(1) so this is safe under bursty multi-asset
                    # streams. The previous list+pop(0) was O(n) per tick.
                    price_list = self.realtime_price[asset]
                    is_first_tick = len(price_list) == 0
                    price_list.append({"time": ts, "price": price})

                    self.realtime_candles[asset] = message[0]

                    # Fire a per-asset readiness event the first time a
                    # price arrives so ``start_realtime_price`` can wait
                    # event-driven instead of polling every 200ms.
                    if is_first_tick:
                        await self.event_registry.set_event(
                            f'price_ready_{asset}', message[0]
                        )

        except Exception as e:
            logger.error("Error in _on_message: %s", e)

    def _on_error(self, error: Exception | str) -> None:
        """
        Handles WebSocket errors.

        Args:
            error (Exception): The error that occurred.
        """
        logger.error(error)
        self.state.websocket_error_reason = str(error)
        self.state.status = WebsocketStatus.ERROR
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(
                    self.event_registry.set_event("status_changed", self.state.status)
                )
        except RuntimeError:
            pass

    def _on_close(self, code: int, msg: str) -> None:
        """
        Handles WebSocket connection closure.

        Args:
            code (int): The closure code.
            msg (str): The closure message.
        """
        logger.info("Websocket connection closed.")
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            self.heartbeat_task = None

        # Drop the pending-order lifecycle state. Anything still
        # in-flight on the dropped socket cannot be reconciled — and
        # leaking the entries into the next session would cross-wire
        # any new pending placed on the same asset.
        self._active_pending.clear()
        self.pending_ticket_map.clear()
        self._exec_to_pending.clear()

        self.state.status = WebsocketStatus.DISCONNECTED
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(
                    self.event_registry.set_event("status_changed", self.state.status)
                )
        except RuntimeError:
            pass

    @property
    def websocket(self) -> Any:
        """
        Returns the active WebSocket instance.

        Returns:
            websockets.WebSocketClientProtocol: The active WebSocket
                connection or None.
        """
        return self.websocket_client.wss if self.websocket_client else None

    async def get_instruments(self) -> None:
        """Sends a request to the WebSocket to retrieve the list of
        available instruments."""
        if self.websocket:
            await self.websocket.send('42["instruments/get"]')

    async def authenticate(self) -> tuple[bool, str]:
        """
        Authenticates the user using the provided credentials.

        Performs HTTP login, retrieves cookies and SSID token, 
        and updates the browser session.

        Returns:
            tuple[bool, str]: (Success status, Error message or "Success").
        """
        async with self.login as login:
            status, msg = await login(
                self.username, self.password, self.user_data_dir
            )
        if status:
            self.state.SSID = self.session_data.get("token")
            self.is_logged = True
            # Sync session to browser client
            if "cookies" in self.session_data:
                cookie_str = self.session_data["cookies"]
                for item in cookie_str.split("; "):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        self.browser._client.cookies.set(
                            k, v, domain=self.host
                        )

            self.browser.headers.update({
                "User-Agent": self.session_data.get("user_agent", ""),
                "Referer": f"{self.https_url}/{self.lang}/trade"
            })
        return status, msg

    async def send_http_request_v1(
            self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Sends an HTTP request using the internal browser client (v1)."""
        # Browser.send_request uses self._client.request internally
        return await self.browser.send_request(method, url, **kwargs)

    async def send_http_request_v2(
            self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Sends an HTTP request using the internal browser client (v2)."""
        return await self.browser.send_request(method, url, **kwargs)

    async def send_websocket_request(self, data: str) -> None:
        """
        Sends a raw string request to the WebSocket.
        Uses a lock to ensure thread-safe sending.

        Args:
            data (str): The raw Socket.IO string to send.
        """
        async with self._ws_send_lock:
            if self.websocket:
                await self.websocket.send(data)

    async def check_connect(self) -> bool:
        """Checks if the WebSocket is currently connected."""
        return self.state.status == WebsocketStatus.CONNECTED

    async def settings_apply(
            self,
            asset: str,
            expiration: int,
            is_fast_option: bool = False,
            end_time: int | None = None
    ) -> None:
        """Apply asset and time settings before placing an order."""
        payload = {
            "asset": asset,
            "time": expiration,
            "isFastOption": is_fast_option,
        }
        if end_time:
            payload["endTime"] = end_time

        data = f'42["settings/apply", {json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def subscribe_realtime_candle(self, asset: str, period: int) -> None:
        """Subscribes to real-time price updates for a specific asset
        and period."""
        payload = {"asset": asset, "period": period}
        data = f'42["instruments/update", {json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def chart_notification(self, asset: str) -> None:
        """Requests chart notifications for a specific asset."""
        payload = {"asset": asset, "version": "1.0.0"}
        payload_json = json.dumps_str(payload)
        data = f'42["chart_notification/get", {payload_json}]'
        await self.send_websocket_request(data)

    async def follow_candle(self, asset: str) -> None:
        """Starts following the depth of market for a specific asset."""
        data = f'42["depth/follow", {json.dumps_str(asset)}]'
        await self.send_websocket_request(data)

    async def unfollow_candle(self, asset: str) -> None:
        """Stops following the depth of the market for a specific asset."""
        data = f'42["depth/unfollow", {json.dumps_str(asset)}]'
        await self.send_websocket_request(data)

    async def signals_subscribe(self) -> None:
        """Subscribes to real-time trading signals from the platform."""
        await self.send_websocket_request('42["signal/subscribe"]')

    async def change_account(
            self,
            account_type: AccountType,
            tournament_id: int = 0
    ) -> None:
        """
        Change active trading account.

        Args:
            account_type:
                REAL or DEMO account.

            tournament_id:
                Tournament/training id.
                Default 0 disables tournament mode.
        """

        self.account_type = account_type
        self.tournament_id = tournament_id

        payload = {
            "demo": int(account_type),
            "tournamentId": tournament_id
        }

        data = f'42["account/change",{json.dumps_str(payload)}]'

        await self.send_websocket_request(data)

    async def edit_training_balance(self, amount: float | int) -> None:
        """Refills the demo account balance."""
        data = f'42["demo/refill",{json.dumps_str(amount)}]'
        await self.send_websocket_request(data)

    async def change_time_offset(self, time_offset: int) -> dict[str, Any]:
        """Changes the account time offset."""
        return await self.settings.set_time_offset(time_offset)

    async def unsubscribe_realtime_candle(self, asset: str) -> None:
        """Unsubscribes from real-time price updates for a specific asset."""
        payload = {"asset": asset}
        data = f'42["instruments/unsubscribe", {json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def subscribe_Traders_mood(self, asset: str, instrument: str) -> None:
        """Subscribes to traders' mood/sentiment for a specific asset."""
        payload = {"asset": asset, "instrument": instrument}
        data = f'42["sentiment/subscribe", {json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def subscribe_all_size(self, asset: str) -> None:
        """Subscribes to all candle sizes for a specific asset."""
        payload = {"asset": asset}
        data = f'42["history/subscribe_all", {json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def get_history_line(
            self,
            asset: str,
            index: int,
            time_from: float,
            offset: int
    ) -> None:
        """Requests historical price line data."""
        payload = {
            "asset": asset,
            "index": index,
            "time": time_from,
            "offset": offset
        }
        data = f'42["history/load", {json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def open_pending(
            self,
            amount: float | int,
            asset: str,
            direction: str,
            duration: int,
            open_time: str | None = None,
    ) -> None:
        """Place a time-based pending order.

        Wire shape verified by live capture against
        ``ws2.qxbroker.com``::

            {
              "openType": 0,
              "asset": "AUDNZD_otc",
              "openTime": "2026-05-05T14:03:00.000Z",
              "timeframe": 60,
              "command": 0,
              "amount": 1.0
            }

        Server confirms with ``s_pending/create`` carrying a
        ``ticket`` UUID under the ``"pending"`` key.

        The previous capture (``action`` / ``time`` / ``isDemo`` /
        ``tournamentId`` / ``requestId``) turned out to belong to a
        different broker version or modal; the live ``ws2`` server
        rejects that shape. This shape is the one that actually
        produces an accepted ``s_pending/create`` response.

        Notes on the fields:

        * ``openType`` — ``0`` means the time-scheduled flavour. The
          quote-triggered variant uses ``1``.
        * ``command`` — **must be the string** ``"call"`` (up) or
          ``"put"`` (down). Sending an integer (``0``/``1``/``2``)
          looks accepted at create time but the server stores it as
          ``null`` in the order DB; when the pending later fires at
          ``openTime`` the executor reads ``command=null`` and emits
          ``f_pending/opened`` with ``error: 9`` — the trade never
          executes. Verified end-to-end against ``ws2.qxbroker.com``.
        * ``timeframe`` — duration of the resulting trade in seconds.
          Replaces the old ``time`` field.
        * ``openTime`` — **must** be an ISO 8601 UTC string with the
          ``YYYY-MM-DDTHH:MM:SS.000Z`` format. Integers (any value)
          are rejected with ``{"error": "open_time_min"}``. When
          ``open_time`` is ``None``, this method auto-schedules to
          the next ``duration`` boundary at least 90 s ahead.

        Args:
            amount: Investment amount.
            asset: Trading pair (``"EURUSD_otc"``, ``"AUDNZD_otc"``…).
            direction: ``"up"`` / ``"call"`` or ``"down"`` / ``"put"``.
            duration: Resulting trade duration in seconds.
            open_time: Explicit ISO 8601 UTC string for the schedule
                window. ``None`` → auto-schedule.
        """
        from datetime import datetime, timezone

        if not isinstance(duration, int) or duration <= 0:
            raise ValueError(
                f"duration must be a positive integer (got {duration!r})"
            )
        command = "put" if direction in ("down", "put") else "call"

        if open_time is None:
            # Auto-schedule: next ``duration`` boundary at least 90 s
            # ahead, expressed as a UTC ISO string.
            now = int(time.time())
            min_open = now + 90
            ts = ((min_open // duration) + 1) * duration
            open_time_iso = datetime.fromtimestamp(
                ts, tz=timezone.utc
            ).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        else:
            open_time_iso = open_time

        payload: dict[str, Any] = {
            "openType": 0,
            "asset": asset,
            "openTime": open_time_iso,
            "timeframe": int(duration),
            "command": command,
            "amount": float(amount),
        }
        data = f'42["pending/create",{json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def open_pending_at_price(
            self,
            amount: float | int,
            asset: str,
            direction: str,
            quote: float,
            period: str = "M1",
    ) -> None:
        """Place a quote-triggered pending order.

        Quote-mode pending fires when the asset price hits ``quote``.
        Wire shape mirrors :meth:`open_pending` but with
        ``openType=1``, ``quote`` (instead of ``openTime``), and
        ``period`` (instead of ``timeframe``)::

            {
              "openType": 1,
              "asset": "EURUSD_otc",
              "quote": 184.379,
              "period": "M1",
              "command": "call",
              "amount": 1.0
            }

        ``command`` is the string ``"call"``/``"put"`` (matching
        :meth:`open_pending` and :meth:`buy`); see the time-mode
        docstring for why integer commands break execution.

        .. warning::
            **Experimental — not end-to-end verified.** Only time-mode
            (:meth:`open_pending`) has been confirmed against
            ``ws2.qxbroker.com``. The quote-mode wire shape mirrors
            the time-mode pattern with field substitutions captured
            from the web-client modal, but has not been observed
            firing live. Test on a demo account before relying on it.
        """
        command = "put" if direction in ("down", "put") else "call"
        payload: dict[str, Any] = {
            "openType": 1,
            "asset": asset,
            "quote": float(quote),
            "period": period,
            "command": command,
            "amount": float(amount),
        }
        data = f'42["pending/create",{json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def instruments_follow(self, asset: str) -> None:
        """Subscribe to the instrument feed for a pending order's asset.

        Quotex emits ``instruments/follow`` so the client receives candle
        and price updates for an asset whose pending order has been
        accepted but hasn't fired yet. Previously this method was an
        alias of ``open_pending`` — that bug duplicated every pending
        order on the wire.
        """
        payload = {"asset": asset}
        data = f'42["instruments/follow", {json.dumps_str(payload)}]'
        await self.send_websocket_request(data)

    async def start_websocket(self) -> tuple[bool, str]:
        """
        Initializes and starts the WebSocket connection.
        Attempts to authenticate if no SSID is present.

        Returns:
            tuple[bool, str]: (Success status, Connection status message).
        """
        # Cancel any leftover task from a previous connection attempt
        # before creating a new one, so reconnects don't leak tasks.
        if self._websocket_task and not self._websocket_task.done():
            self._websocket_task.cancel()
            try:
                await self._websocket_task
            except (asyncio.CancelledError, Exception):
                pass

        self.state.status = WebsocketStatus.CONNECTING
        self.state.auth_status = AuthStatus.NOT_AUTHENTICATED
        await self.event_registry.set_event("status_changed", self.state.status)
        if not self.state.SSID:
            await self.authenticate()

        self.websocket_client = WebsocketClient(self)
        extra_headers = {
            "User-Agent": self.session_data.get("user_agent", ""),
            "Origin": self.https_url,
            "Cookie": self.session_data.get("cookies", ""),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        ws_url = self.wss_url
        ws_proxy = None
        server_hostname = None
        if self.proxy_config:
            extra_headers = self.proxy_config.merge_headers(extra_headers)
            ws_proxy = self.proxy_config.websockets_proxy()
            ws_url, dns_headers = self.proxy_config.resolve_url(self.wss_url)
            if dns_headers:
                # Preserve original SNI so the TLS certificate keeps validating.
                server_hostname = dns_headers.get("Host")
                extra_headers.update(dns_headers)

        self._websocket_task = asyncio.create_task(
            self.websocket_client.run_forever(
                url=ws_url,
                extra_headers=extra_headers,
                ssl=unified_ssl_context,
                proxy=ws_proxy,
                server_hostname=server_hostname,
            )
        )
        for _ in range(100):
            if self.state.status == WebsocketStatus.ERROR:
                return False, self.state.websocket_error_reason
            if self.state.status == WebsocketStatus.CONNECTED:
                return True, "Connected"
            await asyncio.sleep(0.1)
        return False, "Timeout"

    async def send_ssid(self, auth_timeout: float = 10.0) -> bool:
        """Send the SSID token and wait for the broker's authorization
        response before returning.

        v1.4.x bugfix: previously fired-and-forgot — the caller had to
        guess when ``auth_status`` had flipped to ``AUTHENTICATED``.
        Quotex's ``s_authorization`` response lands ~500–1000 ms after
        the SSID frame, so callers that immediately read
        ``auth_status`` would race and see ``NOT_AUTHENTICATED``,
        falsely concluding "Websocket connection rejected." (V1.2.x
        masked this with an unconditional 2 s ``asyncio.sleep`` inside
        ``check_connect``; v1.3.0 dropped the sleep for latency, but
        without a proper auth wait the race was exposed.)

        Returns:
            ``True`` iff ``auth_status`` is ``AUTHENTICATED`` after the
            broker replied. ``False`` on rejection or timeout.
        """
        if not self.state.SSID:
            return False

        # Clear any stale ``auth_changed`` event from a previous
        # connect — otherwise ``wait_event`` returns immediately with
        # the previous result.
        await self.event_registry.clear_event("auth_changed")
        # Reset auth_status to the in-flight value so a stale flip
        # from a prior session doesn't claim "AUTHENTICATED" before
        # the broker has actually responded.
        if self.state.auth_status != AuthStatus.FAILED:
            self.state.auth_status = AuthStatus.AUTHENTICATING

        await self.ssid(self.state.SSID)

        try:
            await self.event_registry.wait_event(
                "auth_changed", timeout=auth_timeout,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "send_ssid: timed out after %ss waiting for "
                "broker authorization response", auth_timeout,
            )
            return False

        return self.state.auth_status == AuthStatus.AUTHENTICATED

    async def connect(self, is_demo: bool) -> tuple[bool, str]:
        """
        Connects to the Quotex platform.

        Args:
            is_demo (bool): True to connect to a DEMO account, False for REAL.

        Returns:
            tuple[bool, str]: (Connection success, Status message).
        """
        self.account_type = (
            AccountType.DEMO if is_demo else AccountType.REAL
        )
        ok, reason = await self.start_websocket()
        if not ok:
            return ok, reason

        if not await self.send_ssid():
            # WS came up but the broker didn't authorize — surface the
            # specific reason if it set one (``authorization/reject``
            # writes ``websocket_error_reason``); otherwise it's a
            # timeout.
            return False, (
                self.state.websocket_error_reason
                or "Authorization timeout"
            )

        self._start_reconnect_supervisor()
        return True, reason

    def _start_reconnect_supervisor(self) -> None:
        """Spawn the reconnect supervisor on first successful connect."""
        if not self.reconnect_policy.enabled:
            return
        if self.reconnect_supervisor is None:
            self.reconnect_supervisor = ReconnectSupervisor(
                self, self.reconnect_policy
            )
        self.reconnect_supervisor.capture()
        self.reconnect_supervisor.start()

    async def close(self) -> bool:
        """Closes the WebSocket connection and the HTTP client session."""
        if self.reconnect_supervisor:
            await self.reconnect_supervisor.stop()
        if self.websocket_client:
            await self.websocket_client.close()
            # Explicitly trigger cleanup to ensure heartbeat is cancelled
            self._on_close(1000, "Graceful closure")
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.settings:
            try:
                await self.settings.close()
            except Exception:
                pass
        if self._http_client:
            await self._http_client.aclose()
        return True

    @property
    def logout(self) -> Logout:
        """Returns the Logout action handler."""
        return Logout(self)

    @property
    def login(self) -> Login:
        """Returns the Login action handler."""
        proxy_url: str | None = None
        if self.proxy_config and self.proxy_config.url:
            proxy_url = self.proxy_config.url
        elif isinstance(self.proxies, str) and "://" in self.proxies:
            proxy_url = self.proxies
        return Login(
            self,
            proxies=proxy_url,
            proxy_config=self.proxy_config,
        )

    @property
    def ssid(self) -> Ssid:
        """Returns the SSID authorization handler."""
        return Ssid(self)

    @property
    def buy(self) -> Buy:
        """Returns the Buy order handler."""
        return Buy(self)

    @property
    def sell_option(self) -> SellOption:
        """Returns the Sell Option handler."""
        return SellOption(self)

    @property
    def get_candles(self) -> GetCandles:
        """Returns the Candles retrieval handler."""
        return GetCandles(self)

    @property
    def get_history(self) -> GetHistory:
        """Returns the Trade History retrieval handler."""
        return GetHistory(self)

    async def get_profile(self) -> Profile:
        """
        Retrieves and parses the user profile data.

        Updates the internal profile object with nickname, balances, 
        country, and timezone.

        Returns:
            Profile: The updated profile object.
        """
        user_settings = await self.settings.get_settings()
        d = user_settings.get("data", {})
        self.profile.nick_name = d.get("nickname")
        self.profile.profile_id = d.get("id")
        self.profile.demo_balance = float(d.get("demoBalance", 0))
        self.profile.live_balance = float(d.get("liveBalance", 0))
        self.profile.currency_code = d.get("currencyCode")
        self.profile.currency_symbol = d.get("currencySymbol")
        self.profile.country_name = d.get("countryName")
        self.profile.offset = d.get("timeOffset")
        return self.profile

    async def get_trader_history(
            self, account_type: int, page: int
    ) -> dict[str, Any]:
        """
        Retrieves the trade history for a specific account and page.

        Args:
            account_type (int): AccountType.REAL or AccountType.DEMO.
            page (int): Page number to retrieve.

        Returns:
            dict: The trade history data.
        """
        history = await self.get_history(account_type, page)
        return history.get("data", {})
