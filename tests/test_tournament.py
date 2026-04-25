import pytest

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex


@pytest.mark.asyncio
async def test_tournament_account_example():
    """
    Example of how to use a tournament account in demo mode.
    
    To use a tournament, you must provide the tournament_id.
    Tournament accounts are always considered 'DEMO' (practice) type.
    """
    email, password = credentials()
    client = Quotex(email=email, password=password)

    # Connect
    check, reason = await client.connect()
    if not check:
        pytest.skip(f"Failed to connect to Quotex: {reason}")

    try:
        # ID do torneio (você obtém isso na plataforma)
        # Exemplo fictício: 12345
        TOURNAMENT_ID = 12345

        print(f"Switching to tournament account {TOURNAMENT_ID}...")

        # Modo PRACTICE com o ID do torneio
        await client.change_account("PRACTICE", tournament_id=TOURNAMENT_ID)

        # Agora qualquer compra ou consulta de saldo usará o ID do torneio
        balance = await client.get_balance()
        print(f"Tournament Balance: {balance}")

        # Exemplo de compra no torneio
        # status, info = await client.buy(10, "EURUSD", "call", 60)

    finally:
        await client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_tournament_account_example())
