"""Trading CLI command handlers."""

import argparse
import asyncio
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from pyquotex.cli.runtime import _is_demo, connect_with_retry
from pyquotex.stable_api import Quotex

console = Console()


async def cmd_buy(client: Quotex, args: argparse.Namespace) -> None:
    """Place an immediate binary option trade."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return

    asset, asset_info = await client.get_available_asset(args.asset, force_open=True)
    if not asset_info or not asset_info[0]:
        console.print(f"[bold red]✗ Asset {args.asset} not found or closed.[/]")
        return

    console.print(
        f"[cyan]Placing trade:[/] [bold]{args.direction.upper()}[/] "
        f"[yellow]{asset}[/] | amount=[bold]{args.amount}[/] | "
        f"duration=[bold]{args.duration}s[/]"
    )

    with Progress(
            SpinnerColumn(), TextColumn("[cyan]Sending order…"), transient=True, console=console
    ) as prog:
        prog.add_task("buy")
        status, trade_data = await client.buy(args.amount, asset, args.direction, args.duration)

    if status:
        order_data = trade_data if isinstance(trade_data, dict) else {}
        trade_id = order_data.get("id")
        close_ts = order_data.get("closeTimestamp")
        console.print(f"[bold green]✓ Order placed![/] Trade ID: [bold]{trade_id}[/]")

        if getattr(args, "check_win", False):
            with Progress(
                    SpinnerColumn(),
                    TextColumn("[cyan]{task.description}"),
                    transient=True,
                    console=console,
            ) as prog:
                task_id = prog.add_task("Waiting for trade closure...")
                check_task = asyncio.create_task(client.check_win(trade_id, args.duration))
                while not check_task.done():
                    server_now = client.api.timesync.server_timestamp if client.api else None
                    remaining = int(close_ts - server_now) if close_ts and server_now else 0
                    label = (
                        f"Waiting… [bold yellow]{remaining}s[/] remaining"
                        if remaining > 0
                        else "Waiting… [bold yellow]finishing[/]"
                    )
                    prog.update(task_id, description=label)
                    try:
                        await asyncio.wait_for(asyncio.shield(check_task), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass

            win, profit = await check_task
            color = "green" if win == "win" else "red"
            label = "WIN 🎉" if win == "win" else "LOSS 💸"
            console.print(f"[bold {color}]{label}[/] — Profit: [bold]{profit:+.2f}[/]")
        else:
            console.print("[dim]Order dispatched. Pass --check-win to wait for result.[/]")
    else:
        console.print(f"[bold red]✗ Order failed.[/] Response: {trade_data}")
        sys.exit(1)


async def cmd_sell(client: Quotex, args: argparse.Namespace) -> None:
    """Sell / close an open position early."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return
    with Progress(
            SpinnerColumn(), TextColumn("[cyan]Sending sell request…"), transient=True, console=console
    ) as prog:
        prog.add_task("sell")
        result = await client.sell_option(args.trade_id)
    console.print(
        Panel(
            f"[bold green]✓ Sell response received[/]\n{result}",
            title="📤 [bold]Sell Option[/]",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )


async def cmd_pending(client: Quotex, args: argparse.Namespace) -> None:
    """Place a pending order to be executed at a future time."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return

    asset, asset_info = await client.get_available_asset(args.asset, force_open=True)
    if not asset_info or not asset_info[0]:
        console.print(f"[bold red]✗ Asset {args.asset} not found or closed.[/]")
        return

    console.print(
        f"[cyan]Placing pending order:[/] [bold]{args.direction.upper()}[/] "
        f"[yellow]{asset}[/] | amount=[bold]{args.amount}[/] | "
        f"duration=[bold]{args.duration}s[/]"
        + (f" | open_time=[bold]{args.open_time}[/]" if args.open_time else "")
    )

    with Progress(
            SpinnerColumn(), TextColumn("[cyan]Sending pending order…"), transient=True, console=console
    ) as prog:
        prog.add_task("pending")
        status, data = await client.open_pending(
            args.amount, asset, args.direction, args.duration, args.open_time
        )

    if status:
        console.print(f"[bold green]✓ Pending order placed![/]\n{data}")
    else:
        console.print(f"[bold red]✗ Pending order failed.[/] {data}")
        sys.exit(1)


async def cmd_check(client: Quotex, args: argparse.Namespace) -> None:
    """Check win/loss result of a trade by ID."""
    is_demo = _is_demo(args)
    if not await connect_with_retry(client, is_demo):
        return

    console.print(f"[cyan]Checking result for Trade ID:[/] [bold]{args.trade_id}[/]")
    with Progress(
            SpinnerColumn(), TextColumn("[cyan]{task.description}"), transient=True, console=console
    ) as prog:
        task_id = prog.add_task("Waiting…")
        check_task = asyncio.create_task(client.check_win(args.trade_id, timeout=300))
        elapsed = 0
        while not check_task.done():
            prog.update(
                task_id,
                description=f"Waiting… [bold yellow]{elapsed}s[/] elapsed",
            )
            try:
                await asyncio.wait_for(asyncio.shield(check_task), timeout=1.0)
            except asyncio.TimeoutError:
                elapsed += 1

        win, profit = await check_task

    color = "green" if win == "win" else "red"
    label = "WIN 🎉" if win == "win" else "LOSS 💸"
    console.print(f"[bold {color}]{label}[/] — Profit: [bold]{profit:+.2f}[/]")


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
    console.print(
        Panel(
            f"[bold {color}]Result: {status.upper()}[/]\n{data}",
            title=f"📋 [bold]Trade Result — {args.operation_id}[/]",
            border_style=color,
            box=box.ROUNDED,
        )
    )
