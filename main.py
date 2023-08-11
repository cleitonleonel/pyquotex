import os
import time
import asyncio
from quotexapi.stable_api import Quotex

client = Quotex(email="user@gmail.com", password="password")
client.debug_ws_enable = False


def asset_parse(asset):
    new_asset = asset[:3] + "/" + asset[3:]
    if "_otc" in asset:
        asset = new_asset.replace("_otc", " (OTC)")
    else:
        asset = new_asset
    return asset


async def login(attempts=5):
    check, reason = await client.connect()
    print("Start your robot")
    attempt = 1
    while attempt <= attempts:
        if not client.check_connect():
            print(f"Tentando reconectar, tentativa {attempt} de {attempts}")
            check, reason = await client.connect()
            if check:
                print("Reconectado com sucesso!!!")
                break
            else:
                print("Erro ao reconectar.")
                attempt += 1
                if os.path.isfile("session.json"):
                    os.remove("session.json")
        elif not check:
            attempt += 1
        else:
            break
        await asyncio.sleep(5)
    return check, reason


async def get_balance():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        client.change_account("practice")
        print("Saldo corrente: ", await client.get_balance())
        print("Saindo...")
    client.close()


async def balance_refill():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        result = await client.edit_practice_balance(1000)
        print(result)
    client.close()


async def buy():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        amount = 5
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 60  # in seconds
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.buy(amount, asset, direction, duration)
            print(status, buy_info)
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo corrente: ", await client.get_balance())
        print("Saindo...")
    client.close()


async def buy_and_check_win():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        print("Saldo corrente: ", await client.get_balance())
        amount = 5
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 60  # in seconds
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.buy(amount, asset, direction, duration)
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


async def sell_option():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        amount = 30
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "put"
        duration = 1000  # in seconds
        status, buy_info = await client.buy(amount, asset, direction, duration)
        print(status, buy_info)
        await client.sell_option(buy_info["id"])
        print("Saldo corrente: ", await client.get_balance())
        print("Saindo...")
    client.close()


async def assets_open():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        print("Check Asset Open")
        for i in client.get_all_asset_name():
            print(i)
            print(i, client.check_asset_open(i))
    client.close()


async def get_candle():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        asset = "AUDCAD_otc"
        offset = 180  # in seconds
        period = 3600  # in seconds / opcional
        candles = await client.get_candles(asset, offset, period)
        for candle in candles["data"]:
            print(candle)
    client.close()


async def get_payment():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        all_data = client.get_payment()
        for asset_name in all_data:
            asset_data = all_data[asset_name]
            print(asset_name, asset_data["payment"], asset_data["open"])
    client.close()


async def get_candle_v2():
    check_connect, message = await login()
    print(check_connect, message)
    if check_connect:
        candles = await client.get_candle_v2("USDJPY", 180)
        print(candles)
    client.close()


async def get_realtime_candle():
    check_connect, message = await login()
    if check_connect:
        list_size = 10
        client.start_candles_stream("USDJPY_otc", list_size)
        while True:
            if len(client.get_realtime_candles("USDJPY_otc")) == list_size:
                break
        print(client.get_realtime_candles("USDJPY_otc"))
    client.close()


async def get_signal_data():
    check_connect, message = await login()
    if check_connect:
        while True:
            print(client.get_signal_data())
            time.sleep(1)
    client.close()


async def main():
    # await get_balance()
    # await get_signal_data()
    # await get_payment()
    # await get_candle()
    await get_candle_v2()
    # await get_realtime_candle()
    # await assets_open()
    # await buy()
    # await buy_and_check_win()
    # await balance_refill()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    # loop.run_forever()
