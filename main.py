from quotexapi.stable_api import Quotex

client = Quotex(email="user@gmail.com", password="password")
client.debug_ws_enable = False


def login():
    client.debug_ws_enable = False
    return client.connect()


def get_balance():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        print("Saldo corrente: ", client.get_balance())
        print("Saindo...")
    client.close()


def balance_refill():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        result = client.edit_practice_balance(10000)
        print(result)
    client.close()


def buy():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        amount = 30
        asset = "EURUSD_otc"  # "EURUSD_otc"
        direction = "call"
        duration = 10  # in seconds
        status, buy_info = client.buy(amount, asset, direction, duration)
        print(status, buy_info)
        print("Saldo corrente: ", client.get_balance())
        print("Saindo...")
    client.close()


def buy_and_check_win():
    check_connect, message = login()
    print("CONEXÃO: ", check_connect, message)
    if check_connect:
        client.change_account("PRACTICE")
        print("Saldo corrente: ", client.get_balance())
        amount = 1500
        asset = "AUDCAD"  # "EURUSD_otc"
        direction = "call"
        duration = 60  # in seconds
        status, buy_info = client.buy(amount, asset, direction, duration)
        # print(status, buy_info)
        if status:
            print("Aguardando resultado...")
            if client.check_win(buy_info["id"]):
                print(f"\nWin!!! \nVencemos moleque!!!\nLucro: R$ {client.get_profit()}")
            else:
                print(f"\nLoss!!! \nPerdemos moleque!!!\nPrejuízo: R$ {client.get_profit()}")
        else:
            print("Falha na operação!!!")
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
        direction = "call"
        duration = 1000  # in seconds
        status, buy_info = client.buy(amount, asset, direction, duration)
        print(status, buy_info)
        client.sell_option(buy_info["id"])
        print("Saldo corrente: ", client.get_balance())
        print("Saindo...")
    client.close()


def asset_open():
    check_connect, message = login()
    print(check_connect, message)
    if check_connect:
        print("Check Asset Open")
        for i in client.get_all_asset_name():
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


# NÃO IMPLEMENTADO AINDA.
def get_candle_v2():
    check_connect, message = login()
    print(check_connect, message)
    print(check_connect, message)
    if check_connect:
        print("\n\n------get")
        a = client.get_candle_v2("NZDUSD_otc", 180)
        print(a)
    client.close()


# NÃO IMPLEMENTADO AINDA.
def get_realtime_candle():
    check_connect, message = login()
    if check_connect:
        list_size = 10
        client.start_candles_stream("NZDUSD_otc", list_size)
        while True:
            if len(client.get_realtime_candles("NZDUSD_otc")) == list_size:
                break
        print(client.get_realtime_candles("NZDUSD_otc"))
    client.close()


get_balance()
# get_candle()
# asset_open()
# buy_and_check_win()
# balance_refill()
