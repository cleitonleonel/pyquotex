#!/usr/bin/env python3
"""PyQuotex CLI — Complete Quotex trading API client.

Every public method exposed by ``stable_api.Quotex`` is reachable from this
CLI.  Use ``python app.py --help`` or ``python app.py <command> --help`` for
full usage.

Commands
--------
Connection & Auth
    login           Connect and show profile + balance
    balance         Show current balance

Account Management
    set-demo-balance    Refill / set demo (practice) balance
    server-time         Show synced server timestamp
    settings            Apply and retrieve trading-UI settings

Assets & Payouts
    assets          List all available assets with open/closed status
    payout          Show payout % for all assets
    payout-asset    Show payout % for a single asset

Candle / Market Data
    candles             Fetch latest candles (up to 199 per request)
    candles-v2          Fetch candles via the v2 API path
    candles-deep        Fetch deep historical data (parallel workers)
    history-line        Fetch raw historical price-line data
    candle-info         Opening / closing / remaining time of current candle
    realtime-price      Live price stream for an asset
    realtime-sentiment  Live trader-sentiment stream
    realtime-candle     Live candle tick stream

Trading
    buy             Place an immediate binary option trade
    sell            Sell / close an open position early
    pending         Place a pending order (executed at a future time)
    check           Check win/loss result of a trade by ID
    result          Look up a trade result from history by operation ID
    signals         Fetch current signal data from the signals stream

History
    history         Show recent trade history (paged)

Indicators
    indicator       Calculate a technical indicator (RSI, MACD, BB, …)
    subscribe-indicator  Live indicator stream with callback

Monitoring
    monitor         Real-time candle price monitor
    strategy        Run Triple-Confirmation strategy (demo only)
"""
import argparse
import asyncio
import csv
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


