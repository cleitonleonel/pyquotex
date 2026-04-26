#!/usr/bin/env python3
"""PyQuotex CLI — Fast Quotex trading API client."""
import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from typing import Any, Callable

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
)
from rich.table import Table

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex
from pyquotex.utils.strategy import TripleConfirmationStrategy

console = Console()
logger = logging.getLogger(__name__)

# Global to track current progress for OTP handling
current_progress: Progress | None = None


async def on_otp(message: str) -> str:
    """Callback to handle OTP input, pausing progress spinners if active."""
    if current_progress:
        current_progress.stop()
        try:
            pin = console.input(f"[bold yellow]🔐 {message}[/]")
            return pin
        finally:
            current_progress.start()
    else:
        return console.input(f"[bold yellow]🔐 {message}[/]")

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyquotex",
        description="⚡ PyQuotex — Fast Quotex trading API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  pyquotex login --demo\n"
            "  pyquotex balance\n"
            "  pyquotex buy --asset EURUSD --amount 5 "
            "--direction call --duration 60\n"
            "  pyquotex candles --asset EURUSD --period 60 --count 10\n"
            "  pyquotex assets\n"
            "  pyquotex history --pages 2\n"
            "  pyquotex monitor --asset EURUSD\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("test-all", help="Run all tests")

    # login
    login_p = sub.add_parser("login", help="Test login and show balance")
    login_p.add_argument(
        "--demo", action="store_true", default=True,
        help="Use demo account (default)"
    )
    login_p.add_argument(
        "--live", action="store_true", help="Use live account"
    )

    # balance
    bal_p = sub.add_parser("balance", help="Show account balance")
    bal_p.add_argument(
        "--demo", action="store_true", default=True,
        help="Use demo account (default)"
    )
    bal_p.add_argument("--live", action="store_true", help="Use live account")

    # buy
    buy_p = sub.add_parser("buy", help="Place a trade")
    buy_p.add_argument(
        "--asset", default="EURUSD", help="Asset symbol (default: EURUSD)"
    )
    buy_p.add_argument(
        "--amount", type=float, default=1.0, help="Trade amount (default: 1.0)"
    )
    buy_p.add_argument("--direction", choices=["call", "put"], default="call",
                       help="Trade direction: call (up) or put (down)")
    buy_p.add_argument(
        "--duration", type=int, default=60,
        help="Duration in seconds (default: 60)"
    )
    buy_p.add_argument(
        "--demo", action="store_true", default=True,
        help="Use demo account (default)"
    )
    buy_p.add_argument(
        "--live", action="store_true", help="Use live account"
    )
    buy_p.add_argument(
        "--check-win", action="store_true",
        help="Wait for the trade to finish and show the result"
    )

    # sell
    sell_p = sub.add_parser("sell", help="Sell/close an open position")
    sell_p.add_argument(
        "--id", dest="trade_id", required=True, help="Trade ID to sell"
    )
    sell_p.add_argument("--demo", action="store_true", default=True)
    sell_p.add_argument("--live", action="store_true")

    # candles
    candles_p = sub.add_parser("candles", help="Fetch candle data")
    candles_p.add_argument(
        "--asset", default="EURUSD", help="Asset symbol (default: EURUSD)"
    )
    candles_p.add_argument(
        "--period", type=int, default=60,
        help="Candle period in seconds (default: 60)"
    )
    candles_p.add_argument(
        "--count", type=int, default=10,
        help="Number of candles (default: 10)"
    )

    # candles-deep
    cd_p = sub.add_parser(
        "candles-deep", help="Fetch deep historical candle data"
    )
    cd_p.add_argument(
        "--asset", default="EURUSD", help="Asset symbol (default: EURUSD)"
    )
    cd_p.add_argument(
        "--seconds", type=int, default=3600,
        help="Amount of history in seconds (default: 3600)"
    )
    cd_p.add_argument(
        "--period", type=int, default=60,
        help="Candle period in seconds (default: 60)"
    )
    cd_p.add_argument(
        "--workers", type=int, default=5,
        help="Number of parallel workers (default: 5)"
    )
    cd_p.add_argument("--output", help="Save results to a CSV file")
    cd_p.add_argument(
        "--demo", action="store_true", default=True, help="Use demo account"
    )
    cd_p.add_argument("--live", action="store_true", help="Use live account")

    # assets
    sub.add_parser("assets", help="List available assets")

    # history
    hist_p = sub.add_parser("history", help="Show trade history")
    hist_p.add_argument(
        "--demo", action="store_true", default=True,
        help="Use demo account (default)"
    )
    hist_p.add_argument(
        "--live", action="store_true", help="Use live account"
    )
    hist_p.add_argument(
        "--pages", type=int, default=1,
        help="Number of history pages (default: 1)"
    )

    # check
    chk_p = sub.add_parser(
        "check", help="Check the result of a specific trade by ID"
    )
    chk_p.add_argument(
        "--id", dest="trade_id", type=str, required=True,
        help="Trade ID to check"
    )
    chk_p.add_argument(
        "--demo", action="store_true", default=True,
        help="Use demo account"
    )
    chk_p.add_argument("--live", action="store_true", help="Use live account")

    # monitor
    mon_p = sub.add_parser("monitor", help="Monitor asset price in real-time")
    mon_p.add_argument(
        "--asset", default="EURUSD", help="Asset symbol (default: EURUSD)"
    )
    mon_p.add_argument(
        "--period", type=int, default=60,
        help="Candle period in seconds (default: 60)"
    )

    # strategy
    strat_p = sub.add_parser(
        "strategy",
        help="Run professional trading strategy (Triple Confirmation)"
    )
    strat_p.add_argument(
        "--asset", default="EURUSD", help="Asset to monitor (default: EURUSD)"
    )
    strat_p.add_argument(
        "--period", type=int, default=60,
        help="Candle period in seconds (default: 60)"
    )
    strat_p.add_argument(
        "--auto-trade", action="store_true",
        help="Automatically place trades on signals (DEMO only)"
    )

    return parser


