"""CLI command handlers and the COMMAND_REGISTRY dict."""
from pyquotex.cli.commands.account import (
    cmd_balance,
    cmd_login,
    cmd_server_time,
    cmd_set_demo_balance,
    cmd_settings,
)
from pyquotex.cli.commands.analysis import (
    cmd_history,
    cmd_indicator,
    cmd_monitor,
    cmd_signals,
    cmd_strategy,
)
from pyquotex.cli.commands.candles import (
    cmd_candle_info,
    cmd_candles,
    cmd_candles_deep,
    cmd_candles_v2,
    cmd_history_line,
)
from pyquotex.cli.commands.diagnostics import cmd_test_all
from pyquotex.cli.commands.market import (
    cmd_assets,
    cmd_payout,
    cmd_payout_asset,
)
from pyquotex.cli.commands.realtime import (
    cmd_realtime_candle,
    cmd_realtime_price,
    cmd_realtime_sentiment,
)
from pyquotex.cli.commands.trading import (
    cmd_buy,
    cmd_check,
    cmd_pending,
    cmd_result,
    cmd_sell,
)

COMMAND_REGISTRY = {
    "login": cmd_login,
    "balance": cmd_balance,
    "server-time": cmd_server_time,
    "set-demo-balance": cmd_set_demo_balance,
    "settings": cmd_settings,
    "assets": cmd_assets,
    "payout": cmd_payout,
    "payout-asset": cmd_payout_asset,
    "candles": cmd_candles,
    "candles-v2": cmd_candles_v2,
    "candles-deep": cmd_candles_deep,
    "history-line": cmd_history_line,
    "candle-info": cmd_candle_info,
    "realtime-price": cmd_realtime_price,
    "realtime-sentiment": cmd_realtime_sentiment,
    "realtime-candle": cmd_realtime_candle,
    "buy": cmd_buy,
    "sell": cmd_sell,
    "pending": cmd_pending,
    "check": cmd_check,
    "result": cmd_result,
    "signals": cmd_signals,
    "history": cmd_history,
    "indicator": cmd_indicator,
    "monitor": cmd_monitor,
    "strategy": cmd_strategy,
    "test-all": cmd_test_all,
}