# ---------------------------------------------------------------------------
# OTP callback
# ---------------------------------------------------------------------------

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
        description="⚡ PyQuotex — Complete Quotex trading API CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  pyquotex login --demo\n"
            "  pyquotex balance --live\n"
            "  pyquotex assets\n"
            "  pyquotex payout\n"
            "  pyquotex payout-asset --asset EURUSD --timeframe 1\n"
            "  pyquotex candles --asset EURUSD --period 60 --count 10\n"
            "  pyquotex candles-v2 --asset EURUSD --period 60\n"
            "  pyquotex candles-deep --asset EURUSD --seconds 3600 --workers 5\n"
            "  pyquotex history-line --asset EURUSD --offset 3600\n"
            "  pyquotex candle-info --asset EURUSD --period 60\n"
            "  pyquotex realtime-price --asset EURUSD\n"
            "  pyquotex realtime-sentiment --asset EURUSD\n"
            "  pyquotex realtime-candle --asset EURUSD --period 60\n"
            "  pyquotex buy --asset EURUSD --amount 5 --direction call --duration 60 --check-win\n"
            "  pyquotex sell --id TRADE_ID\n"
            "  pyquotex pending --asset EURUSD --amount 10 --direction call --duration 60\n"
            "  pyquotex check --id TRADE_ID\n"
            "  pyquotex result --id OPERATION_ID\n"
            "  pyquotex history --pages 2\n"
            "  pyquotex signals\n"
            "  pyquotex indicator --asset EURUSD --name RSI --period 14\n"
            "  pyquotex server-time\n"
            "  pyquotex set-demo-balance --amount 10000\n"
            "  pyquotex settings --asset EURUSD --period 60\n"
            "  pyquotex monitor --asset EURUSD\n"
            "  pyquotex strategy --asset EURUSD --auto-trade\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── helpers ─────────────────────────────────────────────────────────────
    def _add_account_flags(p: argparse.ArgumentParser) -> None:
        g = p.add_mutually_exclusive_group()
        g.add_argument("--demo", action="store_true", default=True,
                       help="Use demo account (default)")
        g.add_argument("--live", action="store_true",
                       help="Use live account")

    def _add_asset_flag(p: argparse.ArgumentParser,
                        default: str = "EURUSD") -> None:
        p.add_argument("--asset", default=default,
                       help=f"Asset symbol (default: {default})")

    # ── test-all ─────────────────────────────────────────────────────────────
    sub.add_parser("test-all", help="Run all tests")

    # ── login ────────────────────────────────────────────────────────────────
    p = sub.add_parser("login", help="Test connection and show profile + balance")
    _add_account_flags(p)

    # ── balance ──────────────────────────────────────────────────────────────
    p = sub.add_parser("balance", help="Show account balance")
    _add_account_flags(p)

    # ── server-time ──────────────────────────────────────────────────────────
    sub.add_parser("server-time",
                   help="Show the current synced server timestamp")

    # ── set-demo-balance ─────────────────────────────────────────────────────
    p = sub.add_parser("set-demo-balance",
                       help="Refill or set demo (practice) account balance")
    p.add_argument("--amount", type=float, default=10000.0,
                   help="Amount to set (default: 10000)")

    # ── settings ─────────────────────────────────────────────────────────────
    p = sub.add_parser("settings",
                       help="Apply trading-UI settings and show result")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    p.add_argument("--mode", choices=["TIMER", "TURBO"], default="TIMER",
                   help="Time mode (default: TIMER)")
    p.add_argument("--deal", type=int, default=5,
                   help="Default deal amount (default: 5)")
    _add_account_flags(p)

    # ── assets ───────────────────────────────────────────────────────────────
    sub.add_parser("assets", help="List all available assets")

    # ── payout ───────────────────────────────────────────────────────────────
    sub.add_parser("payout", help="Show payout % for all assets")

    # ── payout-asset ─────────────────────────────────────────────────────────
    p = sub.add_parser("payout-asset",
                       help="Show payout % for a specific asset")
    _add_asset_flag(p)
    p.add_argument("--timeframe", default="1",
                   choices=["1", "5", "24", "all"],
                   help="Timeframe in minutes, or 'all' (default: 1)")

    # ── candles ──────────────────────────────────────────────────────────────
    p = sub.add_parser("candles", help="Fetch latest candle data (≤199)")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    p.add_argument("--count", type=int, default=10,
                   help="Number of candles to display (default: 10)")
    _add_account_flags(p)

    # ── candles-v2 ───────────────────────────────────────────────────────────
    p = sub.add_parser("candles-v2",
                       help="Fetch candles via the v2 API path")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── candles-deep ─────────────────────────────────────────────────────────
    p = sub.add_parser("candles-deep",
                       help="Fetch deep historical candle data (parallel workers)")
    _add_asset_flag(p)
    p.add_argument("--seconds", type=int, default=3600,
                   help="Total history window in seconds (default: 3600)")
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    p.add_argument("--workers", type=int, default=5,
                   help="Parallel workers 2-10 (default: 5). "
                        "WARNING: >10 may cause a ban.")
    p.add_argument("--output", metavar="FILE",
                   help="Save results to a CSV file")
    _add_account_flags(p)

    # ── history-line ─────────────────────────────────────────────────────────
    p = sub.add_parser("history-line",
                       help="Fetch raw historical price-line data")
    _add_asset_flag(p)
    p.add_argument("--offset", type=int, default=3600,
                   help="History window in seconds (default: 3600)")
    _add_account_flags(p)

    # ── candle-info ──────────────────────────────────────────────────────────
    p = sub.add_parser("candle-info",
                       help="Show opening / closing / remaining time of current candle")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── realtime-price ───────────────────────────────────────────────────────
    p = sub.add_parser("realtime-price",
                       help="Stream live price data for an asset")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── realtime-sentiment ───────────────────────────────────────────────────
    p = sub.add_parser("realtime-sentiment",
                       help="Stream live trader-sentiment data")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── realtime-candle ──────────────────────────────────────────────────────
    p = sub.add_parser("realtime-candle",
                       help="Stream live processed candle ticks")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── buy ──────────────────────────────────────────────────────────────────
    p = sub.add_parser("buy", help="Place an immediate binary option trade")
    _add_asset_flag(p)
    p.add_argument("--amount", type=float, default=1.0,
                   help="Trade amount (default: 1.0)")
    p.add_argument("--direction", choices=["call", "put"], default="call",
                   help="call = UP, put = DOWN (default: call)")
    p.add_argument("--duration", type=int, default=60,
                   help="Duration in seconds (default: 60)")
    p.add_argument("--check-win", action="store_true",
                   help="Wait for the trade to settle and show win/loss")
    _add_account_flags(p)

    # ── sell ─────────────────────────────────────────────────────────────────
    p = sub.add_parser("sell", help="Sell / close an open position early")
    p.add_argument("--id", dest="trade_id", required=True,
                   help="Trade ID to sell")
    _add_account_flags(p)

    # ── pending ──────────────────────────────────────────────────────────────
    p = sub.add_parser("pending",
                       help="Place a pending order (executed at a future time)")
    _add_asset_flag(p)
    p.add_argument("--amount", type=float, default=1.0,
                   help="Trade amount (default: 1.0)")
    p.add_argument("--direction", choices=["call", "put"], default="call",
                   help="call = UP, put = DOWN (default: call)")
    p.add_argument("--duration", type=int, default=60,
                   help="Duration in seconds (default: 60)")
    p.add_argument("--open-time", dest="open_time", default=None,
                   help="Exact open time HH:MM (optional, defaults to next candle)")
    _add_account_flags(p)

    # ── check ────────────────────────────────────────────────────────────────
    p = sub.add_parser("check",
                       help="Check win/loss result of a trade by ID")
    p.add_argument("--id", dest="trade_id", required=True,
                   help="Trade ID to check")
    _add_account_flags(p)

    # ── result ───────────────────────────────────────────────────────────────
    p = sub.add_parser("result",
                       help="Look up trade result from history by operation ID")
    p.add_argument("--id", dest="operation_id", required=True,
                   help="Operation ID to look up")
    _add_account_flags(p)

    # ── history ──────────────────────────────────────────────────────────────
    p = sub.add_parser("history", help="Show recent trade history (paged)")
    p.add_argument("--pages", type=int, default=1,
                   help="Number of history pages (default: 1)")
    _add_account_flags(p)

    # ── signals ──────────────────────────────────────────────────────────────
    sub.add_parser("signals",
                   help="Fetch current signal data from the signals stream")

    # ── indicator ────────────────────────────────────────────────────────────
    p = sub.add_parser("indicator",
                       help="Calculate a technical indicator (RSI, MACD, BB, …)")
    _add_asset_flag(p)
    p.add_argument("--name",
                   choices=["RSI", "MACD", "BOLLINGER",
                            "STOCHASTIC", "ADX", "ATR", "SMA", "EMA", "ICHIMOKU"],
                   default="RSI",
                   help="Indicator name (default: RSI)")
    p.add_argument("--period", type=int, default=14,
                   help="Indicator period (default: 14)")
    p.add_argument("--timeframe", type=int, default=60,
                   help="Candle timeframe in seconds (default: 60)")
    _add_account_flags(p)

    # ── monitor ──────────────────────────────────────────────────────────────
    p = sub.add_parser("monitor",
                       help="Real-time price monitor for an asset")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")

    # ── strategy ─────────────────────────────────────────────────────────────
    p = sub.add_parser("strategy",
                       help="Run Triple-Confirmation strategy (DEMO recommended)")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60,
                   help="Candle period in seconds (default: 60)")
    p.add_argument("--auto-trade", action="store_true",
                   help="Automatically place trades on signals (DEMO only)")

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
# Shared helpers
# ---------------------------------------------------------------------------

