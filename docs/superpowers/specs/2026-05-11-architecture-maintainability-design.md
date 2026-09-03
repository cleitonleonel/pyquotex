# Architecture & Maintainability Refactor — Design Spec

**Date:** 2026-05-11
**Scope:** `pyquotex` library + CLI
**Status:** Approved for planning

## Goals

Reduce monolithic complexity in `pyquotex` while preserving 100% public API backwards compatibility:

1. Split [pyquotex/stable_api.py](../../../pyquotex/stable_api.py) (1573 lines) into domain submodules.
2. Split [app.py](../../../app.py) (1435 lines, ~30 CLI commands) into per-topic command modules.
3. Replace all polling `asyncio.sleep` loops (18+ occurrences) with event-driven waits.

## Non-Goals

- No migration to typer/click — keep `argparse`.
- No new test suite — only minimum regression tests.
- No changes to public method signatures or return types.
- No bump to v2.x — this is a v1.x maintenance refactor.
- No changes to WebSocket protocol, login flow, or business logic.

## Constraints

- `from pyquotex.stable_api import Quotex` must continue to work identically.
- All current public methods on `Quotex` must remain callable as `client.method()`.
- `python app.py <command>` must keep working (referenced in README).
- No new runtime dependencies (Termux compatibility).
- Each phase must leave the repo in a working state (tests passing, CLI usable).

## Target Architecture

### Module Layout

```
pyquotex/
├── stable_api.py              # facade (~200 lines, was 1573)
├── _api/                      # private domain package (underscore = not part of public API)
│   ├── __init__.py
│   ├── _waits.py              # WaitableSlot, wait_until helpers
│   ├── account.py             # AccountMixin
│   ├── trading.py             # TradingMixin
│   ├── history.py             # HistoryMixin
│   ├── realtime.py            # RealtimeMixin
│   └── assets.py              # AssetsMixin
├── cli/
│   ├── __init__.py
│   ├── __main__.py            # entry point
│   ├── parser.py              # make_parser() + subparsers
│   ├── runtime.py             # connect_with_retry, on_otp, helpers
│   ├── formatters.py          # table/CSV formatters shared by commands
│   └── commands/
│       ├── __init__.py        # COMMAND_REGISTRY dict
│       ├── account.py         # login, balance, server_time, set_demo_balance, settings
│       ├── market.py          # assets, payout, payout_asset
│       ├── candles.py         # candles, candles_v2, candles_deep, history_line, candle_info
│       ├── realtime.py        # realtime_price, realtime_sentiment, realtime_candle
│       ├── trading.py         # buy, sell, pending, check, result
│       ├── analysis.py        # signals, history, indicator, monitor, strategy
│       └── diagnostics.py     # test_all
├── api.py                     # unchanged (WS client QuotexAPI)
└── …                          # rest of package unchanged
```

Notes:
- The private package is named `_api/` (with underscore prefix) to avoid collision with the existing `pyquotex/api.py` (WebSocket client) and to signal it is internal.
- `app.py` at repo root is preserved as a 5-line shim that calls `pyquotex.cli.__main__.main()`.

### Mixin-Based Composition

`Quotex` is assembled via multiple inheritance over domain mixins:

```python
# pyquotex/stable_api.py (post-refactor, ~200 lines)
from pyquotex._api.account import AccountMixin
from pyquotex._api.trading import TradingMixin
from pyquotex._api.history import HistoryMixin
from pyquotex._api.realtime import RealtimeMixin
from pyquotex._api.assets import AssetsMixin

class Quotex(
    AccountMixin,
    TradingMixin,
    HistoryMixin,
    RealtimeMixin,
    AssetsMixin,
):
    def __init__(self, email, password, lang="pt", ...):
        # All current __init__ logic stays here
        ...

    # Truly core methods only: websocket property, set_session,
    # check_connect, close, _check_connect
```

Each mixin uses `self.api`, `self.session_data`, etc. — the same shared state attributes that exist today. The mixins never instantiate anything; they only provide methods bound to `Quotex` via the MRO.

Rationale for mixins over composition (`client.trading.buy()`):
- Mixins preserve 100% call-site backwards compatibility (`client.buy()` keeps working).
- No facade glue methods needed.
- Same `self` state-sharing model as today.

