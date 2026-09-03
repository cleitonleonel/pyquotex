"""Output formatting helpers shared by CLI commands."""

import csv
from datetime import datetime
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table

console = Console()


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
    table.add_row("Demo", f"{profile.demo_balance:,.2f}", profile.currency_symbol or "")
    table.add_row("Live", f"{profile.live_balance:,.2f}", profile.currency_symbol or "")
    return table


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
        direction = "[green]▲[/]" if float(cl) >= float(o) else "[red]▼[/]"
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
    """Save the candles list to a CSV file."""
    if not candles:
        return
    fieldnames = list(candles[0].keys())
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(candles)