def _is_demo(args: argparse.Namespace) -> bool:
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
        padding=(0, 1),
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


# ---------------------------------------------------------------------------
# Command implementations — Connection & Account
# ---------------------------------------------------------------------------

async def cmd_login(client: Quotex, args: argparse.Namespace) -> None:
    """Connect and display user profile + balance."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
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
        expand=False,
    ))


async def cmd_balance(client: Quotex, args: argparse.Namespace) -> None:
    """Display current balance."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    profile = await client.get_profile()
    console.print(_balance_table(profile))


async def cmd_server_time(client: Quotex, args: argparse.Namespace) -> None:
    """Show the current synced server timestamp."""
    if not await connect_with_retry(client, True):
        return
    ts = await client.get_server_time()
    dt = datetime.fromtimestamp(ts)
    console.print(Panel(
        f"[bold cyan]Unix:[/]   {ts}\n"
        f"[bold cyan]Local:[/]  {dt.strftime('%Y-%m-%d %H:%M:%S')}",
        title="🕒 [bold]Server Time[/]",
        border_style="cyan",
        box=box.ROUNDED,
        expand=False,
    ))


async def cmd_set_demo_balance(
        client: Quotex, args: argparse.Namespace
) -> None:
    """Refill or set the demo (practice) account balance."""
    if not await connect_with_retry(client, True):
        return
    result = await client.edit_practice_balance(args.amount)
    console.print(Panel(
        f"[bold green]✓ Demo balance updated[/]\n{result}",
        title="💸 [bold]Set Demo Balance[/]",
        border_style="green",
        box=box.ROUNDED,
        expand=False,
    ))


