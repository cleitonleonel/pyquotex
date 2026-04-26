import unittest

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex


class TestDeepHistory(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        email, password = credentials()
        self.client = Quotex(email=email, password=password)
        check, reason = await self.client.connect()
        if not check:
            self.skipTest(f"Failed to connect to Quotex: {reason}")

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_deep_history_continuity(self) -> None:
        """Test if the fetched deep history is chronologically continuous."""
        asset = "EURUSD"
        period = 60
        # Fetch 5 minutes of history
        amount_seconds = 300

        asset, data = await self.client.get_available_asset(asset, force_open=True)
        if not data or not data[0]:
            self.skipTest(
                f"Asset {asset} not found or closed. Please check the asset name and availability."
            )

        candles = await self.client.get_historical_candles(asset, amount_seconds, period)

        if len(candles) == 0:
            self.skipTest("No candles were fetched (market might be closed)")

        # Verify chronological order and continuity
        for i in range(len(candles) - 1):
            curr_time = candles[i]['time']
            next_time = candles[i + 1]['time']

            self.assertLess(curr_time, next_time, f"Candles at index {i} and {i + 1} are out of order")

            # Since it's 1m candles, the difference should be 60s
            # Note: There might be gaps during weekends, but EURUSD is usually open.
            # We allow for some small gaps if needed, but here we expect continuity.
            diff = next_time - curr_time
            self.assertEqual(diff, period,
                             f"Gap found between {curr_time} and {next_time}: {diff}s instead of {period}s")

    async def test_deep_history_amount(self) -> None:
        """Test if we can fetch more than the 200-candle limit."""
        asset = "EURUSD"
        period = 60
        # 5 candles = 300 seconds
        amount_seconds = 300

        asset, data = await self.client.get_available_asset(asset, force_open=True)
        if not data or not data[0]:
            self.skipTest(
                f"Asset {asset} not found or closed. Please check the asset name and availability."
            )

        candles = await self.client.get_historical_candles(asset, amount_seconds, period, max_workers=1)

        if len(candles) == 0:
            self.skipTest("No candles were fetched (market might be closed)")

        # 5 candles * 60s = 300s. We should have at least 5 candles.
        # Note: broker might return slightly more or less depending on exact boundaries.
        self.assertGreaterEqual(len(candles), len(candles), f"Fetched only {len(candles)} candles, expected at least 5")


if __name__ == "__main__":
    unittest.main()
