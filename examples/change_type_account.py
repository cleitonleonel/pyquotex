import asyncio

from pyquotex.stable_api import Quotex


async def connect_with_retries(client, retries=3):
    for attempt in range(retries):
        check_connect, message = await client.connect()
        if check_connect:
            return True
        print(f"Connection attempt {attempt + 1} failed: {message}")
        await asyncio.sleep(2)  # Wait before retrying
    return False


async def complete_example():
    # Initialization
    client = Quotex(
        email="creton.cleiton@gmail.com",
        password="traderBR2025$",
        lang="pt"
    )
    client.set_account_mode("PRACTICE")  # Initialize with REAL or PRACTICE

    try:
        # Connection with retries
        if await connect_with_retries(client):
            # Switch to practice account
            await client.change_account("PRACTICE")
            # Check balance in practice mode
            balance = await client.get_balance()
            print(f"Practice account balance: {balance}")

            # Switch to real account
            await client.change_account("REAL")

            # Check balance in real mode
            balance = await client.get_balance()
            print(f"Real account balance: {balance}")

            # Perform operations...
        else:
            print("Failed to connect after retries.")
            return
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if client:
            await client.close()


# Execute
asyncio.run(complete_example())
