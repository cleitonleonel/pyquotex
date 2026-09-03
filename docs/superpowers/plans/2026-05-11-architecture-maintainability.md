# Architecture & Maintainability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `stable_api.py` (1573 lines) into domain mixins, modularize `app.py` (1435-line CLI) into per-topic files, and replace `asyncio.sleep`-based polling with event-driven waits — while preserving 100% public API backwards compatibility.

**Architecture:** Mixin-based facade in `pyquotex/stable_api.py` composing five domain mixins from a new private `pyquotex/_api/` package. CLI moves to `pyquotex/cli/` with command modules registered in a dict; `app.py` becomes a 5-line shim. Polling loops are replaced by a typed `WaitableSlot` helper whose `.set()` is invoked from the WS message handler in `pyquotex/api.py`.

**Tech Stack:** Python 3.12+, `asyncio`, `argparse`, `pytest`, `pytest-asyncio`, no new runtime dependencies.

**Spec:** [docs/superpowers/specs/2026-05-11-architecture-maintainability-design.md](../specs/2026-05-11-architecture-maintainability-design.md)

**Branch:** All work happens on `refactor/architecture`. Merge to `master` only after Phase 5.

---

## File Structure

### Files Created

| Path | Responsibility |
|---|---|
| `pyquotex/exceptions.py` | Custom exception types (`QuotexTimeoutError`) |
| `pyquotex/_api/__init__.py` | Marker for private domain package |
| `pyquotex/_api/_waits.py` | `WaitableSlot[T]`, `wait_until`, `SlotRegistry` |
| `pyquotex/_api/account.py` | `AccountMixin` — balance, profile, connection, account mode |
| `pyquotex/_api/trading.py` | `TradingMixin` — buy, sell, pending, check_win, results |
| `pyquotex/_api/history.py` | `HistoryMixin` — candles (sync and historical), trade history |
| `pyquotex/_api/realtime.py` | `RealtimeMixin` — streams, sentiment, indicators |
| `pyquotex/_api/assets.py` | `AssetsMixin` — instruments, payouts, asset availability |
| `pyquotex/cli/__init__.py` | Marker |
| `pyquotex/cli/__main__.py` | Entry point with `asyncio.run(main())` |
| `pyquotex/cli/parser.py` | `make_parser()` and all subparser definitions |
| `pyquotex/cli/runtime.py` | `connect_with_retry`, `on_otp`, `_is_demo` |
| `pyquotex/cli/formatters.py` | `_balance_table`, `_print_candles_table`, `_save_candles_csv` |
| `pyquotex/cli/commands/__init__.py` | `COMMAND_REGISTRY` dict |
| `pyquotex/cli/commands/account.py` | login, balance, server-time, set-demo-balance, settings |
| `pyquotex/cli/commands/market.py` | assets, payout, payout-asset |
| `pyquotex/cli/commands/candles.py` | candles, candles-v2, candles-deep, history-line, candle-info |
| `pyquotex/cli/commands/realtime.py` | realtime-price, realtime-sentiment, realtime-candle |
| `pyquotex/cli/commands/trading.py` | buy, sell, pending, check, result |
| `pyquotex/cli/commands/analysis.py` | signals, history, indicator, monitor, strategy |
| `pyquotex/cli/commands/diagnostics.py` | test-all |
| `scripts/snapshot_api_surface.py` | One-shot generator for `tests/fixtures/api_surface.json` |
| `tests/fixtures/api_surface.json` | Baseline snapshot of `Quotex` public surface |
| `tests/test_api_surface.py` | Regression test against the snapshot |
| `tests/test_import_compat.py` | Verifies legacy imports still resolve |
| `tests/test_cli_smoke.py` | `--help` smoke tests for CLI entrypoints |
| `tests/test_waits.py` | Unit tests for `WaitableSlot` and `wait_until` |

### Files Modified

| Path | Change |
|---|---|
| `pyquotex/stable_api.py` | Shrinks from 1573 → ~200 lines (facade only) |
| `pyquotex/api.py` | Add `SlotRegistry` attribute and wire `.set()` calls in `_on_message` |
| `app.py` | Shrinks from 1435 → 5 lines (shim to `pyquotex.cli.__main__:main`) |
| `pyproject.toml` | Bump version 1.0.3 → 1.1.0 |

---

## Phase 0 — Safety Net

### Task 0.1: Create refactor branch

**Files:** repo-wide

- [ ] **Step 1: Create and switch to the refactor branch**

```bash
git checkout -b refactor/architecture
git status
```

Expected: `On branch refactor/architecture` with clean working tree.

---

### Task 0.2: Generate public API surface snapshot

**Files:**
- Create: `scripts/snapshot_api_surface.py`
- Create: `tests/fixtures/api_surface.json`

- [ ] **Step 1: Create the snapshot script**

Create `scripts/snapshot_api_surface.py`:

```python
"""Generate a baseline snapshot of Quotex's public API surface.

Run once before the refactor and commit tests/fixtures/api_surface.json.
The regression test in tests/test_api_surface.py compares the live class
against this snapshot.
"""

import inspect
import json
from pathlib import Path

from pyquotex.stable_api import Quotex

OUTPUT = Path(__file__).parent.parent / "tests" / "fixtures" / "api_surface.json"


def _serialize_signature(sig: inspect.Signature) -> dict:
    params = []
    for name, param in sig.parameters.items():
        params.append(
            {
                "name": name,
                "kind": str(param.kind),
                "default": (
                    "<no-default>"
                    if param.default is inspect.Parameter.empty
                    else repr(param.default)
                ),
                "annotation": (
                    "<no-annotation>"
                    if param.annotation is inspect.Parameter.empty
                    else str(param.annotation)
                ),
            }
        )
    return {
        "parameters": params,
        "return_annotation": (
            "<no-annotation>"
            if sig.return_annotation is inspect.Signature.empty
            else str(sig.return_annotation)
        ),
    }


def main() -> None:
    surface: dict[str, dict] = {}
    for name in sorted(dir(Quotex)):
        if name.startswith("_"):
            continue
        attr = getattr(Quotex, name)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
            except (TypeError, ValueError):
                surface[name] = {"kind": "callable", "signature": None}
                continue
            surface[name] = {
                "kind": "method",
                "signature": _serialize_signature(sig),
            }
        else:
            surface[name] = {"kind": "attribute", "type": type(attr).__name__}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(surface)} public symbols to {OUTPUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script to generate the fixture**

```bash
python scripts/snapshot_api_surface.py
```

Expected: `Wrote N public symbols to tests/fixtures/api_surface.json` (where N is around 50+).

- [ ] **Step 3: Verify fixture looks reasonable**

```bash
python -c "import json; d = json.load(open('tests/fixtures/api_surface.json')); print(len(d), sorted(d)[:10])"
```

Expected: A list of public method names like `['buy', 'calculate_indicator', 'change_account', 'change_time_offset', 'check_asset_open', ...]`.

- [ ] **Step 4: Commit**

```bash
git add scripts/snapshot_api_surface.py tests/fixtures/api_surface.json
git commit -m "test: snapshot baseline of Quotex public API surface"
```

---

### Task 0.3: Add surface regression test

**Files:**
- Create: `tests/test_api_surface.py`

- [ ] **Step 1: Write the test**

Create `tests/test_api_surface.py`:

```python
"""Regression test: Quotex's public surface must not shrink during refactors."""

import inspect
import json
from pathlib import Path

from pyquotex.stable_api import Quotex

FIXTURE = Path(__file__).parent / "fixtures" / "api_surface.json"


