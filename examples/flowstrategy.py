import asyncio

from rich.console import Console
from rich.panel import Panel

from pyquotex.stable_api import Quotex

# Replace with your Quotex login credentials
email = "email.com"
password = "password"

console = Console()


async def connect_to_quotex():
    """ Connects to Quotex API and returns the client object. """
    console.clear()
    console.print(
        Panel("[bold yellow]🔄 Establishing Connection...[/bold yellow]", title="[cyan]System[/cyan]", style="yellow",
              width=60))
    await asyncio.sleep(1.5)

    client = Quotex(email=email, password=password)
    connected, reason = await client.connect()

    console.clear()
    if connected:
        console.print(
            Panel("[bold green]✅ Connection Successful![/bold green]", title="[green]Status[/green]", style="green",
                  width=60))
        await asyncio.sleep(1.5)
        return client
    else:
        console.print(
            Panel(f"[bold red]❌ Connection Failed! Reason: {reason}[/bold red]", title="[red]Status[/red]", style="red",
                  width=60))
        return None


async def get_rsi(client, asset_name, period=4, timeframe=60):
    """ Fetches the RSI value for the asset. """
    rsi_data = await client.calculate_indicator(
        asset=asset_name,
        indicator="RSI",
        params={"period": period},
        timeframe=timeframe  # 1-minute RSI
    )
    if rsi_data and "current" in rsi_data:
        return rsi_data["current"]
    return None


async def buy_and_check_win(client, asset_name, amount, duration, direction):
    """ Places a trade and fetches results after completion. """
    console.clear()
    console.print(Panel(
        f"[bold yellow]🚀 Placing Trade: {amount}$ on {asset_name} ({direction.upper()}) for {duration}s[/bold yellow]",
        title="[yellow]Trade Status[/yellow]", style="yellow", width=60))
    await asyncio.sleep(1.5)

    status, buy_info = await client.buy(amount, asset_name, direction, duration)

    if not status:
        console.clear()
        console.print(Panel(f"[bold red]❌ Trade Failed! Error: {buy_info}[/bold red]", title="[red]Trade Error[/red]",
                            style="red", width=60))
        return False

    console.print(
        Panel(f"[bold green]✅ Trade Placed! ID: {buy_info['id']}[/bold green]", title="[green]Trade Status[/green]",
              style="green", width=60))

    # Wait for trade result before placing another
    await asyncio.sleep(duration)
    return True


async def monitor_rsi_and_trade(client, asset_name, amount, duration):
    """ Continuously monitors RSI and trades when conditions are met. """
    console.print(Panel("[bold cyan]📊 Monitoring RSI... Waiting for trade conditions...[/bold cyan]",
                        title="[cyan]RSI Tracker[/cyan]", style="cyan", width=60))

    trade_active = False
    while True:
        if trade_active:
            await asyncio.sleep(1)
            continue

        rsi = await get_rsi(client, asset_name)
        if rsi is None:
            console.print(
                Panel("[bold red]⚠️ Failed to fetch RSI! Retrying...[/bold red]", title="[red]RSI Error[/red]",
                      style="red", width=60))
            await asyncio.sleep(1)
            continue

        console.print(
            Panel(f"[bold cyan]📈 RSI: {rsi}[/bold cyan]", title="[cyan]RSI Value[/cyan]", style="cyan", width=60))

        if rsi > 70:
            trade_active = await buy_and_check_win(client, asset_name, amount, duration, "call")  # Overbought → BUY
        elif rsi < 30:
            trade_active = await buy_and_check_win(client, asset_name, amount, duration, "put")  # Oversold → SELL

        # Wait for RSI to normalize before taking another trade
        while trade_active:
            rsi = await get_rsi(client, asset_name)
            if 40 <= rsi <= 60:
                trade_active = False
            await asyncio.sleep(3)


async def main():
    """ Main function to connect, check asset, and continuously trade based on RSI. """
    client = await connect_to_quotex()
    if not client:
        return

    asset_name = "USDINR_otc"
    await monitor_rsi_and_trade(client, asset_name, amount=270, duration=60)


# Run the async function
asyncio.run(main())
