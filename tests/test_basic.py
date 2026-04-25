import asyncio
import logging

import pytest

from pyquotex.stable_api import Quotex

# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')

client = Quotex(
    email="user@gmail.com",
    password="account_password",
    lang="pt"
)


async def buy_simple():
    """
    Tenta comprar um ativo com os parâmetros especificados.
    """
    amount = 50  # Valor na moeda da conta
    asset = "EURUSD"  # Código do ativo
    direction = "call"  # Direção: "call" (alta) ou "put" (baixa)
    duration = 15  # Duração em segundos (tente uma duração menor)

    try:
        # Adicione um delay para garantir a sincronização de tempo
        await asyncio.sleep(1)

        status, buy_info = await client.buy(amount, asset, direction, duration)
        if status:
            print("Compra realizada com sucesso.")
        else:
            print(f"Falha na compra. Informações: {buy_info}")
            logging.error(f"Detalhes do erro: {buy_info}")
    except Exception as e:
        print(f"Ocorreu um erro durante a compra: {e}")
        logging.error(f"Ocorreu um erro durante a compra: {e}")


@pytest.mark.asyncio
async def test_basic():
    check_connect, message = await client.connect()
    if not check_connect:
        pytest.skip(f"Falha na conexão: {message}")
    await buy_simple()


if __name__ == '__main__':
    asyncio.run(test_basic())
