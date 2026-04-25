import asyncio
import configparser
import logging

import pytest

from pyquotex.stable_api import Quotex

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')


@pytest.mark.asyncio
async def test_win():
    # Load credentials
    config = configparser.ConfigParser()
    config.read("settings/config.ini")
    email = config.get("settings", "email")
    password = config.get("settings", "password")

    client = Quotex(email, password)

    print("Conectando para teste de Check Win...")
    check, reason = await client.connect()

    if not check:
        pytest.skip(f"Erro ao conectar: {reason}")

    asset = "EURUSD"
    amount = 5
    action = "call"  # Compra
    duration = 60  # 60 segundos

    print(f"\nRealizando compra de {duration}s em {asset}...")
    check_buy, order_id = await client.buy(amount, asset, action, duration)

    if check_buy:
        # Extract ID if buy returns a dict
        order_id_str = order_id.get("id") if isinstance(order_id, dict) else order_id
        print(f"Compra realizada com sucesso! ID: {order_id_str}")
        print("Aguardando 60 segundos para o resultado (usando check_win)...")

        # O check_win fica em loop aguardando a corretora fechar a ordem
        status, profit = await client.check_win(order_id_str)

        print("\n" + "=" * 30)
        print(f"RESULTADO FINAL: {status.upper()}")
        print(f"LUCRO: ${profit:.2f}")
        print("=" * 30)
    else:
        print(f"Erro ao realizar compra: {order_id}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_win())
