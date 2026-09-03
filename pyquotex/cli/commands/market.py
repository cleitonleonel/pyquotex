"""Market CLI command handlers."""

import argparse

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pyquotex.cli.runtime import connect_with_retry
from pyquotex.stable_api import Quotex

console = Console()


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
    console.print(
        Panel(
            f"[bold cyan]Asset:[/]     {args.asset}\n"
            f"[bold cyan]Timeframe:[/] {args.timeframe}M\n"
            f"[bold green]Payout:[/]    {result}%",
            title="💹 [bold]Asset Payout[/]",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )
