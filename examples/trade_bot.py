# examples/trade_bot.py

import asyncio
from quotexapi.config import credentials
from quotexapi.stable_api import Quotex

email, password = credentials()
client = Quotex(
    email=email,
    password=password,
    lang="pt",  # Default pt -> Português.
)


async def analise_sentiment(asset_name, duration):
    count = duration
    while count > 0:
        market_mood = await client.get_realtime_sentiment(asset_name)
        sentiment = market_mood.get('sentiment')
        if sentiment:
            print(f"\rSell: {sentiment.get('sell')} Buy: {sentiment.get('buy')}", end="")

        await asyncio.sleep(0.5)
        count -= 1


async def calculate_profit(asset_name, amount, balance):
    """
    Calculate the profit based on the payout percentage for the given asset.

    Args:
        asset_name (str): The name of the asset.
        amount (float): The amount of money placed on the asset.
        balance (float): The current balance before the profit is calculated.

    Returns:
        tuple: The updated balance and the profit earned.
    """
    payout = client.get_payout_by_asset(asset_name)
    profit = ((payout / 100) * amount)
    balance += amount + profit
    return balance, profit


async def martingale_apply(amount, asset_name, direction, duration, balance, martingale_quantity):
    """
    Apply the Martingale strategy to the given trade, doubling the amount on each loss until the limit is reached.

    Args:
        amount (float): The initial betting amount.
        asset_name (str): The name of the asset being traded.
        direction (str): The trade direction, either "call" or "put".
        duration (int): The duration of the trade in seconds.
        balance (float): The current balance.
        martingale_quantity (int): The number of times the Martingale strategy can be applied.

    Returns:
        tuple: The updated balance, profit, and success status (True/False).
    """
    while martingale_quantity > 0:
        balance -= amount
        print(f"Betting {amount} on asset {asset_name} in the {direction} direction for {duration}s")
        status, buy_info = await client.buy(amount, asset_name, direction, duration)

        if not status:
            print("ERROR: Could not place the bet.")
            return balance, 0, False

        print(f"New Balance: {balance}")

        await analise_sentiment(asset_name, duration)

        result = await check_result(buy_info, direction)

        if result == "Win":
            balance, profit = await calculate_profit(asset_name, amount, balance)
            return balance, profit, True
        elif result == "Doji":
            print("Result: DOJI. No profit or loss.")
            return balance, 0, True

        amount *= 2
        martingale_quantity -= 1

    print("Martingale exhausted. Total loss.")
    return balance, 0, False


async def check_result(buy_data, direction):
    """
    Check the result of the trade based on real-time price and direction.

    Args:
        buy_data (dict): Information about the trade, including open price and close timestamp.
        direction (str): The direction of the trade, either "call" or "put".

    Returns:
        str: The result of the trade ("Win", "Loss", or "Doji").
    """
    open_price = buy_data.get('openPrice')

    while True:
        prices = await client.get_realtime_price(buy_data['asset'])

        if not prices:
            continue

        current_price = prices[-1]['price']

        print(f"\nCurrent Price: {current_price}, Open Price: {open_price}")

        if (direction == "call" and current_price > open_price) or (
                direction == "put" and current_price < open_price):
            print("Result: WIN")
            return 'Win'
        elif (direction == "call" and current_price <= open_price) or (
                direction == "put" and current_price >= open_price):
            print("Result: LOSS")
            return 'Loss'
        else:
            print("Result: DOJI")
            return 'Doji'


async def trade_and_monitor():
    """
    Main function to manage trading and monitor the results.
    It connects to the client, places bets, and applies the Martingale strategy if necessary.

    Returns:
        None
    """
    check_connect, message = await client.connect()
    if check_connect:
        amount = 50
        asset = "AUDCAD"
        direction = "call"
        duration = 60  # in seconds
        balance = await client.get_balance()
        initial_balance = balance
        martingale_quantity = 2
        print("Initial Balance: ", balance)
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)

        if asset_data[2]:
            print("OK: Asset is open.")

            while True:
                print(f"{100 * '='}")
                check_connect = await client.check_connect()
                if not check_connect:
                    check_connect, message = await client.connect()

                print(f"Betting {amount} on asset {asset_name} in the {direction} direction for {duration}s")
                status, buy_info = await client.buy(amount, asset_name, direction, duration)
                print(status, buy_info)
                if status:
                    balance -= amount
                    print(f"New Balance: {balance}")

                    await analise_sentiment(asset_name, duration)

                    result = await check_result(buy_info, direction)

                    if result == "Win":
                        balance, profit = await calculate_profit(asset_name, amount, balance)
                        print(f"Profit: {profit}")
                        print(f"New Balance: {balance}")
                        continue

                    if result == "Doji":
                        print("Result: DOJI. No profit or loss.")
                        continue

                    balance, profit, success = await martingale_apply(
                        amount * 2,
                        asset_name,
                        direction,
                        duration,
                        balance,
                        martingale_quantity
                    )

                    if success:
                        print(f"Profit after Martingale: {profit}")
                    else:
                        print(f"Accumulated Loss: {initial_balance - balance}")

                    print(f"New Balance: {balance}")

                else:
                    print("Operation failed.")

                await asyncio.sleep(1)

        else:
            print("ERROR: Asset is closed.")

    else:
        print("Could not connect to the client.")

    print("Exiting...")
    client.close()


async def main():
    """
    Entry point for the program. It starts the trading and monitoring process.

    Returns:
        None
    """
    await trade_and_monitor()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nClosing the program.")
    finally:
        loop.close()
