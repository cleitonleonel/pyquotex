"""CLI runtime helpers: connection retry, OTP prompt, demo detection."""

import argparse
import asyncio

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from pyquotex.stable_api import Quotex

console = Console()

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
                TextColumn(f"[cyan]Connecting (attempt {attempt}/{max_attempts})…"),
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

        console.print(f"[yellow]⚠ Connection failed:[/] {reason}. Retrying in {delay:.0f}s…")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)

    console.print("[bold red]✗ Could not connect after maximum attempts.[/]")
    return False


def _is_demo(args: argparse.Namespace) -> bool:
    if hasattr(args, "live") and args.live:
        return False
    return True
