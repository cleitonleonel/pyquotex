import asyncio
import logging

import pytest

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex

logging.basicConfig(level=logging.DEBUG)

@pytest.mark.asyncio
async def test_buy():
    email, password = credentials()

    if not email or not password:
        pytest.skip("Email or password not found in config.ini")

    client = Quotex(email=email, password=password)

    try:
        check, reason = await client.connect()
        if not check:
            pytest.skip(f"Falha na conexão: {reason}")

        client.set_account_mode("PRACTICE")
        balance = await client.get_balance()
        assert balance is not None

    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(test_buy())
