"""Account-related methods extracted from Quotex.

This mixin is composed into Quotex via multiple inheritance. It uses
self.api, self.session_data, self.account_is_demo, etc. — all set up in
Quotex.__init__ inside pyquotex/stable_api.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from pyquotex import expiration
from pyquotex._api._constants import DEFAULT_TIMEOUT
from pyquotex.api import QuotexAPI
from pyquotex.exceptions import QuotexTimeoutError
from pyquotex.utils.account_type import AccountType
from pyquotex.utils.services import truncate

logger = logging.getLogger(__name__)


class AccountMixin:
    """Methods related to account state, profile, balance, and session."""

    async def connect(self) -> tuple[bool, str]:
        """Establishes a connection to the Quotex API."""
        if self.api and await self.check_connect():
            return True, "Already connected"
        self.api = QuotexAPI(
            self.host,
            self.email,
            self.password,
            self.lang,
            resource_path=self.resource_path,
            user_data_dir=self.user_data_dir,
            proxies=self.proxies,
            on_otp_callback=self.on_otp_callback,
            reconnect_policy=getattr(self, "reconnect_policy", None),
            wss_url_override=getattr(self, "wss_url_override", None),
        )

        self.api.trace_ws = self.debug_ws_enable
        self.api.session_data = self.session_data
        self.api.current_asset = self.asset_default
        self.api.current_period = self.period_default
        self.api.state.SSID = self.session_data.get("token")

        if not self.session_data.get("token"):
            check, reason = await self.api.authenticate()
            if not check:
                return check, reason

        check, reason = await self.api.connect(self.account_is_demo == AccountType.DEMO)
        if not await self.check_connect():
            logger.error(
                "Websocket failed to connect or connection was rejected."
            )
            if "token" in self.session_data:
                self.session_data["token"] = None
            return False, "Websocket connection rejected."

        return check, reason

    async def reconnect(self) -> None:
        """Attempts to re-authenticate and refresh the session."""
        if self.api:
            await self.api.authenticate()

    def set_account_mode(self, balance_mode: str = "PRACTICE") -> None:
        """Set active account `real` or `practice`"""
        if balance_mode.upper() == "REAL":
            self.account_is_demo = AccountType.REAL
        elif balance_mode.upper() == "PRACTICE":
            self.account_is_demo = AccountType.DEMO
        else:
            raise ValueError(
                f"Invalid balance mode '{balance_mode}'. "
                "Use 'REAL' or 'PRACTICE'."
            )

    async def change_account(self, balance_mode: str, tournament_id: int = 0) -> None:
        """Change active account `real` or `practice` or a specific tournament"""
        self.account_is_demo = (
            AccountType.REAL if balance_mode.upper() == "REAL"
            else AccountType.DEMO
        )
        if self.api:
            await self.api.change_account(self.account_is_demo, tournament_id=tournament_id)

    async def change_time_offset(self, time_offset: int) -> Any:
        """Updates the timezone/time offset on the server."""
        if self.api:
            return await self.api.change_time_offset(time_offset)
        return None

    async def edit_practice_balance(
            self,
            amount: float | int | None = None,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Refills the demo account balance."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        self.api.training_balance_edit_request = None
        await self.api.edit_training_balance(
            amount if amount is not None else 0
        )
        # TODO(refactor/architecture Phase 2.4): polling here cannot be migrated
        # to SlotRegistry until we identify the WS event that should populate
        # self.api.training_balance_edit_request. No producer exists in
        # pyquotex/api.py:_on_message, so this method currently only exits via
        # the timeout branch below. Investigate live WS traffic when refilling
        # demo balance to find the correct producer event, then either:
        #   (a) wire that handler to fire self.slots.training_balance_edit, or
        #   (b) repoint this method to wait on self.slots.balance and return
        #       the new balance dict (changes return shape — would be a
        #       breaking change for any caller relying on the request payload).
        start = time.time()
        while self.api.training_balance_edit_request is None:
            if time.time() - start > timeout:
                raise TimeoutError(
                    "Timeout waiting for practice balance edit response."
                )
            await asyncio.sleep(0.2)
        return self.api.training_balance_edit_request

    async def get_balance(self, timeout: int = DEFAULT_TIMEOUT) -> float:
        """Get account balance using a true event-driven approach."""
        if not self.api or not await self.check_connect():
            raise RuntimeError("Not connected to Quotex")

        if self.api.account_balance is not None:
            if self.api.account_type == AccountType.DEMO:
                balance = self.api.account_balance.get("demoBalance", 0)
            else:
                balance = self.api.account_balance.get("liveBalance", 0)
            return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

        if self.api.account_balance is None:
            try:
                await self.api.slots.balance.wait(timeout=timeout)
            except asyncio.TimeoutError:
                raise QuotexTimeoutError(
                    f"get_balance timed out after {timeout}s"
                )

        if self.api.account_balance is None:
            return 0.0

        if self.api.account_type == AccountType.DEMO:
            balance = self.api.account_balance.get("demoBalance", 0)
        else:
            balance = self.api.account_balance.get("liveBalance", 0)
        return float(f"{truncate(balance + self.get_profit(), 2):.2f}")

    async def get_profile(self) -> Any:
        """Retrieves and parses the user profile data."""
        if self.api:
            return await self.api.get_profile()
        return None

    async def get_server_time(self) -> int:
        """Retrieves and syncs the server time."""
        if self.api is None:
            return int(time.time())

        user_settings = await self.get_profile()
        offset_zone = user_settings.offset if user_settings else 0
        self.api.timesync.server_timestamp = (
            expiration.get_server_timer(offset_zone)
        )
        return self.api.timesync.server_timestamp

    async def start_remaing_time(self) -> None:
        """Debug helper to log the remaining time until the next server
        expiration."""
        if self.api is None:
            return

        now_stamp = datetime.fromtimestamp(expiration.get_timestamp())
        expiration_stamp = datetime.fromtimestamp(
            self.api.timesync.server_timestamp
        )
        remaing_time = int((expiration_stamp - now_stamp).total_seconds())
        while remaing_time >= 0:
            remaing_time -= 1
            logger.debug("Remaining %d seconds...", max(remaing_time, 0))
            await asyncio.sleep(1)

    async def store_settings_apply(
            self,
            asset: str = "EURUSD",
            period: int = 0,
            time_mode: str = "TIMER",
            deal: int = 5,
            percent_mode: bool = False,
            percent_deal: int = 1,
            timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Applies trading settings and retrieves updated settings."""
        if self.api is None:
            raise RuntimeError("API not initialized")

        is_fast_option = False if time_mode.upper() == "TIMER" else True
        self.api.current_asset = asset
        await self.api.settings_apply(
            asset,
            period,
            is_fast_option=is_fast_option,
            deal=deal,
            percent_mode=percent_mode,
            percent_deal=percent_deal
        )
        await asyncio.sleep(0.2)
        start = time.time()
        while True:
            if self.api.settings_list:
                investments_settings = self.api.settings_list
                break
            if time.time() - start > timeout:
                raise TimeoutError("Timeout waiting for settings response.")
            await asyncio.sleep(0.2)

        return investments_settings