async def cmd_settings(client: Quotex, args: argparse.Namespace) -> None:
    """Apply trading-UI settings and display the server response."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    result = await client.store_settings_apply(
        asset=args.asset,
        period=args.period,
        time_mode=args.mode,
        deal=args.deal,
    )
    table = Table(
        title="⚙️  [bold]Settings Applied[/]",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Key", style="bright_white")
    table.add_column("Value", style="yellow")
    for k, v in result.items():
        table.add_row(str(k), str(v))
    console.print(table)


# ---------------------------------------------------------------------------
# Command implementations — Assets & Payouts
# ---------------------------------------------------------------------------

async def cmd_assets(client: Quotex, args: argparse.Namespace) -> None:
    """List all available assets with open/closed status."""
    if not await connect_with_retry(client, True):
        return
    await client.get_all_assets()
    instruments = await client.get_instruments()
    if not instruments:
        console.print("[red]No instruments received.[/]")
        return

    table = Table(
        title="📊 [bold]Available Assets[/]",
        box=box.ROUNDED,
        border_style="bright_blue",
        show_header=True,
        header_style="bold bright_white on blue",
        row_styles=["none", "dim"],
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Asset", style="cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Payout %", justify="right", style="green")

    for idx, i in enumerate(instruments, 1):
        status = "[green]OPEN[/]" if i[14] else "[red]CLOSED[/]"
        payout = f"{i[5]}%" if len(i) > 5 else "—"
        table.add_row(str(idx), i[1], i[2].replace("\n", ""), status, payout)

    console.print(table)


async def cmd_payout(client: Quotex, args: argparse.Namespace) -> None:
    """Show payout % for all assets."""
    if not await connect_with_retry(client, True):
        return
    await client.get_all_assets()
    data = client.get_payment()
    if not data:
        console.print("[red]No payout data available.[/]")
        return

    table = Table(
        title="💹 [bold]Asset Payouts[/]",
        box=box.ROUNDED,
        border_style="green",
        show_header=True,
        header_style="bold bright_white on green",
        row_styles=["none", "dim"],
    )
    table.add_column("Asset", style="cyan", no_wrap=True)
    table.add_column("Payout %", justify="right")
    table.add_column("Turbo %", justify="right")
    table.add_column("1M %", justify="right")
    table.add_column("5M %", justify="right")
    table.add_column("Open", justify="center")

    for asset, info in data.items():
        status = "[green]✓[/]" if info.get("open") else "[red]✗[/]"
        table.add_row(
            asset,
            str(info.get("payment", "—")),
            str(info.get("turbo_payment", "—")),
            str(info.get("profit", {}).get("1M", "—")),
            str(info.get("profit", {}).get("5M", "—")),
            status,
        )
    console.print(table)


async def cmd_payout_asset(client: Quotex, args: argparse.Namespace) -> None:
    """Show payout % for a specific asset."""
    if not await connect_with_retry(client, True):
        return
    await client.get_all_assets()
    result = client.get_payout_by_asset(args.asset, args.timeframe)
    if result is None:
        console.print(f"[red]Asset '{args.asset}' not found.[/]")
        return
    console.print(Panel(
        f"[bold cyan]Asset:[/]     {args.asset}\n"
        f"[bold cyan]Timeframe:[/] {args.timeframe}M\n"
        f"[bold green]Payout:[/]    {result}%",
        title="💹 [bold]Asset Payout[/]",
        border_style="green",
        box=box.ROUNDED,
        expand=False,
    ))


# ---------------------------------------------------------------------------
# Command implementations — Candle / Market Data
# ---------------------------------------------------------------------------

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


async def cmd_realtime_price(client: Quotex, args: argparse.Namespace) -> None:
    """Stream live price data for an asset (Ctrl+C to stop)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(
        f"[cyan]Streaming live price for[/] [bold]{asset}[/] "
        f"[dim](Ctrl+C to stop)[/]"
    )
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


