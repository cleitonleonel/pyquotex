"""Realtime CLI command handlers."""

import argparse
import asyncio
from datetime import datetime

from rich.console import Console

from pyquotex.cli.runtime import _is_demo, connect_with_retry
from pyquotex.stable_api import Quotex

console = Console()


async def cmd_realtime_price(client: Quotex, args: argparse.Namespace) -> None:
    """Stream live price data for an asset (Ctrl+C to stop)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(f"[cyan]Streaming live price for[/] [bold]{asset}[/] [dim](Ctrl+C to stop)[/]")
    await client.start_realtime_price(asset, args.period)
    try:
        while True:
            prices = await client.get_realtime_price(asset)
            if prices:
                latest = prices[-1]
                console.print(
                    f"  [dim]{datetime.now().strftime('%H:%M:%S')}[/]  "
                    f"[bold green]{latest.get('price', latest)}[/]",
                    end="\r",
                )
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stream stopped.[/]")
    finally:
        await client.stop_candles_stream(asset)


async def cmd_realtime_sentiment(client: Quotex, args: argparse.Namespace) -> None:
    """Stream live trader-sentiment data (Ctrl+C to stop)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(f"[cyan]Streaming sentiment for[/] [bold]{asset}[/] [dim](Ctrl+C to stop)[/]")
    await client.start_realtime_sentiment(asset, args.period)
    try:
        while True:
            sentiment = await client.get_realtime_sentiment(asset)
            if sentiment:
                bulls = sentiment.get("call", sentiment.get("bulls", "?"))
                bears = sentiment.get("put", sentiment.get("bears", "?"))
                console.print(
                    f"  [dim]{datetime.now().strftime('%H:%M:%S')}[/]  "
                    f"[green]CALL {bulls}%[/]  [red]PUT {bears}%[/]",
                    end="\r",
                )
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stream stopped.[/]")
    finally:
        await client.stop_candles_stream(asset)


async def cmd_realtime_candle(client: Quotex, args: argparse.Namespace) -> None:
    """Stream live processed candle ticks (Ctrl+C to stop)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(f"[cyan]Streaming candle ticks for[/] [bold]{asset}[/] [dim](Ctrl+C to stop)[/]")
    try:
        while True:
            candle = await client.start_realtime_candle(asset, args.period)
            if candle:
                console.print(
                    f"  [dim]{datetime.now().strftime('%H:%M:%S')}[/]  {candle}",
                    end="\r",
                )
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stream stopped.[/]")
    finally:
        await client.stop_candles_stream(asset)
