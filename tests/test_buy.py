import asyncio
import configparser
import logging
import os

import pytest

from pyquotex.stable_api import Quotex

logging.basicConfig(level=logging.DEBUG)


@pytest.mark.asyncio
async def test_buy():
    # Read credentials from config.ini
    config = configparser.ConfigParser()
    config_path = os.path.join("settings", "config.ini")
    config.read(config_path)

    email = config.get("settings", "email")
    password = config.get("settings", "password")

    client = Quotex(email=email, password=password)

    print("Tentando conectar...")
    check, reason = await client.connect()

    if not check:
        pytest.skip(f"Falha na conexão: {reason}")

    print("Conectado com sucesso!")
    client.set_account_mode("PRACTICE")

    balance = await client.get_balance()
    print(f"Saldo Inicial (PRACTICE): {balance}")

    asset = "EURUSD"
    amount = 5
    direction = "call"  # "call" para compra, "put" para venda
    duration = 60  # 1 minuto

    print(f"Realizando compra: {asset} | {direction} | ${amount}")
    status, buy_info = await client.buy(amount, asset, direction, duration)

    if status:
        print(f"Compra realizada! ID: {buy_info.get('id')}")
        print("Aguardando resultado...")
        win_status, profit = await client.check_win(buy_info.get('id'))
        print(f"Resultado: {win_status} | Lucro: {profit}")
    else:
        print(f"Erro na compra: {buy_info}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_buy())
