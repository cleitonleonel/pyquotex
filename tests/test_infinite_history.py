import asyncio
import logging
import time

import pytest

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_deep_history():
    email, password = credentials()
    client = Quotex(email=email, password=password)

    print("🚀 Conectando ao PyQuotex...")
    if not await client.connect():
        pytest.skip("Falha na conexão ao PyQuotex")

    asset = "EURUSD"
    period = 60  # 1 min
    # Vamos pedir 2 horas de histórico (7200 segundos)
    # Isso deve forçar pelo menos 2-3 lotes (já que cada lote costuma vir com 50-199 velas)
    amount_of_history = 7200

    def progress(fetched, total, count):
        percent = (fetched / total) * 100
        print(f"⏳ Progresso: {percent:.1f}% | Velas acumuladas: {count}")

    print(f"📊 Buscando histórico profundo para {asset}...")
    candles = await client.get_candles_deep(
        asset=asset,
        amount_of_seconds=amount_of_history,
        period=period,
        progress_callback=progress
    )

    if candles:
        print(f"\n✅ SUCESSO! Obtidas {len(candles)} velas.")
        print(f"📅 De: {time.ctime(candles[0]['time'])}")
        print(f"📅 Até: {time.ctime(candles[-1]['time'])}")
    else:
        print("❌ Falha ao obter histórico profundo.")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_deep_history())
