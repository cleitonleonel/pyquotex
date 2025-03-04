# examples/monitoring_assets.py

import time
import asyncio
import logging
from quotexapi.config import credentials
from quotexapi.stable_api import Quotex
from quotexapi.utils.processor import process_candles, get_color

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(message)s'
)
logging.disable()


async def get_candle(client, asset, lock):
    async with lock:
        candles_color = []
        offset = 3600  # in seconds
        period = 60  # in seconds
        end_from_time = time.time()
        candles = await client.get_candles(asset, end_from_time, offset, period)
        candles_data = candles

        if len(candles_data) > 0:
            if not candles_data[0].get("open"):
                candles = process_candles(candles_data, period)
                candles_data = candles

            print(asset, candles_data)

            for candle in candles_data:
                color = get_color(candle)
                candles_color.append(color)

        # else:
        #    print(f"{asset} - No candles.")

        print(f"\r{asset} - {time.strftime("%H:%M:%S")}", end="")
        # await asyncio.sleep(0.1)


async def process_all_assets(client, assets):
    lock = asyncio.Lock()
    tasks = [asyncio.create_task(get_candle(client, asset, lock)) for asset in assets]
    await asyncio.gather(*tasks)


async def main():
    email, password = credentials()
    client = Quotex(
        email=email,
        password=password,
        lang="pt",  # Default pt -> Português.
    )
    check_connect, message = await client.connect()
    if check_connect:
        codes_asset = await client.get_all_assets()
        assets = list(codes_asset.keys())[:30]
        start_time = time.time()
        await process_all_assets(client, assets)
        end_time = time.time()
        print(f"Total time: {end_time - start_time}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