def _current_surface() -> dict[str, dict]:
    surface: dict[str, dict] = {}
    for name in sorted(dir(Quotex)):
        if name.startswith("_"):
            continue
        attr = getattr(Quotex, name)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
            except (TypeError, ValueError):
                surface[name] = {"kind": "callable"}
                continue
            params = [p.name for p in sig.parameters.values()]
            surface[name] = {"kind": "method", "params": params}
        else:
            surface[name] = {"kind": "attribute"}
    return surface


def test_public_methods_present():
    """Every public method in the snapshot must still exist on Quotex."""
    baseline = json.loads(FIXTURE.read_text())
    current = _current_surface()
    missing = sorted(set(baseline) - set(current))
    assert not missing, f"Public symbols removed: {missing}"


def test_public_method_params_unchanged():
    """Parameter names of public methods must not change (order matters)."""
    baseline = json.loads(FIXTURE.read_text())
    current = _current_surface()
    diffs: list[str] = []
    for name, baseline_entry in baseline.items():
        if baseline_entry.get("kind") != "method":
            continue
        if name not in current:
            continue  # caught by previous test
        baseline_params = [p["name"] for p in baseline_entry["signature"]["parameters"]]
        current_params = current[name].get("params", [])
        if baseline_params != current_params:
            diffs.append(f"{name}: baseline={baseline_params} current={current_params}")
    assert not diffs, "Parameter signatures changed:\n" + "\n".join(diffs)
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/test_api_surface.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_surface.py
git commit -m "test: add public API surface regression tests"
```

---

### Task 0.4: Add import compatibility test

**Files:**
- Create: `tests/test_import_compat.py`

- [ ] **Step 1: Write the test**

Create `tests/test_import_compat.py`:

```python
"""Verify legacy import paths continue to resolve."""


def test_stable_api_quotex_importable():
    from pyquotex.stable_api import Quotex

    assert Quotex is not None
    assert hasattr(Quotex, "buy")
    assert hasattr(Quotex, "get_balance")
    assert hasattr(Quotex, "connect")


def test_quotex_api_importable():
    from pyquotex.api import QuotexAPI

    assert QuotexAPI is not None


def test_account_type_importable():
    from pyquotex.utils.account_type import AccountType

    assert AccountType.DEMO is not None
    assert AccountType.REAL is not None


def test_indicators_importable():
    from pyquotex.utils.indicators import TechnicalIndicators

    assert TechnicalIndicators is not None
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/test_import_compat.py -v
```

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_import_compat.py
git commit -m "test: verify legacy imports remain available"
```

---

### Task 0.5: Add CLI smoke test

**Files:**
- Create: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write the test**

Create `tests/test_cli_smoke.py`:

```python
"""Smoke tests: CLI entrypoints respond to --help."""

import subprocess
import sys


def test_app_py_help_runs():
    """`python app.py --help` must exit 0 and list commands."""
    result = subprocess.run(
        [sys.executable, "app.py", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "balance" in result.stdout
    assert "buy" in result.stdout


def test_module_invocation_help_runs():
    """`python -m pyquotex --help` must exit 0 and list commands."""
    result = subprocess.run(
        [sys.executable, "-m", "pyquotex", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "balance" in result.stdout
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/test_cli_smoke.py -v
```

Expected: 2 passed. If `python -m pyquotex` does not exist yet, the second test will fail — adjust by reading `pyquotex/__main__.py` to confirm; if it exists, the test should pass.

- [ ] **Step 3: Inspect __main__ if needed**

If `test_module_invocation_help_runs` fails, run:

```bash
cat pyquotex/__main__.py
```

Confirm it routes to `app.py`'s parser. If it does not list commands, mark this test as expected-fail with a comment until Phase 4 wires it up:

```python
import pytest


@pytest.mark.skip(reason="python -m pyquotex wired up in Phase 4")
def test_module_invocation_help_runs(): ...
```

- [ ] **Step 4: Re-run**

```bash
pytest tests/test_cli_smoke.py -v
```

Expected: all pass (with one skip if applicable).

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli_smoke.py
git commit -m "test: add CLI --help smoke tests"
```

---

## Phase 1 — Wait Helpers and Exceptions

### Task 1.1: Add QuotexTimeoutError

**Files:**
- Create: `pyquotex/exceptions.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_import_compat.py`:

```python
def test_exceptions_importable():
    from pyquotex.exceptions import QuotexTimeoutError

    assert issubclass(QuotexTimeoutError, Exception)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_import_compat.py::test_exceptions_importable -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'pyquotex.exceptions'`.

- [ ] **Step 3: Create the module**

Create `pyquotex/exceptions.py`:

```python
"""Custom exception types raised by pyquotex public APIs."""


class QuotexTimeoutError(Exception):
    """Raised when a Quotex operation exceeds its allotted timeout.

    Wraps asyncio.TimeoutError so callers do not need to import asyncio.
    """
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_import_compat.py::test_exceptions_importable -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/exceptions.py tests/test_import_compat.py
git commit -m "feat(exceptions): add QuotexTimeoutError"
```

---

### Task 1.2: Scaffold private `_api` package

**Files:**
- Create: `pyquotex/_api/__init__.py`

- [ ] **Step 1: Create the package marker**

Create `pyquotex/_api/__init__.py`:

```python
"""Private domain submodules for pyquotex.

Not part of the public API. Re-exports are routed through pyquotex.stable_api.
"""
```

- [ ] **Step 2: Verify package imports**

```bash
python -c "import pyquotex._api; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add pyquotex/_api/__init__.py
git commit -m "chore: scaffold pyquotex._api private package"
```

---

### Task 1.3: Implement WaitableSlot

**Files:**
- Create: `pyquotex/_api/_waits.py`
- Create: `tests/test_waits.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_waits.py`:

```python
"""Unit tests for WaitableSlot and wait_until."""

import asyncio
import pytest

from pyquotex._api._waits import WaitableSlot, wait_until


@pytest.mark.asyncio
async def test_slot_resolves_with_set_value():
    slot: WaitableSlot[int] = WaitableSlot()

    async def setter():
        await asyncio.sleep(0.01)
        slot.set(42)

    asyncio.create_task(setter())
    assert await slot.wait(timeout=1.0) == 42


@pytest.mark.asyncio
async def test_slot_times_out_when_never_set():
    slot: WaitableSlot[int] = WaitableSlot()
    with pytest.raises(asyncio.TimeoutError):
        await slot.wait(timeout=0.05)


@pytest.mark.asyncio
async def test_slot_can_be_cleared_and_reused():
    slot: WaitableSlot[str] = WaitableSlot()
    slot.set("first")
    assert await slot.wait(timeout=0.1) == "first"
    slot.clear()
    with pytest.raises(asyncio.TimeoutError):
        await slot.wait(timeout=0.05)
    slot.set("second")
    assert await slot.wait(timeout=0.1) == "second"


@pytest.mark.asyncio
async def test_slot_set_before_wait_resolves_immediately():
    slot: WaitableSlot[int] = WaitableSlot()
    slot.set(7)
    assert await slot.wait(timeout=0.1) == 7


@pytest.mark.asyncio
async def test_wait_until_resolves_when_predicate_true():
    counter = {"n": 0}

    async def increment():
        await asyncio.sleep(0.01)
        counter["n"] = 5

    asyncio.create_task(increment())
    await wait_until(lambda: counter["n"] >= 5, timeout=1.0)
    assert counter["n"] == 5


