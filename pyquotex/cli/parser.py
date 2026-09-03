"""argparse parser construction for the pyquotex CLI."""

import argparse


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyquotex",
        description="⚡ PyQuotex — Complete Quotex trading API CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  pyquotex login --demo\n"
            "  pyquotex balance --live\n"
            "  pyquotex assets\n"
            "  pyquotex payout\n"
            "  pyquotex payout-asset --asset EURUSD --timeframe 1\n"
            "  pyquotex candles --asset EURUSD --period 60 --count 10\n"
            "  pyquotex candles-v2 --asset EURUSD --period 60\n"
            "  pyquotex candles-deep --asset EURUSD --seconds 3600 --workers 5\n"
            "  pyquotex history-line --asset EURUSD --offset 3600\n"
            "  pyquotex candle-info --asset EURUSD --period 60\n"
            "  pyquotex realtime-price --asset EURUSD\n"
            "  pyquotex realtime-sentiment --asset EURUSD\n"
            "  pyquotex realtime-candle --asset EURUSD --period 60\n"
            "  pyquotex buy --asset EURUSD --amount 5 --direction call --duration 60 --check-win\n"
            "  pyquotex sell --id TRADE_ID\n"
            "  pyquotex pending --asset EURUSD --amount 10 --direction call --duration 60\n"
            "  pyquotex check --id TRADE_ID\n"
            "  pyquotex result --id OPERATION_ID\n"
            "  pyquotex history --pages 2\n"
            "  pyquotex signals\n"
            "  pyquotex indicator --asset EURUSD --name RSI --period 14\n"
            "  pyquotex server-time\n"
            "  pyquotex set-demo-balance --amount 10000\n"
            "  pyquotex settings --asset EURUSD --period 60\n"
            "  pyquotex monitor --asset EURUSD\n"
            "  pyquotex strategy --asset EURUSD --auto-trade\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── helpers ─────────────────────────────────────────────────────────────
    def _add_account_flags(p: argparse.ArgumentParser) -> None:
        g = p.add_mutually_exclusive_group()
        g.add_argument(
            "--demo", action="store_true", default=True, help="Use demo account (default)"
        )
        g.add_argument("--live", action="store_true", help="Use live account")

    def _add_asset_flag(p: argparse.ArgumentParser, default: str = "EURUSD") -> None:
        p.add_argument("--asset", default=default, help=f"Asset symbol (default: {default})")

    # ── test-all ─────────────────────────────────────────────────────────────
    sub.add_parser("test-all", help="Run all tests")

    # ── login ────────────────────────────────────────────────────────────────
    p = sub.add_parser("login", help="Test connection and show profile + balance")
    _add_account_flags(p)

    # ── balance ──────────────────────────────────────────────────────────────
    p = sub.add_parser("balance", help="Show account balance")
    _add_account_flags(p)

    # ── server-time ──────────────────────────────────────────────────────────
    sub.add_parser("server-time", help="Show the current synced server timestamp")

    # ── set-demo-balance ─────────────────────────────────────────────────────
    p = sub.add_parser("set-demo-balance", help="Refill or set demo (practice) account balance")
    p.add_argument("--amount", type=float, default=10000.0, help="Amount to set (default: 10000)")

    # ── settings ─────────────────────────────────────────────────────────────
    p = sub.add_parser("settings", help="Apply trading-UI settings and show result")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    p.add_argument(
        "--mode", choices=["TIMER", "TURBO"], default="TIMER", help="Time mode (default: TIMER)"
    )
    p.add_argument("--deal", type=int, default=5, help="Default deal amount (default: 5)")
    _add_account_flags(p)

    # ── assets ───────────────────────────────────────────────────────────────
    sub.add_parser("assets", help="List all available assets")

    # ── payout ───────────────────────────────────────────────────────────────
    sub.add_parser("payout", help="Show payout %% for all assets")

    # ── payout-asset ─────────────────────────────────────────────────────────
    p = sub.add_parser("payout-asset", help="Show payout %% for a specific asset")
    _add_asset_flag(p)
    p.add_argument(
        "--timeframe",
        default="1",
        choices=["1", "5", "24", "all"],
        help="Timeframe in minutes, or 'all' (default: 1)",
    )

    # ── candles ──────────────────────────────────────────────────────────────
    p = sub.add_parser("candles", help="Fetch latest candle data (≤199)")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    p.add_argument(
        "--count", type=int, default=10, help="Number of candles to display (default: 10)"
    )
    _add_account_flags(p)

    # ── candles-v2 ───────────────────────────────────────────────────────────
    p = sub.add_parser("candles-v2", help="Fetch candles via the v2 API path")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── candles-deep ─────────────────────────────────────────────────────────
    p = sub.add_parser("candles-deep", help="Fetch deep historical candle data (parallel workers)")
    _add_asset_flag(p)
    p.add_argument(
        "--seconds", type=int, default=3600, help="Total history window in seconds (default: 3600)"
    )
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    p.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Parallel workers 2-10 (default: 5). WARNING: >10 may cause a ban.",
    )
    p.add_argument("--output", metavar="FILE", help="Save results to a CSV file")
    _add_account_flags(p)

    # ── history-line ─────────────────────────────────────────────────────────
    p = sub.add_parser("history-line", help="Fetch raw historical price-line data")
    _add_asset_flag(p)
    p.add_argument(
        "--offset", type=int, default=3600, help="History window in seconds (default: 3600)"
    )
    _add_account_flags(p)

    # ── candle-info ──────────────────────────────────────────────────────────
    p = sub.add_parser(
        "candle-info", help="Show opening / closing / remaining time of current candle"
    )
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── realtime-price ───────────────────────────────────────────────────────
    p = sub.add_parser("realtime-price", help="Stream live price data for an asset")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── realtime-sentiment ───────────────────────────────────────────────────
    p = sub.add_parser("realtime-sentiment", help="Stream live trader-sentiment data")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── realtime-candle ──────────────────────────────────────────────────────
    p = sub.add_parser("realtime-candle", help="Stream live processed candle ticks")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    _add_account_flags(p)

    # ── buy ──────────────────────────────────────────────────────────────────
    p = sub.add_parser("buy", help="Place an immediate binary option trade")
    _add_asset_flag(p)
    p.add_argument("--amount", type=float, default=1.0, help="Trade amount (default: 1.0)")
    p.add_argument(
        "--direction",
        choices=["call", "put"],
        default="call",
        help="call = UP, put = DOWN (default: call)",
    )
    p.add_argument("--duration", type=int, default=60, help="Duration in seconds (default: 60)")
    p.add_argument(
        "--check-win", action="store_true", help="Wait for the trade to settle and show win/loss"
    )
    _add_account_flags(p)

    # ── sell ─────────────────────────────────────────────────────────────────
    p = sub.add_parser("sell", help="Sell / close an open position early")
    p.add_argument("--id", dest="trade_id", required=True, help="Trade ID to sell")
    _add_account_flags(p)

    # ── pending ──────────────────────────────────────────────────────────────
    p = sub.add_parser("pending", help="Place a pending order (executed at a future time)")
    _add_asset_flag(p)
    p.add_argument("--amount", type=float, default=1.0, help="Trade amount (default: 1.0)")
    p.add_argument(
        "--direction",
        choices=["call", "put"],
        default="call",
        help="call = UP, put = DOWN (default: call)",
    )
    p.add_argument("--duration", type=int, default=60, help="Duration in seconds (default: 60)")
    p.add_argument(
        "--open-time",
        dest="open_time",
        default=None,
        help="Exact open time HH:MM (optional, defaults to next candle)",
    )
    _add_account_flags(p)

    # ── check ────────────────────────────────────────────────────────────────
    p = sub.add_parser("check", help="Check win/loss result of a trade by ID")
    p.add_argument("--id", dest="trade_id", required=True, help="Trade ID to check")
    _add_account_flags(p)

    # ── result ───────────────────────────────────────────────────────────────
    p = sub.add_parser("result", help="Look up trade result from history by operation ID")
    p.add_argument("--id", dest="operation_id", required=True, help="Operation ID to look up")
    _add_account_flags(p)

    # ── history ──────────────────────────────────────────────────────────────
    p = sub.add_parser("history", help="Show recent trade history (paged)")
    p.add_argument("--pages", type=int, default=1, help="Number of history pages (default: 1)")
    _add_account_flags(p)

    # ── signals ──────────────────────────────────────────────────────────────
    sub.add_parser("signals", help="Fetch current signal data from the signals stream")

    # ── indicator ────────────────────────────────────────────────────────────
    p = sub.add_parser("indicator", help="Calculate a technical indicator (RSI, MACD, BB, …)")
    _add_asset_flag(p)
    p.add_argument(
        "--name",
        choices=["RSI", "MACD", "BOLLINGER", "STOCHASTIC", "ADX", "ATR", "SMA", "EMA", "ICHIMOKU"],
        default="RSI",
        help="Indicator name (default: RSI)",
    )
    p.add_argument("--period", type=int, default=14, help="Indicator period (default: 14)")
    p.add_argument(
        "--timeframe", type=int, default=60, help="Candle timeframe in seconds (default: 60)"
    )
    _add_account_flags(p)

    # ── monitor ──────────────────────────────────────────────────────────────
    p = sub.add_parser("monitor", help="Real-time price monitor for an asset")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")

    # ── strategy ─────────────────────────────────────────────────────────────
    p = sub.add_parser("strategy", help="Run Triple-Confirmation strategy (DEMO recommended)")
    _add_asset_flag(p)
    p.add_argument("--period", type=int, default=60, help="Candle period in seconds (default: 60)")
    p.add_argument(
        "--auto-trade",
        action="store_true",
        help="Automatically place trades on signals (DEMO only)",
    )

    return parser
