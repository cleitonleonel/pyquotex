import os
import sys
import json
import random
import asyncio
import pyfiglet
import configparser
from termcolor import colored
import datetime
from pathlib import Path
from quotexapi.stable_api import Quotex
from quotexapi.utils.account_type import AccountType
from quotexapi.utils.operation_type import OperationType
from quotexapi.utils.duration_time import DurationTime
from my_connection import MyConnection

__author__ = "Cleiton Leonel Creton"
__version__ = "1.0.0"

__message__ = f"""
Use com moderação, pois gerenciamento é tudo!
suporte: cleiton.leonel@gmail.com ou +55 (27) 9 9577-2291
"""

# vars global parameters
asset_current = "AUDCAD"

def resource_path(relative_path: str | Path) -> Path:
    """Get absolute path to resource, works for dev and for PyInstaller"""
    base_dir = Path(__file__).parent
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_dir = Path(sys._MEIPASS)
    return base_dir / relative_path


config_path = Path(os.path.join(".", "settings/config.ini"))
if not config_path.exists():
    config_path.parent.mkdir(exist_ok=True, parents=True)
    text_settings = (f"[settings]\n"
                     f"email={input('Insira o e-mail da conta: ')}\n"
                     f"password={input('Insira a senha da conta: ')}\n"
                     f"email_pass={input('Insira a senha da conta de e-mail: ')}\n"
                     f"user_data_dir={input('Insira um caminho para o profile do browser: ')}\n"
                     )
    config_path.write_text(text_settings)

config = configparser.ConfigParser()

config.read(config_path, encoding="utf-8")

custom_font = pyfiglet.Figlet(font="ansi_shadow")
ascii_art = custom_font.renderText("PyQuotex")
art_effect = f"""{ascii_art}

        author: {__author__} versão: {__version__}
        {__message__}
"""

print(art_effect)

email = config.get("settings", "email")
password = config.get("settings", "password")
email_pass = config.get("settings", "email_pass")
user_data_dir = config.get("settings", "user_data_dir")

if not email.strip() or not password.strip():
    print("E-mail e Senha não podem estar em branco...")
    sys.exit()
if user_data_dir.strip():
    user_data_dir = "browser/instance/quotex.default"

client = Quotex(email=email,
                password=password,
                email_pass=email_pass,
                user_data_dir=Path(os.path.join(".", user_data_dir))
                )


# client.debug_ws_enable = True

# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')


# PRACTICE mode is default / REAL mode is optional
# client.set_account_mode("REAL")


def get_all_options():
    return """Opções disponíveis:
    - get_profile
    - get_balance
    - get_signal_data
    - get_payment
    - get_candle
    - get_candle_v2
    - get_realtime_candle
    - get_realtime_sentiment
    - assets_open
    - buy_simple
    - buy_and_check_win
    - buy_multiple
    - balance_refill
    - help
    """


def asset_parse(asset):
    new_asset = asset[:3] + "/" + asset[3:]
    if "_otc" in asset:
        asset = new_asset.replace("_otc", " (OTC)")
    else:
        asset = new_asset
    return asset

async def get_balance():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    prepare_connection.close()


async def get_profile():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        profile = await client.get_profile()
        description = (f"\nUsuário: {profile.nick_name}\n"
                       f"Saldo Demo: {profile.demo_balance}\n"
                       f"Saldo Real: {profile.live_balance}\n"
                       f"Id: {profile.profile_id}\n"
                       f"Avatar: {profile.avatar}\n"
                       f"País: {profile.country_name}\n"
                       )
        print(description)
    print("Saindo...")
    prepare_connection.close()


async def balance_refill():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        result = await client.edit_practice_balance(5000)
        print(result)
    prepare_connection.close()


async def buy_simple():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        amount = 50
        
        asset, asset_open = check_asset(asset_current)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.trade(OperationType.CALL_GREEN, amount, asset, DurationTime.ONE_MINUTE)
            print(status, buy_info)
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    prepare_connection.close()


async def buy_and_check_win():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    #check_connect, message = await connect()
    if check_connect:
        print("Saldo corrente: ", await client.get_balance())
        amount = 50
        asset, asset_open = check_asset(asset_current)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            status, buy_info = await client.trade(
                    OperationType.CALL_GREEN, amount, asset, DurationTime.ONE_MINUTE
                )
            print(status, buy_info)
            if status:
                print("Aguardando resultado...")
                if await client.check_win(buy_info["id"]):
                    print(f"\nWin!!! \nVencemos moleque!!!\nLucro: $ {client.get_profit()}")
                else:
                    print(f"\nLoss!!! \nPerdemos moleque!!!\nPrejuízo: $ {client.get_profit()}")
            else:
                print("Falha na operação!!!")
        else:
            print("ERRO: Asset está fechado.")
        print("Saldo Atual: ", await client.get_balance())
    print("Saindo...")

    prepare_connection.close()

def check_asset(asset):
    asset_query = asset_parse(asset)
    asset_open = client.check_asset_open(asset_query)
    if not asset_open[2]:
        print(colored("[WARN]: ", "blue"), "Asset is closed.")
        asset = f"{asset}_otc"
        print(colored("[WARN]: ", "blue"), "try OTC Asset -> " + asset)
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
    return asset, asset_open

