"""Account CLI command handlers."""

import argparse
from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pyquotex.cli.formatters import _balance_table
from pyquotex.cli.runtime import _is_demo, connect_with_retry
from pyquotex.stable_api import Quotex

console = Console()


async def cmd_login(client: Quotex, args: argparse.Namespace) -> None:
    """Connect and display user profile + balance."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    profile = await client.get_profile()
    console.print(_balance_table(profile))
    console.print(
        Panel(
            f"[bold blue]Nickname:[/] {profile.nick_name}\n"
            f"[bold blue]Country:[/]  {profile.country_name}\n"
            f"[bold blue]Offset:[/]   {profile.offset}",
            title="👤 [bold]User Profile[/]",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
        )
    )


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
    console.print(
        Panel(
            f"[bold cyan]Unix:[/]   {ts}\n[bold cyan]Local:[/]  {dt.strftime('%Y-%m-%d %H:%M:%S')}",
            title="🕒 [bold]Server Time[/]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
    )


async def cmd_set_demo_balance(client: Quotex, args: argparse.Namespace) -> None:
    """Refill or set the demo (practice) account balance."""
    if not await connect_with_retry(client, True):
        return
    result = await client.edit_practice_balance(args.amount)
    console.print(
        Panel(
            f"[bold green]✓ Demo balance updated[/]\n{result}",
            title="💸 [bold]Set Demo Balance[/]",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )


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
