#!/usr/bin/env python3
"""PyQuotex CLI — Fast Quotex trading API client."""
import asyncio
import argparse
import sys
import time
import logging
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.text import Text
from rich import print as rprint

from pyquotex.stable_api import Quotex
from pyquotex.config import credentials

console = Console()
logger = logging.getLogger(__name__)

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
            "  pyquotex buy --asset EURUSD --amount 5 --direction call --duration 60\n"
            "  pyquotex candles --asset EURUSD --period 60 --count 10\n"
            "  pyquotex assets\n"
            "  pyquotex history --pages 2\n"
            "  pyquotex monitor --asset EURUSD\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # login
    login_p = sub.add_parser("login", help="Test login and show balance")
    login_p.add_argument("--demo", action="store_true", default=True, help="Use demo account (default)")
    login_p.add_argument("--live", action="store_true", help="Use live account")

    # balance
    bal_p = sub.add_parser("balance", help="Show account balance")
    bal_p.add_argument("--demo", action="store_true", default=True, help="Use demo account (default)")
    bal_p.add_argument("--live", action="store_true", help="Use live account")

    # buy
    buy_p = sub.add_parser("buy", help="Place a trade")
    buy_p.add_argument("--asset", default="EURUSD", help="Asset symbol (default: EURUSD)")
    buy_p.add_argument("--amount", type=float, default=1.0, help="Trade amount (default: 1.0)")
    buy_p.add_argument("--direction", choices=["call", "put"], default="call",
                       help="Trade direction: call (up) or put (down)")
    buy_p.add_argument("--duration", type=int, default=60, help="Duration in seconds (default: 60)")
    buy_p.add_argument("--demo", action="store_true", default=True, help="Use demo account (default)")
    buy_p.add_argument("--live", action="store_true", help="Use live account")

    # sell
    sell_p = sub.add_parser("sell", help="Sell/close an open position")
    sell_p.add_argument("--id", dest="trade_id", required=True, help="Trade ID to sell")
    sell_p.add_argument("--demo", action="store_true", default=True)
    sell_p.add_argument("--live", action="store_true")

    # candles
    candles_p = sub.add_parser("candles", help="Fetch candle data")
    candles_p.add_argument("--asset", default="EURUSD", help="Asset symbol (default: EURUSD)")
    candles_p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    candles_p.add_argument("--count", type=int, default=10, help="Number of candles (default: 10)")

    # assets
    sub.add_parser("assets", help="List available assets")

    # history
    hist_p = sub.add_parser("history", help="Show trade history")
    hist_p.add_argument("--demo", action="store_true", default=True, help="Use demo account (default)")
    hist_p.add_argument("--live", action="store_true", help="Use live account")
    hist_p.add_argument("--pages", type=int, default=1, help="Number of history pages (default: 1)")

    # monitor
    mon_p = sub.add_parser("monitor", help="Monitor asset price in real-time")
    mon_p.add_argument("--asset", default="EURUSD", help="Asset symbol (default: EURUSD)")
    mon_p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")

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
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        with Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]Connecting (attempt {attempt}/{max_attempts})…"),
            transient=True,
            console=console,
        ) as prog:
            prog.add_task("connect")
            check, reason = await client.connect(is_demo)

        if check:
            console.print(f"[bold green]✓[/] Connected — {reason}")
            return True

        console.print(f"[yellow]⚠ Connection failed:[/] {reason}. Retrying in {delay:.0f}s…")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)

    console.print("[bold red]✗ Could not connect after maximum attempts.[/]")
    return False


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _is_demo(args) -> bool:
    """Resolve --demo / --live flags to is_demo bool."""
    if hasattr(args, "live") and args.live:
        return False
    return True


def _balance_table(profile) -> Table:
    table = Table(title="💰 Account Balance", show_header=True, header_style="bold magenta")
    table.add_column("Account", style="cyan", no_wrap=True)
    table.add_column("Balance", justify="right", style="green")
    table.add_column("Currency", style="white")
    table.add_row("Demo", f"{profile.demo_balance:,.2f}", profile.currency_symbol or "")
    table.add_row("Live", f"{profile.live_balance:,.2f}", profile.currency_symbol or "")
    return table