@pytest.mark.asyncio
async def test_wait_until_times_out():
    with pytest.raises(asyncio.TimeoutError):
        await wait_until(lambda: False, timeout=0.05)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_waits.py -v
```

Expected: All fail with `ModuleNotFoundError: No module named 'pyquotex._api._waits'`.

- [ ] **Step 3: Implement the helpers**

Create `pyquotex/_api/_waits.py`:

```python
"""Event-driven wait primitives that replace asyncio.sleep polling.

A WaitableSlot is a typed one-shot (re-armable) signal: a consumer awaits
.wait(), and the producer (typically the WS message handler) calls .set(value).

wait_until() exists for cases where the desired state cannot be signaled
from the WS handler. It still uses short polling internally but enforces
a hard timeout.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

DEFAULT_TIMEOUT: float = 10.0


class WaitableSlot(Generic[T]):
    """Typed slot a consumer awaits and the producer fills via .set()."""

    __slots__ = ("_value", "_event")

    def __init__(self) -> None:
        self._value: T | None = None
        self._event = asyncio.Event()

    def set(self, value: T) -> None:
        """Store the value and wake any awaiting consumers."""
        self._value = value
        self._event.set()

    def clear(self) -> None:
        """Reset the slot so subsequent waits block again."""
        self._value = None
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self, timeout: float = DEFAULT_TIMEOUT) -> T:
        """Block until set or raise asyncio.TimeoutError on timeout."""
        await asyncio.wait_for(self._event.wait(), timeout=timeout)
        return self._value  # type: ignore[return-value]


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll_interval: float = 0.05,
) -> None:
    """Poll predicate() until truthy or raise asyncio.TimeoutError."""

    async def _loop() -> None:
        while not predicate():
            await asyncio.sleep(poll_interval)

    await asyncio.wait_for(_loop(), timeout=timeout)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_waits.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/_api/_waits.py tests/test_waits.py
git commit -m "feat(_api): add WaitableSlot and wait_until helpers"
```

---

### Task 1.4: Add SlotRegistry

**Files:**
- Modify: `pyquotex/_api/_waits.py`
- Modify: `tests/test_waits.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_waits.py`:

```python
from pyquotex._api._waits import SlotRegistry


def test_slot_registry_has_named_slots():
    reg = SlotRegistry()
    assert reg.balance is not None
    assert reg.balance_update is not None
    assert reg.candle_v2_ready is not None
    assert reg.historical_ready is not None
    assert reg.pending_confirm is not None
    assert reg.sold_option_confirm is not None
    assert reg.training_balance_edit is not None
    assert reg.auth_status is not None


def test_slot_registry_keyed_slots_create_on_access():
    reg = SlotRegistry()
    slot_a = reg.order_confirm("req-1")
    slot_b = reg.order_confirm("req-1")
    slot_c = reg.order_confirm("req-2")
    assert slot_a is slot_b  # same key returns same slot
    assert slot_a is not slot_c  # different key returns different slot


def test_slot_registry_keyed_slot_release():
    reg = SlotRegistry()
    slot = reg.order_confirm("req-1")
    slot.set({"id": 1})
    reg.release_order_confirm("req-1")
    new_slot = reg.order_confirm("req-1")
    assert new_slot is not slot
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_waits.py -k "SlotRegistry or slot_registry" -v
```

Expected: All fail with `ImportError`.

- [ ] **Step 3: Implement SlotRegistry**

Append to `pyquotex/_api/_waits.py`:

```python
class SlotRegistry:
    """Container of named and keyed WaitableSlots used by QuotexAPI.

    Named slots are pre-created for one-off events (balance update, auth
    status change, etc.). Keyed slots are dynamic per-request waits keyed
    by request_id / operation_id; they are created lazily and released
    once the consumer has read the value.
    """

    def __init__(self) -> None:
        # Named slots
        self.balance: WaitableSlot[dict] = WaitableSlot()
        self.balance_update: WaitableSlot[dict] = WaitableSlot()
        self.candle_v2_ready: WaitableSlot[str] = WaitableSlot()
        self.historical_ready: WaitableSlot[str] = WaitableSlot()
        self.pending_confirm: WaitableSlot[dict] = WaitableSlot()
        self.sold_option_confirm: WaitableSlot[dict] = WaitableSlot()
        self.training_balance_edit: WaitableSlot[dict] = WaitableSlot()
        self.auth_status: WaitableSlot[bool] = WaitableSlot()

        # Keyed slots (created on demand)
        self._order_confirm: dict[str, WaitableSlot[dict]] = {}
        self._win_result: dict[str, WaitableSlot[dict]] = {}

    def order_confirm(self, request_id: str) -> WaitableSlot[dict]:
        slot = self._order_confirm.get(request_id)
        if slot is None:
            slot = WaitableSlot()
            self._order_confirm[request_id] = slot
        return slot

    def release_order_confirm(self, request_id: str) -> None:
        self._order_confirm.pop(request_id, None)

    def win_result(self, operation_id: str) -> WaitableSlot[dict]:
        slot = self._win_result.get(operation_id)
        if slot is None:
            slot = WaitableSlot()
            self._win_result[operation_id] = slot
        return slot

    def release_win_result(self, operation_id: str) -> None:
        self._win_result.pop(operation_id, None)
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_waits.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/_api/_waits.py tests/test_waits.py
git commit -m "feat(_api): add SlotRegistry for named and keyed waitable slots"
```

---

## Phase 2 — Polling → Events

### Task 2.1: Attach SlotRegistry to QuotexAPI

**Files:**
- Modify: `pyquotex/api.py:59-123` (the `__init__` method)

- [ ] **Step 1: Read the current init**

```bash
grep -n "self.event_registry" pyquotex/api.py
```

Locate the line `self.event_registry = EventRegistry()` (around line 121).

- [ ] **Step 2: Add SlotRegistry next to event_registry**

In `pyquotex/api.py`, find the line:

```python
        self.event_registry = EventRegistry()
```

Replace with:

```python
self.event_registry = EventRegistry()
from pyquotex._api._waits import SlotRegistry

self.slots = SlotRegistry()
```

(Inline import avoids a circular import risk; `_waits.py` does not import from `pyquotex.api`.)

- [ ] **Step 3: Add a test confirming the registry exists on a QuotexAPI instance**

Append to `tests/test_waits.py`:

```python
def test_quotex_api_has_slot_registry():
    """QuotexAPI must expose a SlotRegistry as .slots."""
    from pyquotex.api import QuotexAPI

    api = QuotexAPI(
        host="qxbroker.com",
        username="x",
        password="x",
        lang="en",
        resource_path=".",
        user_data_dir="browser",
        proxies=None,
        on_otp_callback=None,
    )
    assert isinstance(api.slots, SlotRegistry)
    assert api.slots.balance is not None
```

- [ ] **Step 4: Run the test**

```bash
pytest tests/test_waits.py::test_quotex_api_has_slot_registry -v
```

Expected: pass. If it fails because of constructor signature drift, adjust the kwargs by reading `pyquotex/api.py` lines around 30-58.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/api.py tests/test_waits.py
git commit -m "feat(api): attach SlotRegistry to QuotexAPI"
```

---

### Task 2.2: Fire balance slot from WS handler

**Files:**
- Modify: `pyquotex/api.py` (lines around 250 and 383)

- [ ] **Step 1: Locate balance assignments**

```bash
grep -n "self.account_balance = " pyquotex/api.py
```

Expected output: two lines (around 250 and 383).

- [ ] **Step 2: Add slot firing**

For each occurrence, change:

```python
                        self.account_balance = data
```

to:

```python
                        self.account_balance = data
                        self.slots.balance.set(data)
```

And:

```python
                    self.account_balance = message
```

to:

```python
                    self.account_balance = message
                    self.slots.balance.set(message)
```

- [ ] **Step 3: Verify imports unchanged**

```bash
python -c "from pyquotex.api import QuotexAPI; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Run surface and import tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py tests/test_waits.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/api.py
git commit -m "feat(api): fire balance slot from WS handler"
```

---

### Task 2.3: Migrate get_balance polling

**Files:**
- Modify: `pyquotex/stable_api.py` (the `get_balance` method around line 666)

- [ ] **Step 1: Read the current implementation**

```bash
sed -n '660,695p' pyquotex/stable_api.py
```

Note the polling loop pattern `while ... is None: await asyncio.sleep(0.2)`.

- [ ] **Step 2: Replace polling with slot.wait()**

In `pyquotex/stable_api.py`, locate the `get_balance` method body. Replace the polling loop:

```python
        while self.api.account_balance is None:
            await asyncio.sleep(0.2)
```

with:

```python
from pyquotex.exceptions import QuotexTimeoutError

if self.api.account_balance is None:
    try:
        await self.api.slots.balance.wait(timeout=timeout)
    except asyncio.TimeoutError:
        raise QuotexTimeoutError(f"get_balance timed out after {timeout}s")
```

Important: keep the existing `timeout` parameter handling and surrounding logic intact. The change is only inside the polling loop region.

- [ ] **Step 3: Run surface test**

```bash
pytest tests/test_api_surface.py -v
```

Expected: pass (signature unchanged).

- [ ] **Step 4: Run all unit tests**

```bash
pytest tests/test_waits.py tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/stable_api.py
git commit -m "refactor(stable_api): replace get_balance polling with WaitableSlot"
```

---

### Task 2.4: Migrate edit_practice_balance polling

**Files:**
- Modify: `pyquotex/api.py` (around line 250 where `training_balance_edit_request` is set — find with grep)
- Modify: `pyquotex/stable_api.py` line 658 (`while self.api.training_balance_edit_request is None`)

- [ ] **Step 1: Find the producer**

```bash
grep -n "training_balance_edit_request" pyquotex/api.py
```

Locate where `self.training_balance_edit_request = ...` is assigned in `_on_message`.

- [ ] **Step 2: Add slot.set() next to assignment**

After the assignment, add:

```python
                    self.slots.training_balance_edit.set(self.training_balance_edit_request)
```

- [ ] **Step 3: Replace consumer polling**

In `pyquotex/stable_api.py`, locate line 658. Replace:

```python
        while self.api.training_balance_edit_request is None:
            await asyncio.sleep(0.2)
```

with:

```python
from pyquotex.exceptions import QuotexTimeoutError

if self.api.training_balance_edit_request is None:
    try:
        await self.api.slots.training_balance_edit.wait(timeout=DEFAULT_TIMEOUT)
    except asyncio.TimeoutError:
        raise QuotexTimeoutError(f"edit_practice_balance timed out after {DEFAULT_TIMEOUT}s")
```

- [ ] **Step 4: Run all unit tests**

```bash
pytest tests/test_waits.py tests/test_api_surface.py tests/test_import_compat.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/api.py pyquotex/stable_api.py
git commit -m "refactor(stable_api): replace edit_practice_balance polling with slot"
```

---

### Task 2.5: Migrate buy / sell_option / pending confirmation polling

**Files:**
- Modify: `pyquotex/api.py` (find producer at `self.pending_id = data.get("id")` around line 366; also find `buy_id` and `sold_options_respond` assignments)
- Modify: `pyquotex/stable_api.py` around lines 1149 (pending), 1153 (buy), 1182 (sell), 1185 (sell)

- [ ] **Step 1: Audit producers**

```bash
grep -n "self.buy_id\|self.pending_id\|self.sold_options_respond" pyquotex/api.py
```

For each assignment in `_on_message`, fire the matching slot:
- `self.buy_id = …` → `self.slots.pending_confirm.set({"id": self.buy_id})` if reused for buy, or use a dedicated `buy_confirm` slot (preferred — add to `SlotRegistry`).
- `self.pending_id = …` → `self.slots.pending_confirm.set({"id": self.pending_id})`
- `self.sold_options_respond = …` → `self.slots.sold_option_confirm.set(self.sold_options_respond)`

- [ ] **Step 2: Add buy_confirm slot to SlotRegistry**

In `pyquotex/_api/_waits.py`, inside `SlotRegistry.__init__`, after `self.pending_confirm`:

```python
        self.buy_confirm: WaitableSlot[dict] = WaitableSlot()
```

- [ ] **Step 3: Update producers in pyquotex/api.py**

After each producer assignment, add the matching `.set()` call as listed in Step 1.

- [ ] **Step 4: Replace consumer polling for `buy`**

In `pyquotex/stable_api.py` `buy()` method, locate the polling loop (around line 1153). Replace:

```python
        while await self.check_connect() and self.api.buy_id is None:
            await asyncio.sleep(0.2)
```

with:

```python
from pyquotex.exceptions import QuotexTimeoutError

if self.api.buy_id is None:
    try:
        await self.api.slots.buy_confirm.wait(timeout=DEFAULT_TIMEOUT)
    except asyncio.TimeoutError:
        raise QuotexTimeoutError(f"buy timed out after {DEFAULT_TIMEOUT}s")
```

- [ ] **Step 5: Replace consumer polling for `open_pending`**

Apply the same pattern to the `open_pending` method polling on `self.api.pending_id` (around line 1149).

- [ ] **Step 6: Replace consumer polling for `sell_option`**

Apply the same pattern to `sell_option` polling on `self.api.sold_options_respond` (around line 1185).

- [ ] **Step 7: Run unit tests**

```bash
pytest tests/test_waits.py tests/test_api_surface.py tests/test_import_compat.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add pyquotex/_api/_waits.py pyquotex/api.py pyquotex/stable_api.py
git commit -m "refactor: replace buy/sell/pending confirmation polling with slots"
```

---

### Task 2.6: Migrate get_candle_v2 polling

**Files:**
- Modify: `pyquotex/api.py` around line 295 (`self.candle_v2_data[asset] = data`)
- Modify: `pyquotex/stable_api.py` line 533

- [ ] **Step 1: Convert keyed slot for candle_v2**

In `pyquotex/_api/_waits.py`, add to `SlotRegistry`:

```python
        self._candle_v2: dict[str, WaitableSlot[dict]] = {}

    def candle_v2(self, asset: str) -> WaitableSlot[dict]:
        slot = self._candle_v2.get(asset)
        if slot is None:
            slot = WaitableSlot()
            self._candle_v2[asset] = slot
        return slot

    def release_candle_v2(self, asset: str) -> None:
        self._candle_v2.pop(asset, None)
```

- [ ] **Step 2: Fire from producer**

In `pyquotex/api.py` around line 295, after `self.candle_v2_data[asset] = data`, add:

```python
                        self.slots.candle_v2(asset).set(data)
```

- [ ] **Step 3: Replace consumer**

In `pyquotex/stable_api.py` around line 533, replace:

```python
        while self.api.candle_v2_data[asset] is None:
            await asyncio.sleep(0.2)
```

with:

```python
from pyquotex.exceptions import QuotexTimeoutError

if self.api.candle_v2_data.get(asset) is None:
    try:
        await self.api.slots.candle_v2(asset).wait(timeout=DEFAULT_TIMEOUT)
    except asyncio.TimeoutError:
        raise QuotexTimeoutError(f"get_candle_v2({asset}) timed out after {DEFAULT_TIMEOUT}s")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_waits.py tests/test_api_surface.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/_api/_waits.py pyquotex/api.py pyquotex/stable_api.py
git commit -m "refactor: replace candle_v2 polling with keyed slot"
```

---

### Task 2.7: Migrate historical_candles polling

**Files:**
- Modify: `pyquotex/api.py` around line 105 / wherever `historical_candles` is mutated
- Modify: `pyquotex/stable_api.py` line 509 (`while await self.check_connect() and self.api.historical_candles is None`)

- [ ] **Step 1: Locate producer**

```bash
grep -n "self.historical_candles" pyquotex/api.py
```

Find the assignment inside `_on_message`. After the assignment add `self.slots.historical_ready.set(self.historical_candles)`.

- [ ] **Step 2: Replace consumer polling**

In `pyquotex/stable_api.py` around line 509, replace:

```python
        while await self.check_connect() and self.api.historical_candles is None:
            await asyncio.sleep(0.1)
```

with:

```python
from pyquotex.exceptions import QuotexTimeoutError

if self.api.historical_candles is None:
    try:
        await self.api.slots.historical_ready.wait(timeout=DEFAULT_TIMEOUT)
    except asyncio.TimeoutError:
        raise QuotexTimeoutError(f"historical_candles wait timed out after {DEFAULT_TIMEOUT}s")
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_waits.py tests/test_api_surface.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add pyquotex/api.py pyquotex/stable_api.py
git commit -m "refactor: replace historical_candles polling with slot"
```

---

### Task 2.8: Migrate remaining stable_api polling loops

**Files:**
- Modify: `pyquotex/stable_api.py` lines 114, 183, 663, 934, 939, 1031, 1256

- [ ] **Step 1: Audit remaining loops**

```bash
grep -n "await asyncio.sleep" pyquotex/stable_api.py
```

For each remaining loop with a `while … is None` or `while not …` pattern, classify:
- If a clear WS event can fire it → fire a slot from `_on_message` and `await self.api.slots.<name>.wait(timeout=…)` at the consumer.
- If the state cannot be signaled cleanly → use `wait_until(predicate, timeout=DEFAULT_TIMEOUT)` from `_waits.py`.

- [ ] **Step 2: Migrate `check_connect` waits (lines 114, 183)**

These wait for `self.api.state.status` to become `CONNECTED`. Add to `SlotRegistry` (already present as `auth_status`) and wire from the WS open / auth handler. Then replace the polling with `await self.api.slots.auth_status.wait(timeout=DEFAULT_TIMEOUT)`.

If wiring requires changes to `_on_open` or auth flow that are out of scope, fall back to `await wait_until(lambda: self.api.state.status == WebsocketStatus.CONNECTED, timeout=DEFAULT_TIMEOUT)` and document.

- [ ] **Step 3: Migrate `calculate_indicator` waits (lines 934, 939, 1031)**

Use `wait_until(lambda: <predicate over self.api.realtime_candles[asset]>, timeout=DEFAULT_TIMEOUT)` because indicator readiness depends on accumulated data, not a single message. Document this as the documented fallback case.

- [ ] **Step 4: Migrate `check_win` (line 1256)**

Use the keyed `win_result` slot in `SlotRegistry`. Find the WS handler that updates win/loss state and fire `self.slots.win_result(operation_id).set(result)`. Replace polling with:

```python
from pyquotex.exceptions import QuotexTimeoutError

try:
    result = await self.api.slots.win_result(operation_id).wait(timeout=timeout)
except asyncio.TimeoutError:
    raise QuotexTimeoutError(f"check_win({operation_id}) timed out after {timeout}s")
finally:
    self.api.slots.release_win_result(operation_id)
```

- [ ] **Step 5: Run all tests**

```bash
pytest tests/test_waits.py tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py -v
```

Expected: all pass.

- [ ] **Step 6: Verify no polling loops remain in stable_api**

```bash
grep -c "while.*is None.*sleep\|while not.*sleep" pyquotex/stable_api.py
```

Expected: 0. If non-zero, audit the remaining lines and decide per the classification rule in Step 1.

- [ ] **Step 7: Commit**

```bash
git add pyquotex/api.py pyquotex/stable_api.py pyquotex/_api/_waits.py
git commit -m "refactor: replace remaining stable_api polling with event waits"
```

---

### Task 2.9: Replace network/login sleeps with exponential backoff

**Files:**
- Modify: `pyquotex/network/login.py` lines around 98 and 170
- Modify: `pyquotex/api.py` line 139 (the `sleep(5)` after error)

- [ ] **Step 1: Read current retry logic**

```bash
sed -n '90,110p' pyquotex/network/login.py
sed -n '160,180p' pyquotex/network/login.py
sed -n '130,145p' pyquotex/api.py
```

- [ ] **Step 2: Add a small backoff helper**

Append to `pyquotex/_api/_waits.py`:

```python
import random


async def backoff_sleep(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.1,
) -> None:
    """Sleep for an exponentially increasing duration with jitter.

    attempt is zero-indexed (0, 1, 2, ...).
    """
    delay = min(cap, base * (2**attempt))
    delay = delay * (1.0 + random.uniform(-jitter, jitter))
    await asyncio.sleep(max(0.0, delay))
```

- [ ] **Step 3: Add a test**

Append to `tests/test_waits.py`:

```python
@pytest.mark.asyncio
async def test_backoff_sleep_grows_and_caps():
    from pyquotex._api._waits import backoff_sleep
    import time

    # attempt=0 should sleep ~1s; cap to a small value for test speed
    start = time.monotonic()
    await backoff_sleep(0, base=0.01, cap=0.1, jitter=0)
    assert (time.monotonic() - start) >= 0.009

    start = time.monotonic()
    await backoff_sleep(5, base=0.01, cap=0.05, jitter=0)
    elapsed = time.monotonic() - start
    assert 0.04 <= elapsed <= 0.15  # cap respected
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_waits.py -v
```

Expected: all pass.

- [ ] **Step 5: Use backoff in login retries**

In `pyquotex/network/login.py`, replace `await asyncio.sleep(1)` calls inside retry loops with `await backoff_sleep(attempt)` where `attempt` is the retry-loop counter. Import: `from pyquotex._api._waits import backoff_sleep`.

If the surrounding loop does not track an `attempt` index, introduce one: `for attempt in range(MAX_RETRIES): ...`.

- [ ] **Step 6: Use backoff in api.py reconnect**

In `pyquotex/api.py` around line 139, replace `await asyncio.sleep(5)` with `await backoff_sleep(retry_count)` where `retry_count` is the surrounding retry counter (introduce if absent).

- [ ] **Step 7: Run all tests**

```bash
pytest tests/test_waits.py tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add pyquotex/_api/_waits.py pyquotex/network/login.py pyquotex/api.py tests/test_waits.py
git commit -m "refactor: use exponential backoff for login/reconnect retries"
```

---

## Phase 3 — Extract Mixins

> **General pattern for each mixin task:**
> 1. Create the mixin file with the method definitions cut from `stable_api.py`.
> 2. Add the mixin to `Quotex`'s base list.
> 3. Remove the same methods from `stable_api.py`.
> 4. Run `pytest tests/test_api_surface.py tests/test_import_compat.py` — surface must remain identical.

### Task 3.1: Extract AccountMixin

**Files:**
- Create: `pyquotex/_api/account.py`
- Modify: `pyquotex/stable_api.py`

**Methods to move:** `connect`, `reconnect`, `get_balance`, `get_profile`, `get_server_time`, `change_account`, `change_time_offset`, `set_account_mode`, `edit_practice_balance`, `store_settings_apply`, `start_remaing_time`, `set_session` *(keep set_session in Quotex if it touches init state — verify)*.

- [ ] **Step 1: Create the mixin skeleton**

Create `pyquotex/_api/account.py`:

```python
"""Account-related methods extracted from Quotex.

