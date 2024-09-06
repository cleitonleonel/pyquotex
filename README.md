# PyQuotex

<img src="https://github.com/cleitonleonel/pyquotex/blob/master/pyquotex.gif?raw=true" alt="pyquotex" width="200"/>

To access more features and greater stability, consider joining the private version.

# About the PyQuotex Library

This library was developed with the purpose of facilitating communication with the Quotex platform through WebSockets, enabling real-time data retrieval and the automation of operations. **It is important to note that this library is not a trading bot, nor does it intend to be**.

## Library Objective

The main goal of this library is to provide the necessary tools for developers to integrate their applications with the Quotex platform, automating specific operations in a safe and efficient manner.

## Automation Implementation

Any additional automation, including the creation of automatic bots that make trading decisions, must be implemented by the developer who chooses to use this library in their projects. The responsibility for these additional implementations lies entirely with the developer.

## Disclaimer

As the developer of this library, **I am not responsible for any malfunction or application failure** that may occur due to improper use of the library or the implementation of automations that go beyond the original scope of this tool.

If you decide to use this library, it is crucial that you carefully analyze and rigorously test your implementations to ensure that they meet your needs and expectations.

# Notice about Support and Discussions

Due to the high volume of messages seeking information and support, and considering that I am just one person and do not have the time to assist everyone individually, **I have created a [discussion group on Telegram](https://t.me/+Uzcmc-NZvN4xNTQx)**.

This group was created so that members can help each other, ask questions, and collaborate with others who are also using this library. Your participation and interaction in the group are highly encouraged, as this way everyone can benefit from the collective experience.

Feel free to join the group and contribute with your questions and knowledge.



### Important note
For some reason, cloudflare ends up identifying automated access to the quotex API and we
applies a block, which prevents successful authentication on the platform using a user
and password, I recommend using Python 3.8 or higher to be successful with this API.
For Windows users it is necessary to install the latest possible openssl, which can be obtained
here [Openssl-Windows](https://slproweb.com/products/Win32OpenSSL.html) .
For Linux users, the latest possible versions of openssl are also recommended, simply
just run ```sudo apt install openssl```.

## Let`s Go to the Private Repository
[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/cleiton.leonel)



https://github.com/user-attachments/assets/acaa0cbb-80c2-450c-9c8f-83fdbfedf0fa



### Install
```shell
git clone https://github.com/cleitonleonel/pyquotex.git
cd pyquotex
pip install -r requirements.txt
python3 app.py
```

### Install by pip
```shell
pip install git+https://github.com/cleitonleonel/pyquotex.git
```

### Import as lib
```python
from quotexapi.stable_api import Quotex

email = "account@gmail.com"
password = "you_password"
email_pass = "gmail_app_key"  # If you use gmail and 2FA enabled.

client = Quotex(
    email=email,
    password=password,
    email_pass=email_pass
)
```

### Login by email and password
if connect sucess return True,None  

if connect fail return False,None

```python
import os
import sys
import json
import time
import random
import asyncio
from pathlib import Path
from quotexapi.stable_api import Quotex

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"

email = "account@gmail.com"
password = "you_password"
email_pass = "gmail_app_key"  # If you use gmail and 2FA enabled.
user_data_dir = "user profile path to browser"

client = Quotex(
    email=email,
    password=password,
    lang="pt",  # Default pt -> Português.
    email_pass=email_pass,
    user_data_dir=user_data_dir
)

client.debug_ws_enable = False


def get_all_options():
    return """Opções disponíveis:
    - test_connection
    - get_profile
    - get_balance
    - get_signal_data
    - get_payment
    - get_candle
    - get_candle_v2
    - get_candle_progressive
    - get_realtime_candle
    - get_realtime_sentiment
    - assets_open
    - buy_simple
    - buy_and_check_win
    - buy_multiple
    - balance_refill
    - help
    """


async def connect(attempts=5):
    check, reason = await client.connect()
    if not check:
        attempt = 0
        while attempt <= attempts:
            if not client.check_connect():
                check, reason = await client.connect()
                if check:
                    print("Reconectado com sucesso!!!")
                    break
                else:
                    print("Erro ao reconectar.")
                    attempt += 1
                    if Path(os.path.join(".", "session.json")).is_file():
                        Path(os.path.join(".", "session.json")).unlink()
                    print(f"Tentando reconectar, tentativa {attempt} de {attempts}")
            elif not check:
                attempt += 1
            else:
                break
            await asyncio.sleep(5)
        return check, reason
    print(reason)
    return check, reason


async def test_connection():
    await client.connect()
    is_connected = client.check_connect()
    print(f"Connected: {is_connected}")
    print("Saindo...")
    client.close()


async def get_balance():
    check_connect, message = await client.connect()
    if check_connect:
        # client.change_account("REAL")
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    client.close()


async def buy_simple():
    check_connect, message = await client.connect()
    if check_connect:
        # client.change_account("REAL")
        amount = 50
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 60  # in seconds
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)
        print(asset_name, asset_data)
        if asset_data[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.buy(amount, asset_name, direction, duration)
            print(status, buy_info)
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    client.close()


async def get_profile():
    check_connect, message = await client.connect()
    if check_connect:
        # client.change_account("REAL")
        profile = await client.get_profile()
        description = (
            f"\nUsuário: {profile.nick_name}\n"
            f"Saldo Demo: {profile.demo_balance}\n"
            f"Saldo Real: {profile.live_balance}\n"
            f"Id: {profile.profile_id}\n"
            f"Avatar: {profile.avatar}\n"
            f"País: {profile.country_name}\n"
        )
        print(description)
    print("Saindo...")
    client.close()


async def balance_refill():
    check_connect, message = await client.connect()
    if check_connect:
        # client.change_account("REAL")
        result = await client.edit_practice_balance(5000)
        print(result)
    client.close()


async def buy_and_check_win():
    check_connect, message = await client.connect()
    if check_connect:
        # client.change_account("REAL")
        print("Saldo corrente: ", await client.get_balance())
        amount = 50
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 60  # in seconds
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)
        print(asset_name, asset_data)
        if asset_data[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.buy(amount, asset_name, direction, duration)
            print(status, buy_info)
            if status:
                print("Aguardando resultado...")
                if await client.check_win(buy_info["id"]):
                    print(f"\nWin!!! \nVencemos moleque!!!\nLucro: R$ {client.get_profit()}")
                else:
                    print(f"\nLoss!!! \nPerdemos moleque!!!\nPrejuízo: R$ {client.get_profit()}")
            else:
                print("Falha na operação!!!")
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo Atual: ", await client.get_balance())
    print("Saindo...")
    client.close()


async def buy_multiple(orders=10):
    order_list = [
        {"amount": 5, "asset": "EURUSD", "direction": "call", "duration": 60},
        {"amount": 10, "asset": "AUDCAD_otc", "direction": "put", "duration": 60},
        {"amount": 15, "asset": "AUDJPY_otc", "direction": "call", "duration": 60},
        {"amount": 20, "asset": "AUDUSD_otc", "direction": "put", "duration": 60},
        {"amount": 25, "asset": "CADJPY", "direction": "call", "duration": 60},
        {"amount": 30, "asset": "EURCHF_otc", "direction": "put", "duration": 60},
        {"amount": 35, "asset": "EURGBP_otc", "direction": "call", "duration": 60},
        {"amount": 40, "asset": "EURJPY", "direction": "put", "duration": 60},
        {"amount": 45, "asset": "GBPAUD_otc", "direction": "call", "duration": 60},
        {"amount": 50, "asset": "GBPJPY_otc", "direction": "put", "duration": 60},
    ]
    check_connect, message = await client.connect()
    for i in range(0, orders):
        print("\n/", 80 * "=", "/", end="\n")
        print(f"ABRINDO ORDEM: {i + 1}")
        order = random.choice(order_list)
        print(order)
        if check_connect:
            # client.change_account("REAL")
            asset_name, asset_data = await client.get_available_asset(order['asset'], force_open=True)
            print(asset_name, asset_data)
            if asset_data[2]:
                print("OK: Asset está aberto.")
                status, buy_info = await client.buy(**order)
                print(status, buy_info)
            else:
                print("ERRO: Asset está fechado.")
            print("Saldo corrente: ", await client.get_balance())
            await asyncio.sleep(2)
    print("\n/", 80 * "=", "/", end="\n")
    print("Saindo...")
    client.close()


async def sell_option():
    check_connect, message = await client.connect()
    if check_connect:
        # client.change_account("REAL")
        amount = 30
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "put"
        duration = 1000  # in seconds
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)
        print(asset_name, asset_data)
        if asset_data[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.buy(amount, asset_name, direction, duration)
            print(status, buy_info)
            await client.sell_option(buy_info["id"])
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    client.close()


async def assets_open():
    check_connect, message = await client.connect()
    if check_connect:
        print("Check Asset Open")
        for i in client.get_all_asset_name():
            print(i)
            print(i, client.check_asset_open(i))
    print("Saindo...")
    client.close()


async def get_candle():
    check_connect, message = await client.connect()
    if check_connect:
        asset = "AUDCAD_otc"
        offset = 86400  # in seconds
        period = 60  # in seconds [5, 10, 15, 30, 60, 120, 180, 240, 300, 600, 900, 1800, 3600, 14400, 86400]
        end_from_time = time.time()
        candles = await client.get_candles(asset, end_from_time, offset, period)
        for candle in candles["data"]:
            print(candle)
    print("Saindo...")
    client.close()


async def get_candle_progressive():
    check_connect, reason = await client.connect()
    if check_connect:
        asset = "EURJPY_otc"
        offset = 86400  # in seconds
        period = 15  # in seconds [5, 10, 15, 30, 60, 120, 180, 240, 300, 600, 900, 1800, 3600, 14400, 86400]
        end_from_time = time.time()
        list_candles = []
        for i in range(5):
            candles = await client.get_candles(asset, end_from_time, offset, period)
            end_from_time = int(candles["data"][0]["time"]) - 1
            list_candles.append(candles["data"])
        print(list_candles)
    print("Saindo...")
    client.close()


async def get_payment():
    check_connect, message = await client.connect()
    if check_connect:
        all_data = client.get_payment()
        for asset_name in all_data:
            asset_data = all_data[asset_name]
            print(asset_name, asset_data["payment"], asset_data["open"])
    print("Saindo...")
    client.close()


async def get_candle_v2():
    check_connect, message = await client.connect()
    if check_connect:
        asset = "EURUSD_otc"
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)
        print(asset_name, asset_data)
        if asset_data[2]:
            print("OK: Asset está aberto.")
            # 60 at 180 seconds
            candles = await client.get_candle_v2(asset, 60)
            print(candles)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    client.close()


async def get_realtime_candle():
    check_connect, message = await client.connect()
    if check_connect:
        list_size = 100
        asset = "USDJPY_otc"
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)
        print(asset_name, asset_data)
        if asset_data[2]:
            print("OK: Asset está aberto.")
            client.start_candles_stream(asset)
            while True:
                prices = client.get_realtime_candles(asset)
                if len(prices[asset]) == list_size:
                    break
            print(prices)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    client.close()


async def get_realtime_sentiment():
    check_connect, message = await client.connect()
    if check_connect:
        asset = "EURUSD_otc"
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)
        print(asset_name, asset_data)
        if asset_data[2]:
            print("OK: Asset está aberto.")
            client.start_candles_stream(asset, 60)
            while True:
                print(await client.get_realtime_sentiment(asset), end="\r")
                await asyncio.sleep(0.5)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    client.close()


async def get_signal_data():
    check_connect, message = await client.connect()
    if check_connect:
        client.start_signals_data()
        while True:
            signals = client.get_signal_data()
            if signals:
                print(json.dumps(signals, indent=4))
            await asyncio.sleep(1)
    print("Saindo...")
    client.close()


async def execute(argument):
    match argument:
        case "test_connection":
            return await test_connection()
        case "get_profile":
            return await get_profile()
        case "get_balance":
            return await get_balance()
        case "get_signal_data":
            return await get_signal_data()
        case "get_payment":
            return await get_payment()
        case "get_candle":
            return await get_candle()
        case "get_candle_v2":
            return await get_candle_v2()
        case "get_candle_progressive":
            return await get_candle_progressive()
        case "get_realtime_candle":
            return await get_realtime_candle()
        case "get_realtime_sentiment":
            return await get_realtime_sentiment()
        case "assets_open":
            return await assets_open()
        case "buy_simple":
            return await buy_simple()
        case "buy_and_check_win":
            return await buy_and_check_win()
        case "buy_multiple":
            return await buy_multiple()
        case "balance_refill":
            return await balance_refill()
        case "help":
            print(f"Uso: {'./app' if getattr(sys, 'frozen', False) else 'python app.py'} <opção>")
            return print(get_all_options())
        case _:
            return print("Opção inválida. Use 'help' para obter a lista de opções.")


async def main():
    if len(sys.argv) != 2:
        # await test_connection()
        # await get_balance()
        # await get_profile()
        # await buy_simple()
        await get_candle()
        return

    option = sys.argv[1]
    await execute(option)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("Encerrando o programa.")
    finally:
        loop.close()

```