async def cmd_login(client: Quotex, args):
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return
    profile = await client.get_profile()
    console.print(_balance_table(profile))
    console.print(Panel(
        f"[bold]Nickname:[/] {profile.nick_name}\n"
        f"[bold]Country:[/]  {profile.country_name}\n"
        f"[bold]Offset:[/]   {profile.offset}",
        title="👤 Profile",
        border_style="blue",
    ))


async def cmd_balance(client: Quotex, args):
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return
    profile = await client.get_profile()
    console.print(_balance_table(profile))


async def cmd_buy(client: Quotex, args):
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    asset = args.asset.upper()
    amount = args.amount
    direction = args.direction
    duration = args.duration

    console.print(
        f"[cyan]Placing trade:[/] [bold]{direction.upper()}[/] "
        f"[yellow]{asset}[/] | amount=[bold]{amount}[/] | duration=[bold]{duration}s[/]"
    )

    with Progress(SpinnerColumn(), TextColumn("[cyan]Sending order…"), transient=True, console=console) as prog:
        prog.add_task("buy")
        status, trade_id = await client.buy(amount, asset, direction, duration)

    if status:
        console.print(f"[bold green]✓ Order placed![/] Trade ID: [bold]{trade_id}[/]")
        console.print("[dim]Waiting for result…[/]")
        win, profit = await client.check_win(trade_id)
        result_color = "green" if win else "red"
        result_label = "WIN 🎉" if win else "LOSS 💸"
        console.print(
            f"[bold {result_color}]{result_label}[/] — Profit: [bold]{profit:+.2f}[/]"
        )
    else:
        console.print(f"[bold red]✗ Order failed.[/] Response: {trade_id}")


async def cmd_sell(client: Quotex, args):
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    trade_id = args.trade_id
    console.print(f"[cyan]Selling trade ID:[/] {trade_id}")
    with Progress(SpinnerColumn(), TextColumn("[cyan]Sending sell order…"), transient=True, console=console) as prog:
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
    return "".join(bars[int((v - mn) / span * (len(bars) - 1))] for v in values)