# ---------------------------------------------------------------------------
# Connection helper with exponential backoff
# ---------------------------------------------------------------------------

async def connect_with_retry(
    client: Quotex,
    is_demo: bool,
    max_attempts: int = 5,
) -> bool:
    """Connect to Quotex with exponential backoff on failure."""
    if await client.check_connect():
        return True
    
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        with Progress(
            SpinnerColumn(),
                TextColumn(
                    f"[cyan]Connecting (attempt {attempt}/{max_attempts})…"
                ),
            transient=True,
            console=console,
        ) as prog:
            global current_progress
            current_progress = prog
            prog.add_task("connect")
            client.account_is_demo = 1 if is_demo else 0
            try:
                check, reason = await client.connect()
            finally:
                current_progress = None

        if check:
            console.print(f"[bold green]✓[/] Connected — {reason}")
            return True

        console.print(
            f"[yellow]⚠ Connection failed:[/] {reason}. "
            f"Retrying in {delay:.0f}s…"
        )
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)

    console.print("[bold red]✗ Could not connect after maximum attempts.[/]")
    return False


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _is_demo(args: argparse.Namespace) -> bool:
    """Resolve --demo / --live flags to is_demo bool."""
    if hasattr(args, "live") and args.live:
        return False
    return True


def _balance_table(profile: Any) -> Table:
    table = Table(
        title="💰 [bold]Account Balance[/]",
        show_header=True,
        header_style="bold bright_white on magenta",
        box=box.ROUNDED,
        border_style="magenta",
        row_styles=["none", "dim"],
        padding=(0, 1)
    )
    table.add_column("Account", style="cyan", no_wrap=True)
    table.add_column("Balance", justify="right", style="bold green")
    table.add_column("Currency", style="bright_white")
    table.add_row(
        "Demo", f"{profile.demo_balance:,.2f}", profile.currency_symbol or ""
    )
    table.add_row(
        "Live", f"{profile.live_balance:,.2f}", profile.currency_symbol or ""
    )
    return table