async def cmd_realtime_sentiment(
        client: Quotex, args: argparse.Namespace
) -> None:
    """Stream live trader-sentiment data (Ctrl+C to stop)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(
        f"[cyan]Streaming sentiment for[/] [bold]{asset}[/] "
        f"[dim](Ctrl+C to stop)[/]"
    )
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


async def cmd_realtime_candle(
        client: Quotex, args: argparse.Namespace
) -> None:
    """Stream live processed candle ticks (Ctrl+C to stop)."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(
        f"[cyan]Streaming candle ticks for[/] [bold]{asset}[/] "
        f"[dim](Ctrl+C to stop)[/]"
    )
    try:
        while True:
            candle = await client.start_realtime_candle(asset, args.period)
            if candle:
                console.print(
                    f"  [dim]{datetime.now().strftime('%H:%M:%S')}[/]  "
                    f"{candle}",
                    end="\r",
                )
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stream stopped.[/]")
    finally:
        await client.stop_candles_stream(asset)


# ---------------------------------------------------------------------------
# Command implementations — Trading
# ---------------------------------------------------------------------------

async def cmd_buy(client: Quotex, args: argparse.Namespace) -> None:
    """Place an immediate binary option trade."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
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
        f"[cyan]Placing trade:[/] [bold]{args.direction.upper()}[/] "
        f"[yellow]{asset}[/] | amount=[bold]{args.amount}[/] | "
        f"duration=[bold]{args.duration}s[/]"
    )

    with Progress(
        SpinnerColumn(), TextColumn("[cyan]Sending order…"),
        transient=True, console=console
    ) as prog:
        prog.add_task("buy")
        status, trade_data = await client.buy(
            args.amount, asset, args.direction, args.duration
        )

    if status:
        order_data = trade_data if isinstance(trade_data, dict) else {}
        trade_id = order_data.get("id")
        close_ts = order_data.get("closeTimestamp")
        console.print(
            f"[bold green]✓ Order placed![/] Trade ID: [bold]{trade_id}[/]"
        )

        if getattr(args, "check_win", False):
            with Progress(
                SpinnerColumn(),
                TextColumn("[cyan]{task.description}"),
                transient=True,
                console=console,
            ) as prog:
                task_id = prog.add_task("Waiting for trade closure...")
                check_task = asyncio.create_task(
                    client.check_win(trade_id, args.duration)
                )
                while not check_task.done():
                    server_now = (
                        client.api.timesync.server_timestamp
                        if client.api else None
                    )
                    remaining = (
                        int(close_ts - server_now)
                        if close_ts and server_now else 0
                    )
                    label = (
                        f"Waiting… [bold yellow]{remaining}s[/] remaining"
                        if remaining > 0
                        else "Waiting… [bold yellow]finishing[/]"
                    )
                    prog.update(task_id, description=label)
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(check_task), timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        pass

            win, profit = await check_task
            color = "green" if win == "win" else "red"
            label = "WIN 🎉" if win == "win" else "LOSS 💸"
            console.print(
                f"[bold {color}]{label}[/] — Profit: [bold]{profit:+.2f}[/]"
            )
        else:
            console.print(
                "[dim]Order dispatched. Pass --check-win to wait for result.[/]"
            )
    else:
        console.print(f"[bold red]✗ Order failed.[/] Response: {trade_data}")
        sys.exit(1)


async def cmd_sell(client: Quotex, args: argparse.Namespace) -> None:
    """Sell / close an open position early."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    with Progress(
        SpinnerColumn(), TextColumn("[cyan]Sending sell request…"),
        transient=True, console=console
    ) as prog:
        prog.add_task("sell")
        result = await client.sell_option(args.trade_id)
    console.print(Panel(
        f"[bold green]✓ Sell response received[/]\n{result}",
        title="📤 [bold]Sell Option[/]",
        border_style="green",
        box=box.ROUNDED,
        expand=False,
    ))


