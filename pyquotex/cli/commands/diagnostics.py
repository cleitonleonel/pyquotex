"""Diagnostics CLI command handlers."""

import argparse
import asyncio
import time
from typing import Any

from rich.console import Console

from pyquotex.cli.runtime import connect_with_retry
from pyquotex.stable_api import Quotex

console = Console()


async def cmd_test_all(client: Quotex, args: argparse.Namespace) -> None:
    """Run a quick smoke-test of every major API method."""
    console.rule("[bold cyan]PyQuotex — test-all[/]")
    passed = 0
    failed = 0

    async def _test(name: str, coro: Any) -> None:
        nonlocal passed, failed
        try:
            result = await coro
            console.print(f"  [green]✓[/] {name}: {str(result)[:80]}")
            passed += 1
        except Exception as e:
            console.print(f"  [red]✗[/] {name}: {e}")
            failed += 1

    if not await connect_with_retry(client, True):
        return

    await client.get_all_assets()

    await _test("get_profile", client.get_profile())
    await _test("get_balance", client.get_balance())
    await _test("get_server_time", client.get_server_time())
    await _test("get_all_asset_name", asyncio.coroutine(lambda: client.get_all_asset_name())())
    await _test("get_payment (sync)", asyncio.coroutine(lambda: client.get_payment())())
    await _test(
        "get_payout_by_asset EURUSD",
        asyncio.coroutine(lambda: client.get_payout_by_asset("EURUSD"))(),
    )
    await _test("get_candles EURUSD 60s", client.get_candles("EURUSD", time.time(), 3600, 60))
    await _test("get_candle_v2 EURUSD", client.get_candle_v2("EURUSD", 60))
    await _test(
        "get_historical_candles EURUSD 1h",
        client.get_historical_candles("EURUSD", amount_of_seconds=3600, period=60, max_workers=2),
    )
    await _test("get_realtime_price EURUSD", client.start_realtime_price("EURUSD", 60))
    await _test("get_realtime_sentiment EURUSD", client.start_realtime_sentiment("EURUSD", 60))
    await _test("get_trader_history demo p1", client.get_trader_history(1, 1))
    await _test(
        "calculate_indicator RSI",
        client.calculate_indicator("EURUSD", "RSI", {"period": 14}, timeframe=60),
    )

    console.rule()
    color = "green" if failed == 0 else "yellow"
    console.print(f"[bold {color}]Results: {passed} passed, {failed} failed[/]")
