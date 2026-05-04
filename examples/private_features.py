"""Demonstration of the previously private features:

* Multilogin browser-profile bootstrap
* Custom proxy + DNS overrides
* Advanced sentiment monitoring with anomaly detection

Run with::

    python -m examples.private_features
"""
import asyncio
import logging

from pyquotex.config import credentials
from pyquotex.stable_api import (
    MultiloginConfig,
    ProxyConfig,
    Quotex,
    SentimentThresholds,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    email, password = credentials()

    # 1) Custom proxy with DNS override (skip the system resolver entirely).
    proxy = ProxyConfig(
        url="http://user:pass@proxy.example.com:8080",
        dns_overrides={"qxbroker.com": "1.2.3.4"},
        extra_headers={"X-Forwarded-For": "203.0.113.10"},
    )

    # 2) Optional Multilogin profile – the client fetches the profile's
    #    proxy + UA and inherits them automatically.
    multilogin = MultiloginConfig(
        profile_id="00000000-0000-0000-0000-000000000000",
        folder_id="my-folder",
        token="<bearer-token>",
        api="v3",
        auto_start=False,  # set True to actually launch on connect()
    )

    # 3) Advanced sentiment monitoring.
    thresholds = SentimentThresholds(
        extreme_bullish=0.7,
        extreme_bearish=-0.7,
        spike_zscore=2.5,
    )

    client = Quotex(
        email=email,
        password=password,
        proxy_config=proxy,
        multilogin=multilogin,
        sentiment_thresholds=thresholds,
        enable_sentiment_monitor=True,
    )

    monitor = client.get_sentiment_monitor()
    assert monitor is not None

    async def on_signal(signal):
        print(
            f"[sentiment] {signal.asset} -> {signal.kind} "
            f"bias={signal.snapshot.bias:.2f} detail={signal.detail}"
        )

    monitor.on_signal(on_signal)

    ok, reason = await client.connect()
    if not ok:
        print(f"connect failed: {reason}")
        return

    print(f"balance: {await client.get_balance()}")
    await client.start_realtime_sentiment("EURUSD")
    await asyncio.sleep(60)

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