### Mixin → Method Mapping

| Mixin | Methods moved from stable_api.py |
|---|---|
| `AccountMixin` | `connect`, `reconnect`, `get_balance`, `get_profile`, `get_server_time`, `change_account`, `change_time_offset`, `set_account_mode`, `edit_practice_balance`, `store_settings_apply`, `get_payment` (account-related), `start_remaing_time` |
| `TradingMixin` | `buy`, `sell_option`, `open_pending`, `check_win`, `get_result`, `get_profit`, `get_history` (trade history) |
| `HistoryMixin` | `get_candles`, `_fetch_historical_batch`, `_parse_historical_candles`, `get_historical_candles`, `get_candles_deep`, `get_candle_v2`, `get_history_line`, `get_trader_history`, `prepare_candles` |
| `RealtimeMixin` | `start_candles_stream`, `stop_candles_stream`, `start_candles_one_stream`, `start_candles_all_size_stream`, `start_signals_data`, `start_realtime_price`, `start_realtime_sentiment`, `start_realtime_candle`, `get_realtime_candles`, `get_realtime_sentiment`, `get_realtime_price`, `subscribe_indicator`, `calculate_indicator`, `start_mood_stream`, `opening_closing_current_candle`, `get_signal_data` |
| `AssetsMixin` | `get_instruments`, `get_all_asset_name`, `get_available_asset`, `check_asset_open`, `get_all_assets`, `get_payout_by_asset`, `re_subscribe_stream` |

## Event-Driven Waits (Polling Replacement)

### Helper

`pyquotex/_api/_waits.py`:

```python
import asyncio
from typing import TypeVar, Callable

T = TypeVar("T")
DEFAULT_TIMEOUT = 10.0


class WaitableSlot[T]:
    """Typed slot a consumer awaits and the WS handler fills."""

    def __init__(self):
        self._value: T | None = None
        self._event = asyncio.Event()

    def set(self, value: T) -> None:
        self._value = value
        self._event.set()

    def clear(self) -> None:
        self._value = None
        self._event.clear()

    async def wait(self, timeout: float = DEFAULT_TIMEOUT) -> T:
        await asyncio.wait_for(self._event.wait(), timeout=timeout)
        return self._value  # type: ignore[return-value]


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll_fallback: float = 0.05,
) -> None:
    """For states that cannot be signaled from the WS handler.
    Short poll with a hard timeout."""

    async def _loop():
        while not predicate():
            await asyncio.sleep(poll_fallback)

    await asyncio.wait_for(_loop(), timeout=timeout)
```

### Migration Map

| Caller (current line) | Polling pattern | Replacement |
|---|---|---|
| `get_balance` (stable_api:663) | `while balance is None: sleep(0.2)` | `WaitableSlot[float]` fired by `s_balance` handler |
| `buy` confirm (stable_api:1153) | `while order_id is None: sleep(0.2)` | `WaitableSlot` keyed by `request_id` in order-confirm handler |
| `sell_option` confirm (stable_api:1185) | same pattern | same approach |
| `check_win` (stable_api:1256) | `while result is None: sleep(1)` | `WaitableSlot` indexed by `operation_id` |
| `start_candles_*` init (stable_api:455, 516, 540) | `sleep(0.1)`/`sleep(0.2)` startup wait | `WaitableSlot` fired by first stream message |
| `calculate_indicator` (stable_api:934, 939, 1031) | `sleep(1)` waiting for candle data | `WaitableSlot` per asset/period |
| `connect` retry (api.py:139) | `sleep(5)` after error | Explicit exponential backoff (no library dep) |
| `network/login.py:98, 170` | `sleep(1)` between attempts | Exponential backoff with jitter |
| `check_connect` (stable_api:114, 183) | `sleep(2)` connection wait | `WaitableSlot[bool]` fired by auth-status handler |

### WS Handler Integration

The WS message dispatcher in [pyquotex/api.py](../../../pyquotex/api.py) gains slot-filling alongside its current state mutations:

```python
# inside QuotexAPI.on_message handler (pseudocode)
elif event == "s_balance":
    self.account_balance = data
    self.slots.balance.set(data)        # NEW: wake waiters
elif event == "successupdateBalance":
    self.slots.balance_update.set(data) # NEW
elif event == "tradesOpened":
    self.slots.order_confirm[request_id].set(data)  # NEW: keyed slot
```

Slots are stored on `QuotexAPI` as a `SlotRegistry` namespace (simple dataclass holding the named slots plus dicts for keyed slots like `order_confirm[request_id]`).

### Error Handling

- All `wait()` calls have a default 10 s timeout; methods that historically waited longer (e.g., `check_win`) get a configurable timeout parameter.
- `asyncio.TimeoutError` is caught at the mixin boundary and re-raised as `QuotexTimeoutError` (new exception class in `pyquotex/exceptions.py` — create if absent) so callers don't import from `asyncio`.
- No silent failures: timeouts always raise.

## CLI Refactor

### Entrypoint

```python
# pyquotex/cli/__main__.py (~80 lines)
import asyncio
from pyquotex.stable_api import Quotex
from pyquotex.cli.parser import make_parser
from pyquotex.cli.runtime import connect_with_retry
from pyquotex.cli.commands import COMMAND_REGISTRY


async def main():
    parser = make_parser()
    args = parser.parse_args()
    client = Quotex(email=args.email, password=args.password, lang=args.lang)
    client.set_session(...)
    await connect_with_retry(client)
    try:
        handler = COMMAND_REGISTRY[args.command]
        await handler(client, args)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### Command Registry

`pyquotex/cli/commands/__init__.py` imports each `cmd_*` and registers them in a dict:

```python
from .account import cmd_login, cmd_balance, cmd_server_time, cmd_set_demo_balance, cmd_settings
from .market import cmd_assets, cmd_payout, cmd_payout_asset
from .candles import (
    cmd_candles,
    cmd_candles_v2,
    cmd_candles_deep,
    cmd_history_line,
    cmd_candle_info,
)
from .realtime import cmd_realtime_price, cmd_realtime_sentiment, cmd_realtime_candle
from .trading import cmd_buy, cmd_sell, cmd_pending, cmd_check, cmd_result
from .analysis import cmd_signals, cmd_history, cmd_indicator, cmd_monitor, cmd_strategy
from .diagnostics import cmd_test_all

COMMAND_REGISTRY = {
    "login": cmd_login,
    "balance": cmd_balance,
    "server-time": cmd_server_time,
    # … 30 entries
}
```

No decorators, no import-time side effects beyond the explicit dict assignment.

### Compat Shim

```python
# app.py (root, post-refactor — 5 lines)
import asyncio
from pyquotex.cli.__main__ import main

if __name__ == "__main__":
    asyncio.run(main())