This mixin is composed into Quotex via multiple inheritance. It uses
self.api, self.session_data, etc. (set up in Quotex.__init__).
"""

from __future__ import annotations

import asyncio
from typing import Any

from pyquotex.exceptions import QuotexTimeoutError


DEFAULT_TIMEOUT = 30


class AccountMixin:
    # Methods are moved here from stable_api.py in Step 2.
    pass
```

- [ ] **Step 2: Move each method**

For each method in the list above:
1. Open `pyquotex/stable_api.py`, find the method, copy its full source (def + body, including decorators).
2. Paste into `pyquotex/_api/account.py` under `class AccountMixin`, preserving indentation.
3. Delete the method from `pyquotex/stable_api.py`.

Imports needed in `account.py` (add as you discover them while moving methods): `from pyquotex.utils.account_type import AccountType`, `from pyquotex.config import update_session`, `from pyquotex.global_value import AuthStatus`, etc. Inspect each moved method to determine which symbols it references and ensure they are imported in `account.py`.

- [ ] **Step 3: Compose AccountMixin into Quotex**

In `pyquotex/stable_api.py`, change the class declaration from:

```python
class Quotex(OptimizedQuotexMixin):
```

to:

```python
from pyquotex._api.account import AccountMixin

class Quotex(AccountMixin, OptimizedQuotexMixin):
```

- [ ] **Step 4: Run surface tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py -v
```

Expected: all pass — surface unchanged.

- [ ] **Step 5: Run CLI smoke**

```bash
pytest tests/test_cli_smoke.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add pyquotex/_api/account.py pyquotex/stable_api.py
git commit -m "refactor(stable_api): extract AccountMixin into _api.account"
```

---

### Task 3.2: Extract TradingMixin

**Files:**
- Create: `pyquotex/_api/trading.py`
- Modify: `pyquotex/stable_api.py`

**Methods to move:** `buy`, `sell_option`, `open_pending`, `check_win`, `get_result`, `get_profit`, `get_history` (trade history, not candles).

- [ ] **Step 1: Create the mixin**

Create `pyquotex/_api/trading.py`:

```python
"""Trading methods (buy, sell, pending, results) extracted from Quotex."""

from __future__ import annotations

import asyncio
from typing import Any

from pyquotex.exceptions import QuotexTimeoutError


DEFAULT_TIMEOUT = 30


class TradingMixin:
    pass
```

- [ ] **Step 2: Move each method**

Same procedure as Task 3.1 Step 2 for the methods listed above. Watch for usage of `expiration` module and `_request_counter` — add those imports if needed:

```python
from pyquotex import expiration
```

The `_request_counter` from `stable_api.py` should stay there (it is module-scoped); the mixin reads it via `from pyquotex.stable_api import _request_counter` only if a method requires it. Prefer moving `_request_counter` to `pyquotex/_api/_waits.py` or a new `pyquotex/_api/_state.py` if it is used by multiple mixins. For this task, leave it in `stable_api.py` and import it from there if needed.

- [ ] **Step 3: Compose**

In `pyquotex/stable_api.py`:

```python
from pyquotex._api.account import AccountMixin
from pyquotex._api.trading import TradingMixin

class Quotex(AccountMixin, TradingMixin, OptimizedQuotexMixin):
```

- [ ] **Step 4: Run surface tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/_api/trading.py pyquotex/stable_api.py
git commit -m "refactor(stable_api): extract TradingMixin into _api.trading"
```

---

### Task 3.3: Extract HistoryMixin

**Files:**
- Create: `pyquotex/_api/history.py`
- Modify: `pyquotex/stable_api.py`

**Methods to move:** `get_candles`, `_fetch_historical_batch`, `_parse_historical_candles`, `get_historical_candles`, `get_candles_deep`, `get_candle_v2`, `get_history_line`, `get_trader_history`, `prepare_candles`.

- [ ] **Step 1: Create the mixin**

Create `pyquotex/_api/history.py`:

```python
"""Candle and historical data methods extracted from Quotex."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pyquotex.exceptions import QuotexTimeoutError
from pyquotex.utils.processor import (
    calculate_candles,
    process_candles_v2,
    merge_candles,
    aggregate_candle,
)


