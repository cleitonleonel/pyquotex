"""Asset metadata, instruments, and payout methods extracted from Quotex.

This mixin is composed into Quotex via multiple inheritance. It uses
self.api, self.codes_asset, etc. — all set up in Quotex.__init__ inside
pyquotex/stable_api.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyquotex._api._constants import DEFAULT_TIMEOUT
from pyquotex._api._waits import wait_until

logger = logging.getLogger(__name__)


class AssetsMixin:
    """Methods related to instruments, assets metadata, and payouts."""

    async def get_instruments(self, timeout: int = DEFAULT_TIMEOUT) -> list[Any]:
        """Get instruments using a true event-driven approach."""
        if not self.api or not await self.check_connect():
            return []

        if self.api.instruments and len(self.api.instruments) > 0:
            return self.api.instruments

        try:
            # Request instruments explicitly
            await self.api.get_instruments()
            # Wait for WebSocket event signaling instruments arrival
            await self.api.event_registry.wait_event("instruments_ready", timeout=timeout)

            if not self.api.instruments:
                # Try one last wait if empty — event-driven up to 2s
                try:
                    await wait_until(
                        lambda: bool(self.api and self.api.instruments),
                        timeout=2,
                        poll_interval=0.1,
                    )
                except asyncio.TimeoutError:
                    pass

            return self.api.instruments or []
        except TimeoutError:
            logger.error("Timeout waiting for instruments after %ds", timeout)
            return []

    def get_all_asset_name(self) -> list[list[str]] | None:
        """
        Retrieves names of all available assets.

        Returns:
            list: List of assets with ID and display name.
        """
        if self.api and self.api.instruments:
            return [[i[1], i[2].replace("\n", "")] for i in self.api.instruments]
        return None

    async def get_available_asset(
            self, asset_name: str, force_open: bool = False
    ) -> tuple[str, Any]:
        """
        Retrieves detailed information for an asset if it is currently open.

        Args:
            asset_name (str): Asset name.
            force_open (bool, optional): Try to find the OTC version if closed.
                Defaults to False.

        Returns:
            tuple: (Final asset name, Asset status info).
        """
        _, asset_open = await self.check_asset_open(asset_name)
        if force_open and (not asset_open or not asset_open[2]):
            condition_otc = "otc" not in asset_name
            refactor_asset = asset_name.replace("_otc", "")
            asset_name = f"{asset_name}_otc" if condition_otc else refactor_asset
            _, asset_open = await self.check_asset_open(asset_name)

        return asset_name, asset_open

    async def check_asset_open(
            self, asset_name: str
    ) -> tuple[list[Any] | None, tuple[Any, Any, Any]]:
        """
        Checks if a specific asset is currently available for trading.

        Args:
            asset_name (str): The name of the asset.

        Returns:
            tuple: (Raw instrument data, Formatted status info).
        """
        instruments = await self.get_instruments()
        for i in instruments:
            if asset_name == i[1]:
                if self.api:
                    self.api.current_asset = asset_name
                return i, (i[0], i[2].replace("\n", ""), i[14])

        return None, (None, None, None)

    async def get_all_assets(self) -> dict[str, str]:
        """
        Retrieves a mapping of all asset names to their internal codes.

        Returns:
            dict: Mapping of asset names to codes.
        """
        instruments = await self.get_instruments()
        for i in instruments:
            if i[0] != "":
                self.codes_asset[i[1]] = i[0]

        return self.codes_asset

    def get_payment(self) -> dict[str, Any]:
        """Retrieves the payout/payment percentages for all instruments."""
        if self.api is None:
            return {}

        assets_data = {}
        for i in self.api.instruments:
            assets_data[i[2].replace("\n", "")] = {
                "turbo_payment": i[18],
                "payment": i[5],
                "profit": {"1M": i[-9], "5M": i[-8]},
                "open": i[14],
            }

        return assets_data

    def get_payout_by_asset(
            self, asset_name: str, timeframe: str = "1"
    ) -> float | dict[str, Any] | None:
        """Retrieves the payout percentage for a specific asset and
        timeframe."""
        if self.api is None:
            return None

        assets_data = {}
        for i in self.api.instruments:
            if asset_name == i[1]:
                assets_data[i[1].replace("\n", "")] = {
                    "turbo_payment": i[18],
                    "payment": i[5],
                    "profit": {"24H": i[-10], "1M": i[-9], "5M": i[-8]},
                    "open": i[14],
                }
                break

        data = assets_data.get(asset_name)
        if data is None:
            return None

        if timeframe == "all":
            return data.get("profit")

        profit = data.get("profit")
        if profit:
            return profit.get(f"{timeframe}M")
        return None