async def wait_for_input_exceeding_x_seconds_limit(secounds=30):
    while True:
        now = datetime.datetime.now()
        if now.second < secounds:
            return  # Returns when it's the right time to proceed
        await asyncio.sleep(0.5)

async def buy_multiple(orders=10):
    order_list = [
        {"action": OperationType.PUT_RED, "amount": 5, "asset": "EURUSD_otc", "direction": "call", "duration": 60},
        {"action": OperationType.CALL_GREEN, "amount": 10, "asset": "AUDCAD_otc", "direction": "put", "duration": 60},
        {"action": OperationType.PUT_RED, "amount": 15, "asset": "AUDJPY_otc", "direction": "call", "duration": 60},
        {"action": OperationType.CALL_GREEN, "amount": 20, "asset": "AUDUSD_otc", "direction": "put", "duration": 60},
        {"action": OperationType.PUT_RED, "amount": 25, "asset": "CADJPY_otc", "direction": "call", "duration": 60},
        {"action": OperationType.CALL_GREEN, "amount": 30, "asset": "EURCHF_otc", "direction": "put", "duration": 60},
        {"action": OperationType.PUT_RED, "amount": 35, "asset": "EURGBP_otc", "direction": "call", "duration": 60},
        {"action": OperationType.CALL_GREEN, "amount": 40, "asset": "EURJPY_otc", "direction": "put", "duration": 60},
        {"action": OperationType.PUT_RED, "amount": 45, "asset": "GBPAUD_otc", "direction": "call", "duration": 60},
        {"action": OperationType.CALL_GREEN, "amount": 50, "asset": "GBPJPY_otc", "direction": "put", "duration": 60},
    ]
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    for i in range(0, orders):
        print("\n/", 80 * "=", "/", end="\n")
        print(f"ABRINDO ORDEM: {i + 1}")
        order = random.choice(order_list)
        print(order)
        if check_connect:
            asset_query = asset_parse(order["asset"])
            asset, asset_open = check_asset(asset_query)
            if asset_open[2]:
                print("OK: Asset está aberto.")
                status, buy_info = await client.trade(**order)
                print(status, buy_info)
            else:
                print("ERRO: Asset está fechado.")
            print("Saldo corrente: ", await client.get_balance())
            await asyncio.sleep(2)
    print("\n/", 80 * "=", "/", end="\n")
    print("Saindo...")
    prepare_connection.close()


async def sell_option():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        amount = 30
        asset, asset_open = check_asset(asset_current)        
        status, sell_info = await client.trade(OperationType.PUT_RED, amount, asset, DurationTime.ONE_MINUTE)
        print(status, sell_info)
        await client.sell_option(sell_info["id"])
        print("Saldo corrente: ", await client.get_balance())
    print("Saindo...")
    prepare_connection.close()


async def assets_open():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        print("Check Asset Open")
        for i in client.get_all_asset_name():
            print(i)
            print(i, client.check_asset_open(i))
    print("Saindo...")
    prepare_connection.close()


async def get_candle():
    check_connect, message = await connect()
    if check_connect:
        asset = "AUDCAD_otc"
        # 60 at 86400
        offset = 180  # in seconds
        period = 86400  # in seconds / opcional
        candles = await client.get_candles(asset, offset, period)
        for candle in candles["data"]:
            print(candle)
    print("Saindo...")
    prepare_connection.close()


async def get_payment():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        all_data = client.get_payment()
        for asset_name in all_data:
            asset_data = all_data[asset_name]
            print(asset_name, asset_data["payment"], asset_data["open"])
    print("Saindo...")
    prepare_connection.close()


async def get_candle_v2():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        asset, asset_open = check_asset(asset_current)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            # 60 at 180 seconds
            candles = await client.get_candle_v2(asset, 60)
            print(candles)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    prepare_connection.close()


async def get_realtime_candle():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        list_size = 10
        asset = "USDJPY_otc"
        asset_query = asset_parse(asset)
        asset_open = client.check_asset_open(asset_query)
        if asset_open[2]:
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
    prepare_connection.close()


async def get_realtime_sentiment():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        asset, asset_open = check_asset(asset_current)
        if asset_open[2]:
            print("OK: Asset está aberto.")
            client.start_candles_stream(asset)
            while True:
                print(client.get_realtime_sentiment(asset), end="\r")
                await asyncio.sleep(0.5)
        else:
            print("ERRO: Asset está fechado.")
    print("Saindo...")
    prepare_connection.close()


async def get_signal_data():
    prepare_connection = MyConnection(client)
    check_connect, message = await prepare_connection.connect()
    if check_connect:
        client.start_signals_data()
        while True:
            signals = client.get_signal_data()
            if signals:
                print(json.dumps(signals, indent=4))
            await asyncio.sleep(1)
    print("Saindo...")
    prepare_connection.close()


async def main():
    
    if len(sys.argv) != 2:
        print(f"Uso: {'./main' if getattr(sys, 'frozen', False) else 'python main.py'} <opção>")
        sys.exit(1)

    async def execute(argument):
        match argument:
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
                return print(get_all_options())
            case _:
                return print("Opção inválida. Use 'help' para obter a lista de opções.")

    option = sys.argv[1]
    await execute(option)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
        # loop.run_forever()
    except KeyboardInterrupt:
        print("Encerrando o programa.")
    finally:
        loop.close()