DEFAULT_TIMEOUT = 30


class HistoryMixin:
    pass
```

- [ ] **Step 2: Move methods**

Same procedure. The historical methods reference `_request_counter` and `process_candles_v2`; ensure imports cover what each method needs.

- [ ] **Step 3: Compose**

```python
from pyquotex._api.history import HistoryMixin

class Quotex(AccountMixin, TradingMixin, HistoryMixin, OptimizedQuotexMixin):
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/_api/history.py pyquotex/stable_api.py
git commit -m "refactor(stable_api): extract HistoryMixin into _api.history"
```

---

### Task 3.4: Extract RealtimeMixin

**Files:**
- Create: `pyquotex/_api/realtime.py`
- Modify: `pyquotex/stable_api.py`

**Methods to move:** `start_candles_stream`, `stop_candles_stream`, `start_candles_one_stream`, `start_candles_all_size_stream`, `start_signals_data`, `start_realtime_price`, `start_realtime_sentiment`, `start_realtime_candle`, `get_realtime_candles`, `get_realtime_sentiment`, `get_realtime_price`, `subscribe_indicator`, `calculate_indicator`, `start_mood_stream`, `opening_closing_current_candle`, `get_signal_data`.

- [ ] **Step 1: Create the mixin**

Create `pyquotex/_api/realtime.py`:

```python
"""Realtime streaming methods extracted from Quotex."""