async def cmd_pending(client: Quotex, args: argparse.Namespace) -> None:
    """Place a pending order to be executed at a future time."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
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
        f"[cyan]Placing pending order:[/] [bold]{args.direction.upper()}[/] "
        f"[yellow]{asset}[/] | amount=[bold]{args.amount}[/] | "
        f"duration=[bold]{args.duration}s[/]"
        + (f" | open_time=[bold]{args.open_time}[/]" if args.open_time else "")
    )

    with Progress(
        SpinnerColumn(), TextColumn("[cyan]Sending pending order…"),
        transient=True, console=console
    ) as prog:
        prog.add_task("pending")
        status, data = await client.open_pending(
            args.amount, asset, args.direction,
            args.duration, args.open_time
        )

    if status:
        console.print(
            f"[bold green]✓ Pending order placed![/]\n{data}"
        )
    else:
        console.print(f"[bold red]✗ Pending order failed.[/] {data}")
        sys.exit(1)


async def cmd_check(client: Quotex, args: argparse.Namespace) -> None:
    """Check win/loss result of a trade by ID."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return

    console.print(
        f"[cyan]Checking result for Trade ID:[/] [bold]{args.trade_id}[/]"
    )
    with Progress(
        SpinnerColumn(), TextColumn("[cyan]{task.description}"),
        transient=True, console=console
    ) as prog:
        task_id = prog.add_task("Waiting…")
        check_task = asyncio.create_task(
            client.check_win(args.trade_id, timeout=300)
        )
        elapsed = 0
        while not check_task.done():
            prog.update(
                task_id,
                description=f"Waiting… [bold yellow]{elapsed}s[/] elapsed",
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(check_task), timeout=1.0
                )
            except asyncio.TimeoutError:
                elapsed += 1

        win, profit = await check_task

    color = "green" if win == "win" else "red"
    label = "WIN 🎉" if win == "win" else "LOSS 💸"
    console.print(
        f"[bold {color}]{label}[/] — Profit: [bold]{profit:+.2f}[/]"
    )


