"""Analysis CLI command handlers."""

import argparse
import asyncio
from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from pyquotex.cli.runtime import _is_demo, connect_with_retry
from pyquotex.stable_api import Quotex
from pyquotex.utils.strategy import TripleConfirmationStrategy

console = Console()


async def cmd_signals(client: Quotex, args: argparse.Namespace) -> None:
    """Fetch current signal data from the signals stream."""
    if not await connect_with_retry(client, True):
        return
    await client.start_signals_data()
    await asyncio.sleep(2)  # allow signals to arrive
    data = client.get_signal_data()
    if not data:
        console.print("[yellow]No signal data available yet.[/]")
        return
    table = Table(
        title="📡 [bold]Signal Data[/]",
        box=box.ROUNDED,
        border_style="yellow",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")
    for k, v in data.items():
        table.add_row(str(k), str(v))
    console.print(table)


async def cmd_history(client: Quotex, args: argparse.Namespace) -> None:
    """Show recent trade history (paged)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    all_trades: list[dict] = []
    account_type = 1 if is_demo else 0
    for page in range(1, args.pages + 1):
        page_data = await client.get_trader_history(account_type, page)
        if isinstance(page_data, dict):
            trades = page_data.get("data", [])
        elif isinstance(page_data, list):
            trades = page_data
        else:
            trades = []
        all_trades.extend(trades)

    if not all_trades:
        console.print("[yellow]No trade history found.[/]")
        return

    table = Table(
        title=f"📜 [bold]Trade History[/] ({'Demo' if is_demo else 'Live'})",
        box=box.ROUNDED,
        border_style="bright_blue",
        show_header=True,
        header_style="bold bright_white on blue",
        row_styles=["none", "dim"],
    )
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Asset", style="cyan")
    table.add_column("Direction", justify="center")
    table.add_column("Amount", justify="right")
    table.add_column("Profit", justify="right")
    table.add_column("Result", justify="center")
    table.add_column("Time", style="dim")

    for t in all_trades:
        profit = float(t.get("profitAmount", 0))
        result_str = (
            "[green]WIN[/]" if profit > 0 else "[red]LOSS[/]" if profit < 0 else "[dim]DRAW[/]"
        )
        direction = t.get("command", t.get("direction", "?")).upper()
        dir_color = "green" if direction in ("CALL", "BUY", "UP") else "red"
        ts = t.get("openTimestamp", t.get("createdAt", ""))
        try:
            ts_str = datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M") if ts else "—"
        except Exception:
            ts_str = str(ts)
        table.add_row(
            str(t.get("ticket", t.get("id", "—")))[:12],
            str(t.get("asset", "?")),
            f"[{dir_color}]{direction}[/{dir_color}]",
            f"{float(t.get('amount', 0)):,.2f}",
            f"{profit:+,.2f}",
            result_str,
            ts_str,
        )
    console.print(table)


async def cmd_indicator(client: Quotex, args: argparse.Namespace) -> None:
    """Calculate a technical indicator and display the result."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(
        f"[cyan]Calculating[/] [bold]{args.name}[/] for "
        f"[yellow]{asset}[/] (period={args.period}, tf={args.timeframe}s)"
    )
    with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Fetching history + computing…"),
            transient=True,
            console=console,
    ) as prog:
        prog.add_task("indicator")
        result = await client.calculate_indicator(
            asset,
            args.name,
            params={"period": args.period},
            timeframe=args.timeframe,
        )
    if not result:
        console.print("[red]No indicator data returned.[/]")
        return
    table = Table(
        title=f"📐 [bold]{args.name} — {asset}[/]",
        box=box.ROUNDED,
        border_style="magenta",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="bold yellow")
    if isinstance(result, dict):
        for k, v in result.items():
            table.add_row(str(k), f"{v:.6f}" if isinstance(v, float) else str(v))
    else:
        table.add_row("result", str(result))
    console.print(table)


async def cmd_monitor(client: Quotex, args: argparse.Namespace) -> None:
    """Real-time price monitor for an asset (Ctrl+C to stop)."""
    if not await connect_with_retry(client, True):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(
        f"[cyan]Monitoring[/] [bold]{asset}[/] [dim](period={args.period}s — Ctrl+C to stop)[/]"
    )
    await client.start_candles_stream(asset, args.period)
    prev_price = None
    try:
        while True:
            prices = await client.get_realtime_price(asset)
            if prices:
                latest = prices[-1]
                price = latest.get("price", latest)
                change = ""
                if prev_price is not None:
                    delta = float(price) - float(prev_price)
                    change = (
                        f" [green]+{delta:.5f}[/]"
                        if delta > 0
                        else f" [red]{delta:.5f}[/]"
                        if delta < 0
                        else " [dim]—[/]"
                    )
                console.print(
                    f"  [dim]{datetime.now().strftime('%H:%M:%S')}[/]  "
                    f"[bold]{price}[/]{change}      ",
                    end="\r",
                )
                prev_price = price
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor stopped.[/]")
    finally:
        await client.stop_candles_stream(asset)


async def cmd_strategy(client: Quotex, args: argparse.Namespace) -> None:
    """Run a Triple-Confirmation strategy."""
    if not await connect_with_retry(client, True):
        return
    strategy = TripleConfirmationStrategy(
        client=client,
        asset=args.asset,
        period=args.period,
    )
    console.print(
        Panel(
            f"[bold cyan]Asset:[/]      {args.asset}\n"
            f"[bold cyan]Period:[/]     {args.period}s\n"
            f"[bold cyan]Auto-trade:[/] {'YES ⚠ DEMO ONLY' if args.auto_trade else 'NO (signal only)'}",
            title="🧠 [bold]Triple Confirmation Strategy[/]",
            border_style="magenta",
            box=box.ROUNDED,
            expand=False,
        )
    )
    await strategy.run(auto_trade=args.auto_trade)