async def cmd_login(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return
    profile = await client.get_profile()
    console.print(_balance_table(profile))
    console.print(Panel(
        f"[bold blue]Nickname:[/] {profile.nick_name}\n"
        f"[bold blue]Country:[/]  {profile.country_name}\n"
        f"[bold blue]Offset:[/]   {profile.offset}",
        title="👤 [bold]User Profile[/]",
        border_style="bright_blue",
        box=box.ROUNDED,
        padding=(1, 2),
        expand=False
    ))


async def cmd_balance(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return
    profile = await client.get_profile()
    console.print(_balance_table(profile))


async def cmd_buy(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    # Automatically resolve asset name (handles OTC switching and casing)
    asset, asset_info = await client.get_available_asset(
        args.asset, force_open=True
    )
    if not asset_info or not asset_info[0]:
        console.print(
            f"[bold red]✗ Asset {args.asset} not found or closed.[/]"
        )
        return
    amount = args.amount
    direction = args.direction
    duration = args.duration

    console.print(
        f"[cyan]Placing trade:[/] [bold]{direction.upper()}[/] "
        f"[yellow]{asset}[/] | amount=[bold]{amount}[/] | "
        f"duration=[bold]{duration}s[/]"
    )

    with Progress(
            SpinnerColumn(), TextColumn("[cyan]Sending order…"),
            transient=True, console=console
    ) as prog:
        prog.add_task("buy")
        status, trade_data = await client.buy(
            amount, asset, direction, duration
        )

    if status:
        order_data = trade_data if isinstance(trade_data, dict) else {}
        trade_id = order_data.get("id")
        close_timestamp = order_data.get("closeTimestamp")

        console.print(
            f"[bold green]✓ Order placed![/] Trade ID: [bold]{trade_id}[/]"
        )

        if getattr(args, "check_win", False):
            with Progress(
                    SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                    transient=True, console=console
            ) as prog:
                task_id = prog.add_task("Waiting for trade closure...")

                # Add a small buffer to time out to account for network latency
                check_task = asyncio.create_task(
                    client.check_win(trade_id, duration)
                )

                while not check_task.done():
                    # Calculate real remaining time using server clock
                    server_now = (
                        client.api.timesync.server_timestamp
                        if client.api else None
                    )
                    if close_timestamp and server_now:
                        remaining = int(close_timestamp - server_now)
                    else:
                        remaining = 0  # If no server time, just show finishing

                    if remaining > 0:
                        prog.update(
                            task_id,
                            description=(
                                f"Waiting for trade closure... "
                                f"[bold yellow]{remaining}s[/] remaining"
                            )
                        )
                    else:
                        prog.update(
                            task_id,
                            description=(
                                "Waiting for trade closure... "
                                "[bold yellow]finishing up[/]"
                            )
                        )

                    try:
                        await asyncio.wait_for(
                            asyncio.shield(check_task), timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        pass

                win, profit = await check_task

            win_bool = win == "win"
            result_color = "green" if win_bool else "red"
            result_label = "WIN 🎉" if win_bool else "LOSS 💸"
            console.print(
                f"[bold {result_color}]{result_label}[/] — "
                f"Profit: [bold]{profit:+.2f}[/]"
            )
        else:
            console.print(
                "[dim]Order successfully dispatched. "
                "Exiting immediately as --check-win was not passed.[/]"
            )
    else:
        # Fixed UnboundLocalError by using trade_data
        console.print(f"[bold red]✗ Order failed.[/] Response: {trade_data}")
        if not getattr(args, "check_win", False):
            sys.exit(1)


async def cmd_check(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    trade_id = args.trade_id
    console.print(
        f"[cyan]Checking result for Trade ID:[/] [bold]{trade_id}[/]"
    )

    with Progress(
            SpinnerColumn(), TextColumn("[cyan]{task.description}"),
            transient=True, console=console
    ) as prog:
        task_id = prog.add_task("Waiting for trade closure...")
        try:
            # Increase default timeout for manual check
            check_task = asyncio.create_task(
                client.check_win(trade_id, timeout=300)
            )
            elapsed = 0

            while not check_task.done():
                prog.update(
                    task_id,
                    description=(
                        f"Waiting for trade closure... "
                        f"[bold yellow]{elapsed}s[/] elapsed"
                    )
                )

                try:
                    await asyncio.wait_for(
                        asyncio.shield(check_task), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    elapsed += 1

            win, profit = await check_task
            win_bool = win == "win"
            result_color = "green" if win_bool else "red"
            result_label = "WIN 🎉" if win_bool else "LOSS 💸"
            console.print(
                f"[bold {result_color}]{result_label}[/] — "
                f"Profit/Loss: [bold]{profit:+.2f}[/]"
            )
        except Exception as e:
            console.print(
                f"[bold red]✗ Could not check trade result.[/] Error: {e}"
            )


async def cmd_sell(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    trade_id = args.trade_id
    console.print(f"[cyan]Selling trade ID:[/] {trade_id}")
    with Progress(
            SpinnerColumn(), TextColumn("[cyan]Sending sell order…"),
            transient=True, console=console
    ) as prog:
        prog.add_task("sell")
        result = await client.sell_option(trade_id)
    console.print(f"[bold green]Sell result:[/] {result}")


def _ascii_sparkline(values: list[float]) -> str:
    """Render a compact ASCII sparkline for a list of floats."""
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    span = mx - mn or 1
    return "".join(
        bars[int((v - mn) / span * (len(bars) - 1))] for v in values
    )


async def cmd_candles(client: Quotex, args: argparse.Namespace) -> None:
    ok = await connect_with_retry(client, is_demo=True)
    if not ok:
        return

    # Automatically resolve asset name (handles OTC switching and casing)
    asset, asset_info = await client.get_available_asset(
        args.asset, force_open=True
    )
    if not asset_info or not asset_info[0]:
        console.print(
            f"[bold red]✗ Asset {args.asset} not found or closed.[/]"
        )
        return
    period = args.period
    count = args.count
    end_time = time.time()

    with Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]Fetching {count} candles for {asset}…"),
            transient=True, console=console
    ) as prog:
        prog.add_task("candles")
        candles = await client.get_candles(asset, end_time, count, period)

    if not candles:
        console.print("[yellow]No candle data returned.[/]")
        return

    table = Table(
        title=f"🕯 [bold]Candles — {asset}[/] (period={period}s)",
        header_style="bold bright_white on cyan",
        box=box.MINIMAL_DOUBLE_HEAD,
        border_style="cyan",
        row_styles=["none", "dim"]
    )
    table.add_column("Time", style="dim")
    table.add_column("Open", justify="right", style="bright_white")
    table.add_column("High", justify="right", style="green")
    table.add_column("Low", justify="right", style="red")
    table.add_column("Close", justify="right", style="bright_white")
    table.add_column("Trend", justify="center")

    closes = []
    for c in candles[-count:]:
        ts = time.strftime("%H:%M:%S", time.localtime(c.get("time", 0)))
        o, h, l, cl = (
            c.get("open", 0), c.get("high", 0),
            c.get("low", 0), c.get("close", 0)
        )
        direction = "[green]▲[/]" if cl >= o else "[red]▼[/]"
        table.add_row(
            ts, f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{cl:.5f}", direction
        )
        closes.append(cl)

    console.print(table)
    if closes:
        console.print(f"  Trend: [bold]{_ascii_sparkline(closes)}[/]")


async def cmd_assets(client: Quotex, args: argparse.Namespace) -> None:
    ok = await connect_with_retry(client, is_demo=True)
    if not ok:
        return

    with Progress(
            SpinnerColumn(), TextColumn("[cyan]Fetching asset list…"),
            transient=True, console=console
    ) as prog:
        prog.add_task("assets")
        raw_data = await client.get_instruments(timeout=60)

    if not raw_data:
        console.print("[yellow]No instruments data available.[/]")
        return

    # Standardize data format: Quotex may return a dict with "list",
    # "instruments", or just the list
    if isinstance(raw_data, dict):
        items = raw_data.get("list", raw_data.get("instruments", []))
    elif isinstance(raw_data, list):
        items = raw_data
    else:
        items = []

    if not items and isinstance(raw_data, dict):
        # Fallback: if it's a dict and we didn't find 'list', maybe it
        # IS the list of assets?
        # Check if keys look like symbols
        items = list(raw_data.values()) if any(
            isinstance(v, (dict, list)) for v in raw_data.values()
        ) else []

    table = Table(
        title="📊 [bold]Available Assets[/]",
        header_style="bold bright_white on magenta",
        box=box.ROUNDED,
        border_style="magenta",
        row_styles=["none", "dim"]
    )
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Name", style="bright_white")
    table.add_column("Payout", justify="right", style="bold green")
    table.add_column("Status", justify="center")

    for item in items[:50]:
        try:
            if isinstance(item, dict):
                symbol = item.get("symbol", item.get("asset", ""))
                name = item.get("name", "")
                payout = f"{item.get('payout', item.get('profit', 0))}%"
                status = (
                    "[green]●[/]" if item.get("enabled", True) else "[red]○[/]"
                )
            elif isinstance(item, list) and len(item) >= 6:
                symbol = item[1]
                name = item[2]
                payout = f"{item[5]}%"
                status = "[green]●[/]"
            else:
                continue
            table.add_row(str(symbol), str(name), str(payout), status)
        except Exception:
            continue

    console.print(table)
    if len(items) > 50:
        console.print(f"[dim]… and {len(items) - 50} more assets[/]")


async def cmd_history(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    account_type = 1 if is_demo else 0
    all_deals = []

    with Progress(
            SpinnerColumn(), TextColumn("[cyan]Fetching history…"),
            transient=True, console=console
    ) as prog:
        prog.add_task("history")
        for page in range(1, args.pages + 1):
            data = await client.get_trader_history(account_type, page)
            deals = data.get("deals", []) if isinstance(data, dict) else []
            all_deals.extend(deals)

    if not all_deals:
        console.print("[yellow]No trade history found.[/]")
        return

    table = Table(
        title="📜 [bold]Trade History[/]",
        header_style="bold bright_white on blue",
        box=box.ROUNDED,
        border_style="blue",
        row_styles=["none", "dim"]
    )
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Asset", style="bold cyan")
    table.add_column("Dir", justify="center")
    table.add_column("Amount", justify="right", style="bright_white")
    table.add_column("Profit", justify="right")
    table.add_column("Result", justify="center")
    table.add_column("Time", style="dim")

    for deal in all_deals[:100]:
        d_id = str(deal.get("id", ""))[:8]
        asset = deal.get("asset", "")
        direction = deal.get("command", "")
        amount = f"{deal.get('amount', 0):.2f}"
        profit = deal.get("profit", 0)
        profit_str = (
            f"[green]+{profit:.2f}[/]" if profit > 0
            else f"[red]{profit:.2f}[/]"
        )
        result = "[green]WIN[/]" if profit > 0 else "[red]LOSS[/]"
        ts = time.strftime(
            "%m-%d %H:%M", time.localtime(deal.get("openTimestamp", 0))
        )
        table.add_row(d_id, asset, direction, amount, profit_str, result, ts)

    console.print(table)
    wins = sum(1 for d in all_deals if d.get("profit", 0) > 0)
    losses = len(all_deals) - wins
    total_profit = sum(d.get("profit", 0) for d in all_deals)
    pct = wins / len(all_deals) * 100 if all_deals else 0
    console.print(
        f"\n[bold]Total:[/] {len(all_deals)} trades | "
        f"[green]Wins: {wins}[/] | [red]Losses: {losses}[/] | "
        f"Win rate: [bold]{pct:.1f}%[/] | "
        f"Net P&L: [bold]{'[green]' if total_profit >= 0 else '[red]'}"
        f"{total_profit:+.2f}[/]"
    )


async def cmd_monitor(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = True
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    # Automatically resolve asset name (handles OTC switching and casing)
    asset, asset_info = await client.get_available_asset(
        args.asset, force_open=True
    )
    if not asset_info or not asset_info[0]:
        console.print(
            f"[bold red]✗ Asset {args.asset} not found or closed.[/]"
        )
        return
    period = args.period
    console.print(Panel(
        f"Monitoring [bold cyan]{asset}[/] | period=[bold]{period}s[/]\n"
        "Press Ctrl+C to stop.",
        title="📡 Live Monitor",
        border_style="cyan",
    ))

    await client.start_realtime_candle(asset, period)
    prices: list[float] = []
    try:
        while True:
            candle = await client.get_realtime_candles(asset)
            if candle:
                price = (
                    candle[2]
                    if isinstance(candle, list) and len(candle) >= 3
                    else None
                )
                if price:
                    prices.append(float(price))
                    trend = _ascii_sparkline(prices[-20:])
                    direction = (
                        "▲" if len(prices) < 2 or prices[-1] >= prices[-2]
                        else "▼"
                    )
                    color = "green" if direction == "▲" else "red"
                    console.print(
                        f"\r[{color}]{direction}[/{color}] [bold]{asset}[/] "
                        f"Price: [bold cyan]{price:.5f}[/]  "
                        f"[{color}]{trend}[/{color}]",
                        end="",
                    )
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Monitoring stopped.[/]")


async def cmd_candles_deep(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = not args.live
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    asset, asset_info = await client.get_available_asset(
        args.asset, force_open=True
    )
    if not asset_info or not asset_info[0]:
        console.print(
            f"[bold red]✗ Asset {args.asset} not found or closed.[/]"
        )
        return

    console.print(
        f"[bold blue]📊 Fetching deep history for {asset}...[/bold blue]"
    )

    with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
    ) as progress:
        # Map worker names to Rich task IDs
        worker_tasks = {}
        total_candles = 0

        def update_progress(completed: int, total: int, count: int, worker: str) -> None:
            nonlocal total_candles
            if worker not in worker_tasks:
                worker_tasks[worker] = progress.add_task(
                    f"[cyan]{worker}",
                    total=100
                )

            pct = (completed / total) * 100 if total > 0 else 100
            progress.update(
                worker_tasks[worker],
                completed=pct,
                description=f"[cyan]{worker}[/] | Candles: [bold green]{count}[/] | {pct:.1f}%"
            )

        candles = await client.get_historical_candles(
            asset,
            args.seconds,
            args.period,
            max_workers=getattr(args, 'workers', 5),
            progress_callback=update_progress
        )

    if candles:
        console.print(
            f"[bold green]✅ Successfully fetched {len(candles)} "
            "candles![/bold green]"
        )
        console.print(
            f"📅 From: [yellow]"
            f"{datetime.fromtimestamp(candles[0]['time'])}[/yellow]"
        )
        console.print(
            f"📅 To:   [yellow]"
            f"{datetime.fromtimestamp(candles[-1]['time'])}[/yellow]"
        )

        if args.output:
            import csv
            with open(args.output, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=candles[0].keys())
                writer.writeheader()
                writer.writerows(candles)
            console.print(f"[bold cyan]📁 Saved to {args.output}[/bold cyan]")
    else:
        console.print(
            "[bold red]❌ No history found or fetch failed.[/bold red]"
        )


async def cmd_strategy(client: Quotex, args: argparse.Namespace) -> None:
    is_demo = True
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    # Automatically resolve asset name (handles OTC switching and casing)
    asset, asset_info = await client.get_available_asset(
        args.asset, force_open=True
    )
    if not asset_info or not asset_info[0]:
        console.print(
            f"[bold red]✗ Asset {args.asset} not found or closed.[/]"
        )
        return
    period = args.period
    strategy = TripleConfirmationStrategy()

    mode_text = (
        '[bold green]AUTO-TRADE (DEMO)[/]' if args.auto_trade
        else '[bold yellow]MONITOR ONLY[/]'
    )
    console.print(Panel(
        f"Strategy: [bold magenta]Triple Confirmation[/] | "
        f"Asset: [bold cyan]{asset}[/] | Period: [bold]{period}s[/]\n"
        f"Mode: {mode_text}\n"
        "Searching for high-probability entries...",
        title="🤖 Trading Bot",
        border_style="magenta",
    ))

    try:
        while True:
            # Fetch last candles
            candles = await client.get_candles(asset, time.time(), 60, period)
            if candles:
                signal = strategy.analyze(candles)

                # Render status line
                ts = time.strftime("%H:%M:%S")
                last_price = candles[-1]['close']
                status_color = (
                    "green" if signal == "call" else "red"
                    if signal == "put" else "dim"
                )
                signal_text = (
                    f"[{status_color}]SIGNAL: {str(signal).upper()}[/]"
                    if signal else "[dim]SIGNAL: NONE[/]"
                )

                console.print(
                    f"\r[dim]{ts}[/] | Price: [bold]{last_price:.5f}[/] | "
                    f"{signal_text}",
                    end="",
                )

                if signal:
                    console.print(
                        f"\n[bold {status_color}]🎯 ENTRY DETECTED:[/] "
                        f"[white]{signal.upper()}[/] on {asset}"
                    )
                    if args.auto_trade:
                        console.print(
                            f"[yellow]⚡ Placing {signal.upper()} trade...[/]"
                        )
                        status, trade_data = await client.buy(
                            10, asset, signal, period
                        )
                        if status:
                            # Extract ID correctly from dictionary response
                            trade_id = (
                                trade_data.get("id")
                                if isinstance(trade_data, dict) else trade_data
                            )
                            console.print(
                                f"[bold green]✓ Trade Placed![/] "
                                f"ID: {trade_id}"
                            )
                        else:
                            console.print(
                                f"[bold red]✗ Trade Failed:[/] {trade_data}"
                            )

            await asyncio.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[dim]Strategy stopped.[/]")


async def cmd_test_all(client: Quotex, args: argparse.Namespace) -> None:
    """Run a suite of tests to verify all functionality."""
    console.print(Panel(
        "[bold yellow]🚀 Starting Full Test Suite[/]",
        border_style="yellow", expand=False
    ))

    # 1. Login & Balance
    console.print("\n[bold cyan]1. Testing Login & Balance...[/]")
    await cmd_login(client, args)

    # 2. Assets
    console.print("\n[bold cyan]2. Testing Assets List...[/]")
    await cmd_assets(client, args)

    # 3. Candles
    console.print("\n[bold cyan]3. Testing Candles Fetching...[/]")
    args.asset = "EURUSD"
    args.period = 60
    args.count = 5
    await cmd_candles(client, args)

    # 4. History
    console.print("\n[bold cyan]4. Testing Trade History...[/]")
    args.pages = 1
    await cmd_history(client, args)

    console.print(Panel(
        "\n[bold green]✅ Test Suite Completed![/]",
        border_style="green", expand=False
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = make_parser()
    args = parser.parse_args()

    if not args.command:
        console.print(Panel(
            "[bold cyan]⚡ PyQuotex[/] — Fast Quotex trading API\n\n"
            "Run [bold]pyquotex --help[/] for available commands.",
            border_style="cyan",
        ))
        parser.print_help()
        return

    email, password = credentials()
    if not email or not password:
        console.print(
            "[bold red]✗ No credentials found.[/] "
            "Please configure pyquotex/config.py"
        )
        sys.exit(1)

    client = Quotex(
        email=email,
        password=password,
        on_otp_callback=on_otp,
    )

    try:
        command_map: dict[str, Callable[[Quotex, argparse.Namespace], Any]] = {
            "login": cmd_login,
            "balance": cmd_balance,
            "buy": cmd_buy,
            "check": cmd_check,
            "sell": cmd_sell,
            "candles": cmd_candles,
            "assets": cmd_assets,
            "history": cmd_history,
            "monitor": cmd_monitor,
            "strategy": cmd_strategy,
            "candles-deep": cmd_candles_deep,
            "test-all": cmd_test_all,
        }
        handler = command_map.get(args.command)
        if handler:
            await handler(client, args)
        else:
            console.print(f"[red]Unknown command: {args.command}[/]")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
