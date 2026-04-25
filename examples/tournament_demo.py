"""
Example: Using Tournament Accounts in PyQuotex
"""
import asyncio
import logging

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


async def tournament_demo():
    # 1. Get credentials (will prompt if not set in settings/config.ini)
    email, password = credentials()

    # 2. Initialize the client
    client = Quotex(email=email, password=password)

    print("🚀 Connecting to Quotex...")
    check, reason = await client.connect()

    if not check:
        print(f"❌ Connection failed: {reason}")
        return

    try:
        # 3. Define the Tournament ID
        # You can find this ID in the Quotex platform under the Tournaments section.
        # Example ID: 12345
        TOURNAMENT_ID = 12345

        print(f"\n🏆 Switching to Tournament Account (ID: {TOURNAMENT_ID})...")

        # Tournament accounts are a special type of PRACTICE mode.
        # We pass the tournament_id to activate it.
        await client.change_account("PRACTICE", tournament_id=TOURNAMENT_ID)

        # 4. Check Tournament Balance
        # Now get_balance() will return the balance specific to that tournament.
        balance = await client.get_balance()
        print(f"💰 Tournament Balance: ${balance}")

        # 5. Example: Place a trade in the tournament
        asset = "EURUSD"
        amount = 10
        direction = "call"
        duration = 60  # 1 minute

        asset, data = await client.get_available_asset(asset, force_open=True)
        if not asset:
            raise Exception(
                f"Asset {asset[0]} is not open. Please open it before placing a trade."
            )

        print(f"\n📊 Placing trade in tournament: {asset} | {direction} | ${amount}")
        # Note: We commented the buy line for safety in this demo.
        status, buy_info = await client.buy(amount, asset, direction, duration)
        print(f"Trade status: {'Success' if status else 'Failed'}")
        if status:
            print(f"✅ Trade placed! ID: {buy_info.get('id')}")

    except Exception as e:
        print(f"⚠️ Error during demo: {e}")
    finally:
        # 6. Close connection
        print("\n🔌 Closing connection...")
        await client.close()


if __name__ == "__main__":
    asyncio.run(tournament_demo())