from __future__ import annotations

import asyncio
from typing import Any

from pyquotex.exceptions import QuotexTimeoutError
from pyquotex.utils.indicators import TechnicalIndicators
from pyquotex.utils.processor import process_tick


DEFAULT_TIMEOUT = 30


class RealtimeMixin:
    pass
```

- [ ] **Step 2: Move methods**

Same procedure.

- [ ] **Step 3: Compose**

```python
from pyquotex._api.realtime import RealtimeMixin

class Quotex(AccountMixin, TradingMixin, HistoryMixin, RealtimeMixin, OptimizedQuotexMixin):
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/_api/realtime.py pyquotex/stable_api.py
git commit -m "refactor(stable_api): extract RealtimeMixin into _api.realtime"
```

---

### Task 3.5: Extract AssetsMixin

**Files:**
- Create: `pyquotex/_api/assets.py`
- Modify: `pyquotex/stable_api.py`

**Methods to move:** `get_instruments`, `get_all_asset_name`, `get_available_asset`, `check_asset_open`, `get_all_assets`, `get_payment`, `get_payout_by_asset`, `re_subscribe_stream`.

- [ ] **Step 1: Create the mixin**

Create `pyquotex/_api/assets.py`:

```python
"""Asset metadata and payout methods extracted from Quotex."""

from __future__ import annotations

import asyncio
from typing import Any


DEFAULT_TIMEOUT = 30


class AssetsMixin:
    pass