async def cmd_candles(client: Quotex, args):
    ok = await connect_with_retry(client, is_demo=True)
    if not ok:
        return

    asset = args.asset.upper()
    period = args.period
    count = args.count
    end_time = time.time()

    with Progress(SpinnerColumn(), TextColumn(f"[cyan]Fetching {count} candles for {asset}…"),
                  transient=True, console=console) as prog:
        prog.add_task("candles")
        candles = await client.get_candles(asset, end_time, count, period)

    if not candles:
        console.print("[yellow]No candle data returned.[/]")
        return

    table = Table(title=f"🕯 Candles — {asset} (period={period}s)", header_style="bold cyan")
    table.add_column("Time", style="dim")
    table.add_column("Open", justify="right")
    table.add_column("High", justify="right", style="green")
    table.add_column("Low", justify="right", style="red")
    table.add_column("Close", justify="right")
    table.add_column("Dir", justify="center")

    closes = []
    for c in candles[-count:]:
        ts = time.strftime("%H:%M:%S", time.localtime(c.get("time", 0)))
        o, h, l, cl = c.get("open", 0), c.get("high", 0), c.get("low", 0), c.get("close", 0)
        direction = "[green]▲[/]" if cl >= o else "[red]▼[/]"
        table.add_row(ts, f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{cl:.5f}", direction)
        closes.append(cl)

    console.print(table)
    if closes:
        console.print(f"  Trend: [bold]{_ascii_sparkline(closes)}[/]")


async def cmd_assets(client: Quotex, args):
    ok = await connect_with_retry(client, is_demo=True)
    if not ok:
        return

    with Progress(SpinnerColumn(), TextColumn("[cyan]Fetching asset list…"), transient=True, console=console) as prog:
        prog.add_task("assets")
        instruments = await client.get_instruments()

    if not instruments:
        console.print("[yellow]No instruments data available.[/]")
        return

    table = Table(title="📊 Available Assets", header_style="bold magenta")
    table.add_column("Symbol", style="cyan")
    table.add_column("Name")
    table.add_column("Payout", justify="right", style="green")
    table.add_column("Status", justify="center")

    items = instruments if isinstance(instruments, list) else instruments.get("list", [])
    for item in items[:50]:  # cap display at 50
        symbol = item.get("symbol", item.get("asset", ""))
        name = item.get("name", "")
        payout = f"{item.get('payout', item.get('profit', 0))}%"
        status = "[green]●[/]" if item.get("enabled", True) else "[red]○[/]"
        table.add_row(symbol, name, payout, status)

    console.print(table)
    if len(items) > 50:
        console.print(f"[dim]… and {len(items) - 50} more assets[/]")


async def cmd_history(client: Quotex, args):
    is_demo = _is_demo(args)
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    account_type = 1 if is_demo else 0
    all_deals = []

    with Progress(SpinnerColumn(), TextColumn("[cyan]Fetching history…"), transient=True, console=console) as prog:
        prog.add_task("history")
        for page in range(1, args.pages + 1):
            data = await client.get_trader_history(account_type, page)
            deals = data.get("deals", []) if isinstance(data, dict) else []
            all_deals.extend(deals)

    if not all_deals:
        console.print("[yellow]No trade history found.[/]")
        return

    table = Table(title="📜 Trade History", header_style="bold blue")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Asset", style="cyan")
    table.add_column("Direction", justify="center")
    table.add_column("Amount", justify="right")
    table.add_column("Profit", justify="right")
    table.add_column("Result", justify="center")
    table.add_column("Time", style="dim")

    for deal in all_deals[:100]:
        d_id = str(deal.get("id", ""))[:8]
        asset = deal.get("asset", "")
        direction = deal.get("command", "")
        amount = f"{deal.get('amount', 0):.2f}"
        profit = deal.get("profit", 0)
        profit_str = f"[green]+{profit:.2f}[/]" if profit > 0 else f"[red]{profit:.2f}[/]"
        result = "[green]WIN[/]" if profit > 0 else "[red]LOSS[/]"
        ts = time.strftime("%m-%d %H:%M", time.localtime(deal.get("openTimestamp", 0)))
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
        f"Net P&L: [bold]{'[green]' if total_profit >= 0 else '[red]'}{total_profit:+.2f}[/]"
    )


async def cmd_monitor(client: Quotex, args):
    is_demo = True
    ok = await connect_with_retry(client, is_demo)
    if not ok:
        return

    asset = args.asset.upper()
    period = args.period
    console.print(Panel(
        f"Monitoring [bold cyan]{asset}[/] | period=[bold]{period}s[/]\nPress Ctrl+C to stop.",
        title="📡 Live Monitor",
        border_style="cyan",
    ))

    await client.start_realtime_candle(asset, period)
    prices = []
    try:
        while True:
            candle = client.get_realtime_candle(asset)
            if candle:
                price = candle[2] if isinstance(candle, list) and len(candle) >= 3 else None
                if price:
                    prices.append(float(price))
                    trend = _ascii_sparkline(prices[-20:])
                    direction = "▲" if len(prices) < 2 or prices[-1] >= prices[-2] else "▼"
                    color = "green" if direction == "▲" else "red"
                    console.print(
                        f"\r[{color}]{direction}[/{color}] [bold]{asset}[/] "
                        f"Price: [bold cyan]{price:.5f}[/]  [{color}]{trend}[/{color}]",
                        end="",
                    )
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Monitoring stopped.[/]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
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
        console.print("[bold red]✗ No credentials found.[/] Please configure pyquotex/config.py")
        sys.exit(1)

    client = Quotex(
        email=email,
        password=password,
    )

    try:
        command_map = {
            "login": cmd_login,
            "balance": cmd_balance,
            "buy": cmd_buy,
            "sell": cmd_sell,
            "candles": cmd_candles,
            "assets": cmd_assets,
            "history": cmd_history,
            "monitor": cmd_monitor,
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