async def cmd_result(client: Quotex, args: argparse.Namespace) -> None:
    """Look up a trade result from history by operation ID."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    status, data = await client.get_result(args.operation_id)
    if status is None:
        console.print(f"[red]Operation ID '{args.operation_id}' not found.[/]")
        return
    color = "green" if status == "win" else "red"
    console.print(Panel(
        f"[bold {color}]Result: {status.upper()}[/]\n{data}",
        title=f"📋 [bold]Trade Result — {args.operation_id}[/]",
        border_style=color,
        box=box.ROUNDED,
    ))


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


# ---------------------------------------------------------------------------
# Command implementations — History
# ---------------------------------------------------------------------------

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
            "[green]WIN[/]" if profit > 0
            else "[red]LOSS[/]" if profit < 0
            else "[dim]DRAW[/]"
        )
        direction = t.get("command", t.get("direction", "?")).upper()
        dir_color = "green" if direction in ("CALL", "BUY", "UP") else "red"
        ts = t.get("openTimestamp", t.get("createdAt", ""))
        try:
            ts_str = datetime.fromtimestamp(int(ts)).strftime(
                "%m-%d %H:%M"
            ) if ts else "—"
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


# ---------------------------------------------------------------------------
# Command implementations — Indicators
# ---------------------------------------------------------------------------

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
        SpinnerColumn(), TextColumn("[cyan]Fetching history + computing…"),
        transient=True, console=console
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


# ---------------------------------------------------------------------------
# Command implementations — Monitor & Strategy
# ---------------------------------------------------------------------------

async def cmd_monitor(client: Quotex, args: argparse.Namespace) -> None:
    """Real-time price monitor for an asset (Ctrl+C to stop)."""
    if not await connect_with_retry(client, True):
        return
    asset, _ = await client.get_available_asset(args.asset, force_open=True)
    console.print(
        f"[cyan]Monitoring[/] [bold]{asset}[/] "
        f"[dim](period={args.period}s — Ctrl+C to stop)[/]"
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
                        f" [green]+{delta:.5f}[/]" if delta > 0
                        else f" [red]{delta:.5f}[/]" if delta < 0
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
    """Run Triple-Confirmation strategy."""
    if not await connect_with_retry(client, True):
        return
    strategy = TripleConfirmationStrategy(
        client=client,
        asset=args.asset,
        period=args.period,
    )
    console.print(Panel(
        f"[bold cyan]Asset:[/]      {args.asset}\n"
        f"[bold cyan]Period:[/]     {args.period}s\n"
        f"[bold cyan]Auto-trade:[/] {'YES ⚠ DEMO ONLY' if args.auto_trade else 'NO (signal only)'}",
        title="🧠 [bold]Triple Confirmation Strategy[/]",
        border_style="magenta",
        box=box.ROUNDED,
        expand=False,
    ))
    await strategy.run(auto_trade=args.auto_trade)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _print_candles_table(
    candles: list[dict],
    asset: str,
    period: int,
    title: str | None = None,
) -> None:
    """Render a Rich table of candle data."""
    tbl_title = title or f"🕯️  [bold]Candles — {asset} ({period}s)[/]"
    table = Table(
        title=tbl_title,
        box=box.ROUNDED,
        border_style="bright_blue",
        show_header=True,
        header_style="bold bright_white on blue",
        row_styles=["none", "dim"],
    )
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Open", justify="right")
    table.add_column("High", justify="right", style="green")
    table.add_column("Low", justify="right", style="red")
    table.add_column("Close", justify="right", style="bold")
    table.add_column("Dir", justify="center")

    for c in candles:
        ts = c.get("time", c.get("timestamp", 0))
        try:
            ts_str = datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M:%S")
        except Exception:
            ts_str = str(ts)
        o = c.get("open", 0)
        h = c.get("max", c.get("high", 0))
        lo = c.get("min", c.get("low", 0))
        cl = c.get("close", 0)
        direction = (
            "[green]▲[/]" if float(cl) >= float(o)
            else "[red]▼[/]"
        )
        table.add_row(
            ts_str,
            f"{float(o):.5f}",
            f"{float(h):.5f}",
            f"{float(lo):.5f}",
            f"{float(cl):.5f}",
            direction,
        )
    console.print(table)


def _save_candles_csv(candles: list[dict], filepath: str) -> None:
    """Save candles list to a CSV file."""
    if not candles:
        return
    fieldnames = list(candles[0].keys())
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(candles)


# ---------------------------------------------------------------------------
# test-all runner
# ---------------------------------------------------------------------------

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
    await _test("get_all_asset_name", asyncio.coroutine(
        lambda: client.get_all_asset_name()
    )())
    await _test("get_payment (sync)", asyncio.coroutine(
        lambda: client.get_payment()
    )())
    await _test("get_payout_by_asset EURUSD",
                asyncio.coroutine(
                    lambda: client.get_payout_by_asset("EURUSD")
                )())
    await _test("get_candles EURUSD 60s",
                client.get_candles("EURUSD", time.time(), 3600, 60))
    await _test("get_candle_v2 EURUSD",
                client.get_candle_v2("EURUSD", 60))
    await _test("get_historical_candles EURUSD 1h",
                client.get_historical_candles(
                    "EURUSD", amount_of_seconds=3600, period=60, max_workers=2
                ))
    await _test("get_realtime_price EURUSD",
                client.start_realtime_price("EURUSD", 60))
    await _test("get_realtime_sentiment EURUSD",
                client.start_realtime_sentiment("EURUSD", 60))
    await _test("get_trader_history demo p1",
                client.get_trader_history(1, 1))
    await _test("calculate_indicator RSI",
                client.calculate_indicator(
                    "EURUSD", "RSI", {"period": 14}, timeframe=60
                ))

    console.rule()
    color = "green" if failed == 0 else "yellow"
    console.print(
        f"[bold {color}]Results: {passed} passed, {failed} failed[/]"
    )


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

COMMAND_MAP: dict[str, Any] = {
    "login":               cmd_login,
    "balance":             cmd_balance,
    "server-time":         cmd_server_time,
    "set-demo-balance":    cmd_set_demo_balance,
    "settings":            cmd_settings,
    "assets":              cmd_assets,
    "payout":              cmd_payout,
    "payout-asset":        cmd_payout_asset,
    "candles":             cmd_candles,
    "candles-v2":          cmd_candles_v2,
    "candles-deep":        cmd_candles_deep,
    "history-line":        cmd_history_line,
    "candle-info":         cmd_candle_info,
    "realtime-price":      cmd_realtime_price,
    "realtime-sentiment":  cmd_realtime_sentiment,
    "realtime-candle":     cmd_realtime_candle,
    "buy":                 cmd_buy,
    "sell":                cmd_sell,
    "pending":             cmd_pending,
    "check":               cmd_check,
    "result":              cmd_result,
    "signals":             cmd_signals,
    "history":             cmd_history,
    "indicator":           cmd_indicator,
    "monitor":             cmd_monitor,
    "strategy":            cmd_strategy,
    "test-all":            cmd_test_all,
}


async def main() -> None:
    parser = make_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    email, password = credentials()
    client = Quotex(
        email=email,
        password=password,
        on_otp_callback=on_otp,
    )

    handler = COMMAND_MAP.get(args.command)
    if handler is None:
        console.print(f"[red]Unknown command: {args.command}[/]")
        parser.print_help()
        return

    try:
        await handler(client, args)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