```

- [ ] **Step 2: Move methods**

Same procedure.

- [ ] **Step 3: Compose final Quotex**

```python
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
    OptimizedQuotexMixin,
):
```

- [ ] **Step 4: Confirm stable_api.py size**

```bash
wc -l pyquotex/stable_api.py
```

Expected: ~200-300 lines (down from 1573). If significantly more, audit for dead code or methods missed.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py tests/test_waits.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyquotex/_api/assets.py pyquotex/stable_api.py
git commit -m "refactor(stable_api): extract AssetsMixin into _api.assets"
```

---

## Phase 4 — CLI Modularization

### Task 4.1: Scaffold pyquotex/cli/

**Files:**
- Create: `pyquotex/cli/__init__.py`
- Create: `pyquotex/cli/parser.py`
- Create: `pyquotex/cli/runtime.py`
- Create: `pyquotex/cli/formatters.py`

- [ ] **Step 1: Create the package marker**

Create `pyquotex/cli/__init__.py`:

```python
"""Command-line interface for pyquotex."""
```

- [ ] **Step 2: Move make_parser into cli/parser.py**

Read [app.py](../../../app.py) lines 99-360 to confirm the bounds of `make_parser()`. Copy that function (and any module-level constants it uses) into `pyquotex/cli/parser.py` with an appropriate header docstring:

```python
"""argparse parser construction for pyquotex CLI."""

import argparse

# ... copy make_parser() and its helpers verbatim ...
```

Do not delete the function from `app.py` yet (Task 4.4 does that).

- [ ] **Step 3: Move runtime helpers into cli/runtime.py**

From `app.py`, copy `on_otp` (lines 82-98), `connect_with_retry` (lines 363-409), `_is_demo` (lines 410-415) into `pyquotex/cli/runtime.py`. Add the imports they need (re-read the original file to verify).

```python
"""CLI runtime helpers: connection retry, OTP prompt, demo detection."""
# ... copied content with original imports preserved ...
```

- [ ] **Step 4: Move formatters into cli/formatters.py**

From `app.py`, copy `_balance_table` (lines 416-441), `_print_candles_table` (lines 1249-1296), `_save_candles_csv` (lines 1297-1311) into `pyquotex/cli/formatters.py`:

```python
"""Output formatting helpers shared by CLI commands."""
# ... copied content ...
```

- [ ] **Step 5: Verify imports resolve**

```bash
python -c "from pyquotex.cli.parser import make_parser; print('parser ok')"
python -c "from pyquotex.cli.runtime import connect_with_retry, on_otp; print('runtime ok')"
python -c "from pyquotex.cli.formatters import _balance_table; print('formatters ok')"
```

Expected: three `... ok` lines.

- [ ] **Step 6: Commit**

```bash
git add pyquotex/cli/
git commit -m "feat(cli): scaffold pyquotex.cli package with parser/runtime/formatters"
```

---

### Task 4.2: Move command functions into cli/commands/

**Files:**
- Create: `pyquotex/cli/commands/__init__.py`
- Create: `pyquotex/cli/commands/account.py`
- Create: `pyquotex/cli/commands/market.py`
- Create: `pyquotex/cli/commands/candles.py`
- Create: `pyquotex/cli/commands/realtime.py`
- Create: `pyquotex/cli/commands/trading.py`
- Create: `pyquotex/cli/commands/analysis.py`
- Create: `pyquotex/cli/commands/diagnostics.py`

**Mapping (line numbers refer to current app.py):**

| File | Commands (with original app.py line) |
|---|---|
| `account.py` | `cmd_login` (442), `cmd_balance` (461), `cmd_server_time` (470), `cmd_set_demo_balance` (486), `cmd_settings` (502) |
| `market.py` | `cmd_assets` (531), `cmd_payout` (563), `cmd_payout_asset` (601) |
| `candles.py` | `cmd_candles` (625), `cmd_candles_v2` (640), `cmd_candles_deep` (653), `cmd_history_line` (700), `cmd_candle_info` (721) |
| `realtime.py` | `cmd_realtime_price` (749), `cmd_realtime_sentiment` (777), `cmd_realtime_candle` (808) |
| `trading.py` | `cmd_buy` (840), `cmd_sell` (926), `cmd_pending` (946), `cmd_check` (987), `cmd_result` (1026) |
| `analysis.py` | `cmd_signals` (1044), `cmd_history` (1072), `cmd_indicator` (1141), `cmd_monitor` (1186), `cmd_strategy` (1224) |
| `diagnostics.py` | `cmd_test_all` (1312) |

- [ ] **Step 1: Create the registry skeleton**

Create `pyquotex/cli/commands/__init__.py`:

```python
"""Registry mapping CLI command names to async handler functions."""

from pyquotex.cli.commands.account import (
    cmd_login,
    cmd_balance,
    cmd_server_time,
    cmd_set_demo_balance,
    cmd_settings,
)
from pyquotex.cli.commands.market import (
    cmd_assets,
    cmd_payout,
    cmd_payout_asset,
)
from pyquotex.cli.commands.candles import (
    cmd_candles,
    cmd_candles_v2,
    cmd_candles_deep,
    cmd_history_line,
    cmd_candle_info,
)
from pyquotex.cli.commands.realtime import (
    cmd_realtime_price,
    cmd_realtime_sentiment,
    cmd_realtime_candle,
)
from pyquotex.cli.commands.trading import (
    cmd_buy,
    cmd_sell,
    cmd_pending,
    cmd_check,
    cmd_result,
)
from pyquotex.cli.commands.analysis import (
    cmd_signals,
    cmd_history,
    cmd_indicator,
    cmd_monitor,
    cmd_strategy,
)
from pyquotex.cli.commands.diagnostics import cmd_test_all


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
```

> Verify the exact command names against `make_parser()` subparsers. If a name differs (e.g. `set_demo_balance` vs `set-demo-balance`), correct the dict.

- [ ] **Step 2: Move each command group**

For each row in the mapping table, create the corresponding file (e.g., `pyquotex/cli/commands/account.py`) starting with a docstring and the imports needed. Copy each `cmd_*` function body verbatim from `app.py`. Add at top:

```python
"""<group> CLI command handlers."""

import argparse
from rich.console import Console
from rich.table import Table

from pyquotex.stable_api import Quotex
from pyquotex.cli.formatters import _balance_table  # if used
from pyquotex.cli.runtime import _is_demo  # if used
# ... add any other imports each cmd uses ...

console = Console()
```

> The exact set of imports for each commands file depends on which symbols the moved functions reference. Inspect each function's body and add the matching imports.

- [ ] **Step 3: Verify each new module imports cleanly**

```bash
for m in account market candles realtime trading analysis diagnostics; do
  python -c "import pyquotex.cli.commands.$m; print('$m ok')"
done
```

Expected: 7 `... ok` lines.

- [ ] **Step 4: Verify the registry**

```bash
python -c "from pyquotex.cli.commands import COMMAND_REGISTRY; print(len(COMMAND_REGISTRY), sorted(COMMAND_REGISTRY))"
```

Expected: a count of 27 (or matching the actual number of `cmd_*` functions in `app.py`) and the sorted command list.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/cli/commands/
git commit -m "feat(cli): move cmd_* functions into pyquotex.cli.commands"
```

---

### Task 4.3: Wire pyquotex/cli/__main__.py

**Files:**
- Create: `pyquotex/cli/__main__.py`

- [ ] **Step 1: Read the existing main() in app.py**

```bash
sed -n '1407,1434p' app.py
```

Note how it builds the parser, creates the client, calls the command handler, closes.

- [ ] **Step 2: Write the new entry point**

Create `pyquotex/cli/__main__.py`:

```python
"""pyquotex CLI entry point. Run with `python -m pyquotex` or via app.py."""

