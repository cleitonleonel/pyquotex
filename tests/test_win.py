import asyncio
import configparser
import logging
import os

import pytest

from pyquotex.stable_api import Quotex

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

@pytest.mark.asyncio
async def test_win():
    # Load credentials
    config = configparser.ConfigParser()
    config_path = "settings/config.ini"

    if not os.path.exists(config_path):
        pytest.skip("config.ini not found")

    config.read(config_path)

    try:
        email = config.get("settings", "email")
        password = config.get("settings", "password")
    except (configparser.NoSectionError, configparser.NoOptionError):
        pytest.skip("Credentials not found in config.ini")

    client = Quotex(email, password)

    try:
        print("Conectando para teste de Check Win...")
        check, reason = await client.connect()

        if not check:
            pytest.skip(f"Erro ao conectar: {reason}")

        asset = "EURUSD"
        amount = 5
        action = "call"
        duration = 60

        asset, data = await client.get_available_asset(asset, force_open=True)
        if not data or not data[0]:
            pytest.skip(
                f"Asset {asset} not found or closed. Please check the asset name and availability."
            )

        print(f"\nRealizando compra de {duration}s em {asset}...")
        try:
            # Note: We skip if buy fails because it might be due to market closed
            check_buy, order_info = await client.buy(amount, asset, action, duration)
        except TimeoutError:
            pytest.skip(f"Timeout ao realizar compra em {asset} (mercado provavelmente fechado)")

        if check_buy:
            order_id = order_info.get("id") if isinstance(order_info, dict) else order_info
            print(f"Compra realizada! ID: {order_id}")
            try:
                status, profit = await client.check_win(order_id)
                print(f"Resultado: {status} | Lucro: {profit}")
            except TimeoutError:
                pytest.skip("Timeout waiting for trade result (market might be slow or closed)")
        else:
            pytest.skip(f"Erro ao realizar compra (provavelmente mercado fechado): {order_info}")

    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(test_win())
