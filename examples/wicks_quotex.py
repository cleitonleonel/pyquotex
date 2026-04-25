import asyncio
import time
from datetime import datetime

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex

PAIR = "EURUSD_otc"  # par de moedas
TIMEFRAME = 60
EXPIRATION = 1  # minutos
AMOUNT = 700  # valor fixo da aposta


def is_wick_rejection(candle):
    print(candle)
    open_, close, high, low = candle['open'], candle['close'], candle['high'], candle['low']
    body = abs(open_ - close)
    wick_top = high - max(open_, close)
    wick_bottom = min(open_, close) - low

    if body == 0:
        return None  # candle doji, ignorar

    # Pavio inferior longo: possível reversão para cima
    if wick_bottom > body * 2 and wick_top < body:
        return "call"
    # Pavio superior longo: possível reversão para baixo
    elif wick_top > body * 2 and wick_bottom < body:
        return "put"
    return None


async def main():
    email, password = credentials()
    qx = Quotex(email=email, password=password)
    await qx.connect()
    # await qx.authenticate()

    while True:
        candles = await qx.get_candles(PAIR, time.time(), 3600, TIMEFRAME)
        print(f"{datetime.now()} - Obtendo candles para {PAIR}...")
        if not candles or len(candles) < 2:
            continue

        last_candle = candles[-2]  # vela anterior à atual
        direction = is_wick_rejection(last_candle)

        if direction:
            print(f"{datetime.now()} - Sinal de {direction.upper()} detectado")
            opened = await qx.buy(amount=AMOUNT, direction=direction, asset=PAIR, duration=EXPIRATION)
            if opened:
                print(f"Aposta realizada: {direction.upper()} | Preço: {last_candle['close']}")
            await asyncio.sleep(60)  # espera próximo candle
        else:
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