import asyncio
import sys

from pyquotex.cli.commands import COMMAND_REGISTRY
from pyquotex.cli.parser import make_parser
from pyquotex.cli.runtime import connect_with_retry, on_otp
from pyquotex.stable_api import Quotex


async def main() -> None:
    parser = make_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    client = Quotex(
        email=args.email,
        password=args.password,
        lang=args.lang,
        on_otp_callback=on_otp,
    )

    await connect_with_retry(client)
    try:
        handler = COMMAND_REGISTRY.get(args.command)
        if handler is None:
            print(f"Unknown command: {args.command}", file=sys.stderr)
            parser.print_help()
            sys.exit(2)
        await handler(client, args)
    finally:
        await client.close()


def cli_main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
```

> **Important:** the constructor arguments must match what `app.py` currently passes. Re-read `app.py` line 1407-1434 and adjust this `Quotex(...)` call to use the same args.

- [ ] **Step 3: Test the new entry point**

```bash
python -m pyquotex --help
```

Expected: parser help output listing all commands.

- [ ] **Step 4: Run CLI smoke tests**

```bash
pytest tests/test_cli_smoke.py -v
```

Expected: all pass. If `test_module_invocation_help_runs` was previously skipped, un-skip it now and confirm it passes.

- [ ] **Step 5: Commit**

```bash
git add pyquotex/cli/__main__.py
git commit -m "feat(cli): add pyquotex.cli.__main__ entry point"
```

---

### Task 4.4: Reduce app.py to a shim

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Replace app.py content**

Replace the entire content of `app.py` with:

```python
"""Compatibility shim. The CLI now lives in pyquotex.cli.

Kept so that documented usage `python app.py <command>` continues to work.
"""

from pyquotex.cli.__main__ import cli_main

if __name__ == "__main__":
    cli_main()
```

- [ ] **Step 2: Verify app.py still works**

```bash
python app.py --help
```

Expected: parser help output (same as before the refactor).

- [ ] **Step 3: Verify line count**

```bash
wc -l app.py
```

Expected: ~7 lines.

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py tests/test_waits.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "refactor(cli): reduce app.py to a 7-line shim to pyquotex.cli"
```

---

## Phase 5 — Cleanup

### Task 5.1: Remove dead code from stable_api.py

**Files:**
- Modify: `pyquotex/stable_api.py`

- [ ] **Step 1: Audit unused imports**

```bash
python -c "
import ast, sys
tree = ast.parse(open('pyquotex/stable_api.py').read())
imports = []
for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            imports.append(alias.asname or alias.name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            imports.append(alias.asname or alias.name.split('.')[0])
src = open('pyquotex/stable_api.py').read()
for name in imports:
    count = src.count(name)
    if count <= 1:
        print(f'POSSIBLY UNUSED: {name}')
"
```

Review the output. For each `POSSIBLY UNUSED` symbol, confirm by searching usage outside its `import` line and remove it from the import block if truly unused.

- [ ] **Step 2: Remove**

Edit `pyquotex/stable_api.py` and delete unused imports identified in Step 1.

- [ ] **Step 3: Re-run all tests**

```bash
pytest tests/test_api_surface.py tests/test_import_compat.py tests/test_cli_smoke.py tests/test_waits.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add pyquotex/stable_api.py
git commit -m "chore(stable_api): remove dead imports after mixin extraction"
```

---

### Task 5.2: Bump version

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump version**

In `pyproject.toml`, change:

```toml
version = "1.0.3"
```

to:

```toml
version = "1.1.0"
```

- [ ] **Step 2: Verify**

```bash
grep '^version' pyproject.toml
```

Expected: `version = "1.1.0"`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version to 1.1.0"
```

---

### Task 5.3: Update README (optional)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an Architecture note**

In `README.md`, add a short section after "🎯 Objetivo" with the new layout — keep it brief, 5-10 lines. Do not rewrite the README; just acknowledge the new internal structure:

```markdown
## 🏗 Arquitectura interna

- `pyquotex.stable_api.Quotex` — facade público (la API que usás).
- `pyquotex._api/*` — mixins por dominio (account, trading, history, realtime, assets).
- `pyquotex.cli/*` — entrada de comandos del CLI.
- `pyquotex.api.QuotexAPI` — cliente WebSocket subyacente.

La interfaz pública no cambia entre 1.0.x y 1.1.0.
```

- [ ] **Step 2: Verify rendering visually if possible**

```bash
head -60 README.md
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: note new internal architecture in README"
```

---

### Task 5.4: Final verification and merge prep

**Files:** none

- [ ] **Step 1: Run the full test suite (excluding live-credential tests)**

```bash
pytest tests/ -k "not test_buy and not test_win and not test_login and not test_tournament" -v
```

Expected: all selected tests pass. If anything fails, debug before merging.

- [ ] **Step 2: Confirm file sizes**

```bash
wc -l pyquotex/stable_api.py app.py
```

Expected:
- `pyquotex/stable_api.py`: 200-300 lines (was 1573).
- `app.py`: ~7 lines (was 1435).

- [ ] **Step 3: Confirm no polling loops remain in stable_api**

```bash
grep -n "while.*is None.*sleep\|while not.*sleep" pyquotex/stable_api.py pyquotex/_api/*.py
```

Expected: no output, or only the documented `wait_until`-based fallbacks in `_api/realtime.py` for `calculate_indicator`.

- [ ] **Step 4: View summary of all commits on the branch**

```bash
git log --oneline master..refactor/architecture
```

Expected: 16-20 commits across phases 0-5.

- [ ] **Step 5: Prepare merge**

```bash
git checkout master
git merge --no-ff refactor/architecture -m "refactor: split stable_api into mixins, modularize CLI, event-driven waits"
git log --oneline -5
```

Do **not** push without explicit user confirmation.

---

## Acceptance Criteria

- [ ] `pytest tests/test_api_surface.py` passes — no public methods removed.
- [ ] `pytest tests/test_import_compat.py` passes — legacy imports work.
- [ ] `pytest tests/test_cli_smoke.py` passes — `app.py --help` and `python -m pyquotex --help` both succeed.
- [ ] `pytest tests/test_waits.py` passes — wait primitives behave correctly.
- [ ] `wc -l pyquotex/stable_api.py` shows < 300 lines.
- [ ] `wc -l app.py` shows < 20 lines.
- [ ] No `while … sleep` polling loops in `pyquotex/stable_api.py` (only documented `wait_until` fallbacks allowed in `_api/realtime.py`).
- [ ] `pyproject.toml` version is `1.1.0`.

## Notes for the Implementer

- **Move methods, don't rewrite them.** The body of every moved method should be byte-identical to before. Only `self` references and shared imports change.
- **Avoid renaming.** No method names change. No parameter names change. Even `self.api`, `self.session_data`, etc. stay identical.
- **Commit per task.** Do not batch multiple tasks into one commit. Small commits make Phase 2 (the highest-risk phase) easy to bisect.
- **Run the surface test after every mixin extraction.** It is the fastest way to catch an accidental method drop.
- **If a polling migration in Phase 2 cannot find a clean producer signal**, fall back to `wait_until(predicate, timeout=…)`. Document the case in a comment and move on — it is still better than naked `sleep`.
- **Integration tests requiring credentials** (`test_buy.py`, `test_win.py`, `test_login.py`, `test_tournament.py`) are not gated by CI. Run them manually if you have credentials; otherwise rely on the surface + smoke tests.
