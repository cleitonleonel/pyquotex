import os
import time
import asyncio
from quotexapi.stable_api import Quotex

# browser=True enable playwright
client = Quotex(email="example@gmail.com", password="password", browser=False)
client.debug_ws_enable = False


def asset_parse(asset):
    new_asset = asset[:3] + "/" + asset[3:]
    if "_otc" in asset:
        asset = new_asset.replace("_otc", " (OTC)")
    else:
        asset = new_asset
    return asset


def login(attempts=2):
    check, reason = client.connect()
    print("Start your robot")
    attempt = 1
    while attempt < attempts:
        if not client.check_connect():
            print(f"Tentando reconectar, tentativa {attempt} de {attempts}")
            check, reason = client.connect()
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
        time.sleep(0.5)
    return check, reason


def get_balance():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        client.change_account("practice")
        print("Saldo corrente: ", client.get_balance())
        print("Saindo...")
    client.close()


def balance_refill():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        result = client.edit_practice_balance(50000)
        print(result)
    client.close()


def buy():
    check_connect, message = login()
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
            status, buy_info = client.buy(amount, asset, direction, duration)
            print(status, buy_info)
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo corrente: ", client.get_balance())
        print("Saindo...")
    client.close()


async def buy_and_check_win():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        print("Saldo corrente: ", client.get_balance())
        amount = 5
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 30  # in seconds
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            status, buy_info = client.buy(amount, asset, direction, duration)
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
        print("Saldo Atual: ", client.get_balance())
        print("Saindo...")
    client.close()


def sell_option():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        amount = 30
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "put"
        duration = 1000  # in seconds
        status, buy_info = client.buy(amount, asset, direction, duration)
        print(status, buy_info)
        client.sell_option(buy_info["id"])
        print("Saldo corrente: ", client.get_balance())
        print("Saindo...")
    client.close()


def assets_open():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        print("Check Asset Open")
        for i in client.get_all_asset_name():
            print(i)
            print(i, client.check_asset_open(i))
    client.close()


def get_candle():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        asset = "AUDCAD_otc"
        offset = 180  # in seconds
        period = 3600  # in seconds / opcional
        candles = client.get_candles(asset, offset, period)
        for candle in candles["data"]:
            print(candle)
    client.close()


def get_payment():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        all_data = client.get_payment()
        for asset_name in all_data:
            asset_data = all_data[asset_name]
            print(asset_name, asset_data["payment"], asset_data["open"])
    client.close()


def get_candle_v2():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        a = client.get_candle_v2("USDJPY", 10)
        print(a)
    client.close()


def get_realtime_candle():
    check_connect, message = login()
    if check_connect:
        list_size = 10
        client.start_candles_stream("USDJPY_otc", list_size)
        while True:
            if len(client.get_realtime_candles("USDJPY_otc")) == list_size:
                break
        print(client.get_realtime_candles("USDJPY_otc"))
    client.close()


def get_signal_data():
    check_connect, message = login()
    if check_connect:
        while True:
            print(client.get_signal_data())
            time.sleep(1)
    client.close()


# get_signal_data()
# get_balance()
# get_payment()
# get_candle()
# get_candle_v2()
# get_realtime_candle()
# assets_open()
# buy()
asyncio.run(buy_and_check_win())
# balance_refill()
