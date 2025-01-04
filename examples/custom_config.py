# examples/custom_config.py

import asyncio
import logging
from quotexapi.stable_api import Quotex

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(message)s'
)

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"

email = "account@gmail.com"
password = "you_password"
email_pass = "gmail_app_key"


client = Quotex(
    email=email,
    password=password
)

# client.set_session(user_agent=USER_AGENT)

# PRACTICE mode is default / REAL mode is optional
# client.set_account_mode("REAL")

client.debug_ws_enable = False


async def main():
    await client.connect()
    is_connected = client.check_connect()
    if is_connected:
        print(f"Connected: {is_connected}")
        balance = await client.get_balance()
        print(f"Balance: {balance}")
    print("Saindo...")
    client.close()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("Encerrando o programa.")
    finally:
        loop.close()
