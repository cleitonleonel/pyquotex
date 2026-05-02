import asyncio

from pyquotex.stable_api import Quotex

# Your existing imports and initializations here...

# Initialize your Quotex client
client = Quotex(
    email="email@gmail.com",
    password="password",
    lang="pt",  # Default pt -> Português.
)
cookies = "custom_cookies"
ssid = "session_id"
user_agent = "custom_user_agent"

"""client.set_session(
    user_agent=user_agent,
    cookies=cookies,
    ssid=ssid
)"""


def check_moving_average_cross(
        prices: list,
        short_window: int = 5,
        long_window: int = 15,
) -> str:
    """Return 'buy', 'sell', or 'hold' based on MA crossover.

    ``prices`` is the list returned by ``get_realtime_price()``.
    Each entry is a dict that may contain a ``'price'`` key or a direct
    numeric price depending on the broker's WS format.  We handle both.
    """
    if len(prices) < long_window:
        return "hold"

    def _price(item: dict | float) -> float:
        if isinstance(item, dict):
            # try common key names
            for key in ("price", "close", "value"):
                if key in item:
                    return float(item[key])
            # fallback: first numeric value in the dict
            for v in item.values():
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return float(item)

    short_ma = sum(_price(p) for p in prices[-short_window:]) / short_window
    long_ma  = sum(_price(p) for p in prices[-long_window:])  / long_window

    # Crossover: compare current vs previous bar's MAs
    prev_short_ma = sum(_price(p) for p in prices[-(short_window + 1):-1]) / short_window
    prev_long_ma  = sum(_price(p) for p in prices[-(long_window  + 1):-1]) / long_window

    if prev_short_ma <= prev_long_ma and short_ma > long_ma:
        return "buy"   # bullish crossover
    elif prev_short_ma >= prev_long_ma and short_ma < long_ma:
        return "sell"  # bearish crossover
    else:
        return "hold"


async def get_prices(asset_name):
    await client.start_realtime_price(asset_name, 60)
    prices = await client.get_realtime_price(asset_name)
    while len(prices) <= 15:
        prices = await client.get_realtime_price(asset_name)
        await asyncio.sleep(1)
    return prices


async def monitor_markets():
    assets = client.get_all_asset_name()
    while True:
        for asset in assets:
            asset_name, asset_data = await client.get_available_asset(asset[0], force_open=False)

            # Check if asset_data is None before accessing its elements
            if asset_data is None:
                print(f"Failed to retrieve data for asset {asset_name}")

            elif asset_data[2]:  # Check if the asset is open (assuming index 2 indicates open state)
                prices = await get_prices(asset_name)
                if len(prices) >= 15:
                    signal = check_moving_average_cross(prices)
                    print(f"Asset: {asset_name} | Signal: {signal}")
                    # Example of placing a trade based on the signal
                    if signal == "buy":
                        await client.buy(50, asset_name, "call", 60)
                    elif signal == "sell":
                        await client.buy(50, asset_name, "put", 60)
            else:
                print(f"Asset {asset_name} is closed.")

            await asyncio.sleep(1)  # Pause briefly before checking the next asset
        # To avoid spamming, wait before the next monitoring cycle
        print("\nAwaiting before the next monitoring cycle...\n")
        await asyncio.sleep(10)  # Adjust the sleep duration based on your needs


async def main():
    check_connect, message = await client.connect()
    print(message)
    if check_connect:
        # try:
        await monitor_markets()
        # except Exception as e:  # Catch generic exceptions for better error handling
        #    print(f"An error occurred while monitoring markets: {e}")
    else:
        print("Failed to connect to the client:", message)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("Encerrando o programa.")
    finally:
        loop.close()
