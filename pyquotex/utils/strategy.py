"""Triple Confirmation trading strategy for PyQuotex.

Usage
-----
    from pyquotex.utils.strategy import TripleConfirmationStrategy

    strategy = TripleConfirmationStrategy(
        client=client,
        asset="EURUSD",
        period=60,
    )
    await strategy.run(auto_trade=False)   # signal-only (safe)
    await strategy.run(auto_trade=True)    # auto-trade DEMO only
"""
import asyncio
import logging
import time
from typing import Any

from .indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class TripleConfirmationStrategy:
    """Professional Trading Strategy: Triple Confirmation.

    Combines three independent confirmation layers before signalling a trade:

    1. **Trend** — EMA-20 / EMA-50 alignment
    2. **Momentum** — RSI-7 directional bias
    3. **Entry timing** — Stochastic K/D crossover in extreme zone

    Parameters
    ----------
    client : Quotex
        An already-connected ``stable_api.Quotex`` instance.
    asset : str
        Asset symbol, e.g. ``"EURUSD"`` or ``"EURUSD_otc"``.
    period : int
        Candle period in seconds (default 60).
    amount : float
        Trade amount per signal (default 1.0).
    rsi_period : int
        RSI look-back period (default 7).
    ema_fast : int
        Fast EMA period (default 20).
    ema_slow : int
        Slow EMA period (default 50).
    stoch_k : int
        Stochastic %K period (default 14).
    stoch_d : int
        Stochastic %D period (default 3).
    """

    def __init__(
            self,
            client: Any,
            asset: str = "EURUSD",
            period: int = 60,
            amount: float = 1.0,
            rsi_period: int = 7,
            ema_fast: int = 20,
            ema_slow: int = 50,
            stoch_k: int = 14,
            stoch_d: int = 3,
    ) -> None:
        self.client = client
        self.asset = asset
        self.period = period
        self.amount = amount
        self.rsi_period = rsi_period
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.stoch_k = stoch_k
        self.stoch_d = stoch_d
        self.indicators = TechnicalIndicators()
        self._min_candles = ema_slow + 5

    # ------------------------------------------------------------------
    # Core analysis (pure, no I/O)
    # ------------------------------------------------------------------

    def analyze(self, candles: list[dict[str, Any]]) -> str | None:
        """Analyze candle data and return ``'call'``, ``'put'``, or ``None``.

        Parameters
        ----------
        candles : list[dict]
            Candle dicts with ``open``, ``close``, ``high``, ``low`` keys.

        Returns
        -------
        str or None
            ``'call'`` / ``'put'`` when all three confirmations align,
            ``None`` otherwise.
        """
        if len(candles) < self._min_candles:
            return None

        closes = [float(c["close"]) for c in candles]
        highs  = [float(c.get("high", c.get("max", c["close"]))) for c in candles]
        lows   = [float(c.get("low",  c.get("min", c["close"]))) for c in candles]

        # 1. EMA trend
        ema20 = self.indicators.calculate_ema(closes, self.ema_fast)
        ema50 = self.indicators.calculate_ema(closes, self.ema_slow)
        if not ema20 or not ema50:
            return None

        price   = closes[-1]
        uptrend   = price > ema20[-1] > ema50[-1]
        downtrend = price < ema20[-1] < ema50[-1]

        # 2. RSI momentum
        rsi = self.indicators.calculate_rsi(closes, self.rsi_period)
        if not rsi:
            return None
        last_rsi = rsi[-1]

        # 3. Stochastic entry
        stoch = self.indicators.calculate_stochastic(
            closes, highs, lows, self.stoch_k, self.stoch_d
        )
        if not stoch["d"] or len(stoch["k"]) < 2 or len(stoch["d"]) < 2:
            return None

        k_now, k_prev = stoch["k"][-1], stoch["k"][-2]
        d_now, d_prev = stoch["d"][-1], stoch["d"][-2]

        # CALL: uptrend + RSI > 50 + bullish K/D cross below 30
        if uptrend and last_rsi > 50:
            if k_prev < d_prev and k_now > d_now and d_now < 30:
                return "call"

        # PUT: downtrend + RSI < 50 + bearish K/D cross above 70
        if downtrend and last_rsi < 50:
            if k_prev > d_prev and k_now < d_now and d_now > 70:
                return "put"

        return None

    # ------------------------------------------------------------------
    # Async runner
    # ------------------------------------------------------------------

    async def run(
            self,
            auto_trade: bool = False,
            poll_interval: float = 5.0,
    ) -> None:
        """Run the strategy loop indefinitely (stop with Ctrl+C).

        Parameters
        ----------
        auto_trade : bool
            When ``True``, automatically places trades on signals.
            **Use DEMO account only.**
        poll_interval : float
            Seconds to wait between candle checks (default 5.0).
        """
        logger.info(
            "TripleConfirmation started — asset=%s period=%ds "
            "auto_trade=%s",
            self.asset, self.period, auto_trade,
        )

        # Resolve OTC fallback once
        asset, info = await self.client.get_available_asset(
            self.asset, force_open=True
        )
        if not info or not info[0]:
            logger.error("Asset %s is not available.", self.asset)
            return

        await self.client.start_candles_stream(asset, self.period)

        try:
            while True:
                candles = await self.client.get_candles(
                    asset,
                    time.time(),
                    self.period * (self._min_candles + 10),
                    self.period,
                )

                if not candles or len(candles) < self._min_candles:
                    logger.debug("Not enough candles yet (%d).", len(candles) if candles else 0)
                    await asyncio.sleep(poll_interval)
                    continue

                signal = self.analyze(candles)

                if signal:
                    logger.info(
                        "Signal: %s | price=%.5f | candles=%d",
                        signal.upper(), float(candles[-1]["close"]), len(candles),
                    )
                    print(
                        f"  🟢 CALL" if signal == "call" else "  🔴 PUT",
                        f"  {asset}  @{candles[-1]['close']}",
                    )

                    if auto_trade:
                        status, data = await self.client.buy(
                            self.amount, asset, signal, self.period
                        )
                        if status:
                            trade_id = (data or {}).get("id", "?")
                            logger.info("Trade placed: id=%s", trade_id)
                            print(f"  ✅ Trade placed — id={trade_id}")
                        else:
                            logger.warning("Trade failed: %s", data)
                            print(f"  ❌ Trade failed: {data}")
                else:
                    logger.debug("No signal.")

                await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass
        finally:
            await self.client.stop_candles_stream(asset)
            logger.info("TripleConfirmation stopped.")