```

### Helpers Extraction

- `_balance_table` → `pyquotex/cli/formatters.py`
- `_print_candles_table` → `pyquotex/cli/formatters.py`
- `_save_candles_csv` → `pyquotex/cli/formatters.py`
- `connect_with_retry` → `pyquotex/cli/runtime.py`
- `on_otp` → `pyquotex/cli/runtime.py`
- `_is_demo` → `pyquotex/cli/runtime.py`

The `make_parser()` function moves to `pyquotex/cli/parser.py` unchanged.

## Tests

### New Tests Added

1. **`tests/test_api_surface.py`** — snapshot check of `Quotex` public methods + signatures (loads `tests/fixtures/api_surface.json`).
2. **`tests/test_import_compat.py`** — verifies legacy imports still resolve.
3. **`tests/test_cli_smoke.py`** — runs `python -m pyquotex --help` and `python app.py --help` via `subprocess`.
4. **`tests/test_waits.py`** — unit tests for `WaitableSlot` and `wait_until` (resolution, timeout, clear).

### Snapshot Generation

Before any refactor, a one-shot script `scripts/snapshot_api_surface.py` writes `tests/fixtures/api_surface.json` with:
- All public methods of `Quotex` (`dir(Quotex)` filtered to not start with `_`).
- `inspect.signature()` for each — parameter names, defaults, annotations.

The snapshot file is committed and is the source of truth for the surface test.

### Acceptance Criteria

- All 4 new tests pass without credentials.
- `pytest tests/ -k "not test_buy and not test_win and not test_login"` passes locally.
- No method removed from `Quotex` public surface.
- No CLI command removed or renamed.

### What Is Not Tested

Existing integration tests (`test_buy.py`, `test_win.py`, `test_login.py`, etc.) that require live Quotex credentials remain unchanged and are out of scope for CI gating.

## Implementation Phases

Each phase is independently revertible; the repo stays functional between phases.

### Phase 0 — Safety net (1 commit)

1. Add `scripts/snapshot_api_surface.py` and run it to generate `tests/fixtures/api_surface.json`.
2. Add `tests/test_api_surface.py`, `tests/test_import_compat.py`, `tests/test_cli_smoke.py`.
3. Confirm new tests pass.

### Phase 1 — Wait helpers (1 commit)

1. Create `pyquotex/_api/__init__.py` (empty) and `pyquotex/_api/_waits.py` with `WaitableSlot` and `wait_until`.
2. Create `pyquotex/exceptions.py` with `QuotexTimeoutError`. (Verified: file does not currently exist.)
3. Add `tests/test_waits.py`.
4. No changes to `stable_api.py` or `api.py` yet.

### Phase 2 — Polling → events (3–4 commits, one per domain)

1. Add `SlotRegistry` to `QuotexAPI` in `pyquotex/api.py`. Wire WS message handlers to fill slots in addition to current state mutations.
2. Migrate `get_balance` polling → `WaitableSlot`. Run tests.
3. Migrate `buy`/`sell_option`/`open_pending` confirmation polling. Run tests.
4. Migrate `check_win` and `get_result` polling. Run tests.
5. Migrate `start_*_stream` startup waits. Run tests.
6. Migrate `connect`/login retry sleeps to explicit exponential backoff. Run tests.

Commit boundaries can be one-per-step or grouped by domain; the planner skill will decide the final granularity.

### Phase 3 — Extract mixins (5 commits)

1. Create `pyquotex/_api/account.py` with `AccountMixin`; move account-related methods; add to `Quotex` bases. Run surface test.
2. Repeat for `trading.py`, `history.py`, `realtime.py`, `assets.py` (one mixin per commit).
3. After all 5, `stable_api.py` contains only `__init__`, `websocket` property, `set_session`, `check_connect`, `_check_connect`, `close`, plus the class declaration with mixins.

### Phase 4 — CLI modularization (3 commits)

1. Create `pyquotex/cli/` skeleton (`parser.py`, `runtime.py`, `formatters.py`, `__main__.py`) without removing anything from `app.py` yet.
2. Move `cmd_*` functions into `pyquotex/cli/commands/*.py` by domain group. Update imports. CLI smoke test must pass.
3. Reduce root `app.py` to the 5-line shim. CLI smoke test must still pass.

### Phase 5 — Cleanup (1 commit)

1. Remove dead code from `stable_api.py` (imports no longer needed, etc.).
2. Update [README.md](../../../README.md) with a brief "Architecture" section pointing to the new layout (optional).
3. Bump version in `pyproject.toml` (e.g., 1.0.3 → 1.1.0) — this is a non-breaking refactor that adds internal structure.

## Risk Mitigation

- **Branch**: all work on `refactor/architecture`; merge to `master` only after Phase 5.
- **Per-phase reversibility**: each phase is a coherent set of commits; reverting one does not require reverting others.
- **Continuous verification**: Phase 0's surface test is run locally (and in CI if/when added) before each subsequent commit. Any drop in the public surface fails the test immediately.
- **Timing regressions**: Phase 2 (event migration) is the highest-risk phase. If the integration tests (`test_buy.py`) reveal timing issues, the slot model can be tuned (longer default timeouts, configurable per-method) without abandoning the refactor.
- **Rollback escape hatch**: if Phase 2 proves unworkable, Phases 3–5 are independent and can proceed without it.

## Open Questions

None — all major decisions confirmed during brainstorming:
- All three refactor fronts in one spec.
- 100% backwards compatible via mixin-based facade.
- Keep `argparse`, modularize CLI only.
- Replace ALL polling with events.
- 5 domain mixins.
- Minimum regression tests only.

## Effort Estimate

14–16 commits total across 5 phases, 3–5 work sessions depending on pacing.
