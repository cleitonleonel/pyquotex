"""Demonstration of the previously private features:

* Multilogin browser-profile bootstrap
* Custom proxy + DNS overrides + browser-impersonating TLS (curl_cffi)
* Advanced sentiment monitoring with anomaly detection
* Persistent sentiment history (SQLite) + cross-asset correlation
* Auto-reconnect with subscription replay

Run with::

    python -m examples.private_features

The optional ``stealth`` extra installs ``curl_cffi`` for browser-grade
TLS fingerprints — useful when running behind a Multilogin profile or
residential proxy::

    pip install 'pyquotex[stealth]'
"""
import asyncio
import logging

from pyquotex import (
    MultiloginConfig,
    ProxyConfig,
    Quotex,
    ReconnectPolicy,
    SentimentCorrelationAnalyzer,
    SentimentStore,
    SentimentThresholds,
)
from pyquotex.config import credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    email, password = credentials()

    # 1) Custom proxy with DNS override + browser-impersonating TLS.
    proxy = ProxyConfig(
        url="http://user:pass@proxy.example.com:8080",
        dns_overrides={"qxbroker.com": "1.2.3.4"},
        extra_headers={"X-Forwarded-For": "203.0.113.10"},
        use_browser_tls=True,        # requires `pip install pyquotex[stealth]`
        impersonate="chrome120",
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

    # 3) Advanced sentiment monitoring + persistence.
    store = SentimentStore("sentiment.db")
    thresholds = SentimentThresholds(
        extreme_bullish=0.7,
        extreme_bearish=-0.7,
        spike_zscore=2.5,
    )

    # 4) Auto-reconnect: keep the WS alive across drops and replay
    #    subscriptions + account mode automatically.
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=-1,          # unlimited
        initial_delay=1.0,
        max_delay=60.0,
        backoff_factor=2.0,
        jitter=0.25,
    )

    client = Quotex(
        email=email,
        password=password,
        proxy_config=proxy,
        multilogin=multilogin,
        sentiment_thresholds=thresholds,
        enable_sentiment_monitor=True,
        reconnect_policy=policy,
    )

    # Persist every sentiment sample into the SQLite store.
    monitor = client.get_sentiment_monitor()
    assert monitor is not None
    monitor.store = store

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

    assets = ["EURUSD", "GBPUSD", "USDJPY"]
    for a in assets:
        await client.start_realtime_sentiment(a)

    # Run for a minute and then look for cross-asset divergences.
    await asyncio.sleep(60)

    analyzer = SentimentCorrelationAnalyzer(monitor=monitor)
    matrix = analyzer.correlation_matrix(assets, window=30)
    print("correlation matrix:", matrix)
    divergences = analyzer.find_divergences(assets, window=30, threshold=-0.4)
    print("anti-correlated pairs:", divergences)

    # Reconnect stats are exposed for observability.
    if client.api and client.api.reconnect_supervisor:
        print("reconnect stats:", client.api.reconnect_supervisor.stats)

    await client.close()
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
