"""Candles CLI command handlers."""
import argparse
import asyncio
import time
from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from pyquotex.cli.formatters import _print_candles_table, _save_candles_csv
from pyquotex.cli.runtime import _is_demo, connect_with_retry
from pyquotex.stable_api import Quotex

console = Console()


async def cmd_candles(client: Quotex, args: argparse.Namespace) -> None:
    """Fetch latest candles for an asset (up to 199 per call)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    candles = await client.get_candles(
        asset, time.time(), args.period * args.count, args.period
    )
    if not candles:
        console.print("[red]No candle data received.[/]")
        return
    _print_candles_table(candles[-args.count:], asset, args.period)


async def cmd_candles_v2(client: Quotex, args: argparse.Namespace) -> None:
    """Fetch candles via the v2 API path."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    candles = await client.get_candle_v2(asset, args.period)
    if not candles:
        console.print("[red]No v2 candle data received.[/]")
        return
    _print_candles_table(candles, asset, args.period, title="Candles (v2)")


async def cmd_candles_deep(client: Quotex, args: argparse.Namespace) -> None:
    """Fetch deep historical candle data using parallel workers."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    if args.workers > 10:
        console.print(
            "[bold red]⚠ WARNING:[/] workers > 10 may cause a ban. "
            "Clamping to 10."
        )
        args.workers = 10

    asset, _ = await client.get_available_asset(args.asset, force_open=True)

    def _progress_cb(done: int, total: int, count: int, label: str) -> None:
        pct = int(done / total * 100) if total else 0
        console.print(
            f"  [dim]{label}[/] {pct}% — {count} candles collected",
            end="\r",
        )

    with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Fetching deep history…"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
            console=console,
    ) as prog:
        prog.add_task("fetch")
        candles = await client.get_historical_candles(
            asset,
            amount_of_seconds=args.seconds,
            period=args.period,
            max_workers=args.workers,
            progress_callback=_progress_cb,
        )

    console.print(f"\n[green]✓[/] {len(candles)} candles fetched.")
    _print_candles_table(candles[-20:], asset, args.period,
                         title=f"Last 20 of {len(candles)} candles (deep)")

    if args.output:
        _save_candles_csv(candles, args.output)
        console.print(f"[green]✓ Saved to {args.output}[/]")


async def cmd_history_line(client: Quotex, args: argparse.Namespace) -> None:
    """Fetch raw historical price-line data."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    await client.get_all_assets()
    data = await client.get_history_line(
        asset, time.time(), args.offset
    )
    if not data:
        console.print("[red]No history-line data received.[/]")
        return
    console.print(Panel(
        str(data)[:2000],
        title=f"📈 [bold]History Line — {asset}[/]",
        border_style="blue",
        box=box.ROUNDED,
    ))


async def cmd_candle_info(client: Quotex, args: argparse.Namespace) -> None:
    """Show opening / closing / remaining time of the current candle."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    await client.start_candles_stream(asset, args.period)
    await asyncio.sleep(1)  # let stream warm up
    info = await client.opening_closing_current_candle(asset, args.period)
    if not info:
        console.print("[red]Could not retrieve candle info.[/]")
        return
    opening = datetime.fromtimestamp(info.get("opening", 0))
    closing = datetime.fromtimestamp(info.get("closing", 0))
    console.print(Panel(
        f"[bold cyan]Asset:[/]      {asset}\n"
        f"[bold cyan]Period:[/]     {args.period}s\n"
        f"[bold cyan]Opening:[/]    {opening.strftime('%H:%M:%S')}\n"
        f"[bold cyan]Closing:[/]    {closing.strftime('%H:%M:%S')}\n"
        f"[bold yellow]Remaining:[/] {info.get('remaining', '?')}s",
        title="🕯️  [bold]Current Candle Info[/]",
        border_style="cyan",
        box=box.ROUNDED,
        expand=False,
    ))
    await client.stop_candles_stream(asset)
