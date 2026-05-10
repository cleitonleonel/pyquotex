# Winning-Streak Sizing + Eager Parser Warm-Up — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Paroli-style winning-streak ladder that compounds the next channel signal's stake on consecutive wins (resets on loss or max level), plus eager parser warm-up so `cached_parser_count` reaches `enabled_parser_count` at startup instead of lazily on first message.

**Architecture:** Two new opt-in fields on `parser_config` + two new state fields on `martingale_state`; existing martingale logic untouched. The risk-gate sizing branch picks the streak stake (`ceil(last_payout)`) when `current_win_streak > 0`, otherwise the existing martingale ladder, otherwise base. New `Pipeline.warm_up()` is invoked at lifespan startup and on every parser create/update so the cache is materialised eagerly. Admin-bot notifications get a ladder-state extension so DMs surface streak progress.

**Tech Stack:** Python 3.13 + FastAPI + SQLModel/aiosqlite + structlog + pytest-asyncio (backend); Next.js 15 + TanStack Query (frontend); `uv` for Python, `bun` for JS.

**Spec:** `docs/superpowers/specs/2026-05-10-winning-streak-and-eager-warmup-design.md`

---

## File map

**Modified (backend, in roughly task order):**

- `autotrader/backend/src/autotrader/models/parser_config.py` — 2 new columns
- `autotrader/backend/src/autotrader/db.py` — ALTER TABLE ADD COLUMN migrations
- `autotrader/backend/src/autotrader/models/martingale_state.py` — 2 new columns + record_outcome signature extension + reset_state both-ladder reset
- `autotrader/backend/src/autotrader/services/risk_gate.py` — _round_stake helper + streak sizing branch
- `autotrader/backend/src/autotrader/services/executor.py` — pass new params to record_outcome + extend `_attempt_to_payload` with ladder snapshot
- `autotrader/backend/src/autotrader/routers/parsers.py` — extend `MartingalePayload` schema + `_payload_to_dict` + `_to_response` + call `pipeline.prebuild` after save
- `autotrader/backend/src/autotrader/routers/risk.py` — extend `StreakRow` response
- `autotrader/backend/src/autotrader/routers/telegram.py` — call `pipeline.prebuild` on watch
- `autotrader/backend/src/autotrader/services/pipeline.py` — `warm_up()` + `prebuild()` methods
- `autotrader/backend/src/autotrader/main.py` — lifespan invocation of `pipeline.warm_up()`
- `autotrader/backend/src/autotrader/services/admin_bot_notify.py` — extend `format_trade_placed` / `format_trade_settled`

**Modified (frontend):**

- `autotrader/frontend/lib/api.ts` — extend `MartingalePayload` and `StreakRow` types
- `autotrader/frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx` — add `WinningStreakBlock` component
- `autotrader/frontend/app/dashboard/_components/panel-martingale-streaks.tsx` (or wherever the streaks table renders) — add 2 columns
- `autotrader/frontend/app/dashboard/pipeline/page.tsx` — pipeline streaks table additions

**Modified (tests):**

- `autotrader/backend/tests/test_risk.py` — round-stake helper, win-streak ladder, mid-streak loss → mart recovery, both-ladder coexistence
- `autotrader/backend/tests/test_e2e_elite_scenario.py` — full E2E winning-streak scenario
- `autotrader/backend/tests/test_startup_recovery.py` — eager warm-up: cached count after startup, build-failure tolerance, save-time prebuild
- `autotrader/backend/tests/test_admin_bot.py` — formatter extension assertions
- `autotrader/backend/tests/test_parsers.py` — payload round-trip for new fields

---

## Task 1 — Schema migration: parser_config + martingale_state new columns

**Files:**
- Modify: `autotrader/backend/src/autotrader/models/parser_config.py` (add 2 fields)
- Modify: `autotrader/backend/src/autotrader/models/martingale_state.py` (add 2 fields)
- Modify: `autotrader/backend/src/autotrader/db.py` (ALTER TABLE migrations)

- [ ] **Step 1.1: Add fields to `ParserConfig` SQLModel**

In `autotrader/backend/src/autotrader/models/parser_config.py`, add immediately after the `martingale_auto_recovery` field (around line 78, before the `enabled` field):

```python
    # Winning-streak (Paroli) sizing — opt-in per parser. When True,
    # after a winning trade settles, the **next channel signal** for
    # this parser stakes at ``ceil(martingale_state.last_payout)``
    # instead of base. Compounds up to ``winning_streak_max_level``
    # consecutive wins, then resets to base. A loss at any point also
    # resets to base (loss handling delegates to martingale config).
    #
    # ``last_payout`` (live runtime value) lives on
    # :class:`MartingaleState`; both ladders share that table since
    # they're per-parser counters with identical lifecycle (reset on
    # config edit / manual reset button).
    winning_streak_enabled: bool = Field(default=False, nullable=False)
    winning_streak_max_level: int = Field(default=2, nullable=False)
```

- [ ] **Step 1.2: Add fields to `MartingaleState` SQLModel**

In `autotrader/backend/src/autotrader/models/martingale_state.py`, after the `last_outcome` field (around line 33), add:

```python
    # Winning-streak (Paroli) state. ``current_win_streak`` ticks up
    # on each winning settle; ``last_payout`` records ``stake +
    # profit`` of that winning trade so the next channel signal can
    # size at ``ceil(last_payout)``. Both reset to 0 on a loss or
    # when ``current_win_streak >= winning_streak_max_level`` after
    # a win. At runtime the two ladders are mutually exclusive
    # (loss resets win_streak; win resets current_streak with
    # reset_on_win), so ``current_streak > 0 ⇔ current_win_streak == 0``.
    current_win_streak: int = Field(default=0, nullable=False)
    last_payout: float = Field(default=0.0, nullable=False)
```

- [ ] **Step 1.3: Add migrations to `db.py`**

In `autotrader/backend/src/autotrader/db.py`, immediately after the existing `martingale_auto_recovery` migration block (around line 117) — same shape as that block — add:

```python
    if cols and "winning_streak_enabled" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE parser_configs ADD COLUMN "
                "winning_streak_enabled BOOLEAN NOT NULL DEFAULT 0",
            ),
        )
    if cols and "winning_streak_max_level" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE parser_configs ADD COLUMN "
                "winning_streak_max_level INTEGER NOT NULL DEFAULT 2",
            ),
        )
```

Then immediately after the `global_settings` migration block (around line 195, just before `async def close_db`), add a `martingale_states` migration:

```python
    # martingale_states gained winning-streak columns when Paroli
    # sizing landed. Existing rows default to ``0`` for both, which
    # is the same as having no streak in progress.
    cols = await conn.run_sync(_columns, "martingale_states")
    if cols and "current_win_streak" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE martingale_states ADD COLUMN "
                "current_win_streak INTEGER NOT NULL DEFAULT 0",
            ),
        )
    if cols and "last_payout" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE martingale_states ADD COLUMN "
                "last_payout REAL NOT NULL DEFAULT 0",
            ),
        )
```

- [ ] **Step 1.4: Run the existing test suite to verify nothing regressed**

```bash
cd autotrader/backend
uv run pytest -x 2>&1 | tail -5
```

Expected: same number of tests pass as before this task (319 passed). Existing rows get the new columns at default values; existing behaviour unchanged.

- [ ] **Step 1.5: Commit**

```bash
git add autotrader/backend/src/autotrader/models/parser_config.py \
        autotrader/backend/src/autotrader/models/martingale_state.py \
        autotrader/backend/src/autotrader/db.py
git commit -m "feat(autotrader/db): winning-streak schema fields + migrations

parser_config gains:
  - winning_streak_enabled (bool, default False)
  - winning_streak_max_level (int, default 2)

martingale_states gains:
  - current_win_streak (int, default 0)
  - last_payout (float, default 0.0)

Both pairs land via ALTER TABLE ADD COLUMN at lifespan startup,
matching the existing martingale_auto_recovery migration pattern.
Existing rows are byte-identical at default values; existing
behaviour unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — `_round_stake` helper + Quotex integer-stake constraint

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/risk_gate.py` (add helper)
- Test: `autotrader/backend/tests/test_risk.py` (parametrised)

- [ ] **Step 2.1: Write the failing test**

Append to `autotrader/backend/tests/test_risk.py` (near the top, after the existing imports — find a non-async location next to other test helpers):

```python
import math  # noqa: PLC0415  (used only by the new test below)

import pytest  # already imported but explicit for clarity


@pytest.mark.parametrize(
    "value,expected",
    [
        (5.0, 5),
        (5.5, 6),
        (9.25, 10),       # base $5 + 85% payout
        (10.0, 10),
        (18.50, 19),      # $10 base + 85%
        (37.0, 37),
        (37.01, 38),      # ceiling, never round-down
        (0.0, 0),
        (0.99, 1),
    ],
)
def test_round_stake_ceils_to_int_for_quotex(value: float, expected: int) -> None:
    """Quotex's stake field is integer-only; rounding-up is the
    correct safety direction (never under-stake the operator's
    intended risk). Helper test stays pure / synchronous so it runs
    fast and shows up high in the pytest report."""
    from autotrader.services.risk_gate import _round_stake  # noqa: PLC0415

    assert _round_stake(value) == expected
```

- [ ] **Step 2.2: Run to confirm it fails**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_round_stake_ceils_to_int_for_quotex -v
```

Expected: FAIL with `ImportError: cannot import name '_round_stake'`.

- [ ] **Step 2.3: Implement `_round_stake`**

In `autotrader/backend/src/autotrader/services/risk_gate.py`, immediately after the `_MAX_STAKE` constant (around line 44, before the `RiskDecision` dataclass), add:

```python
import math  # noqa: PLC0415  (top-of-file import — move there if more callers appear)


def _round_stake(value: float) -> int:
    """Quotex requires integer stakes. Always round UP so the
    operator never under-stakes their intended risk profile —
    base $5 with 85% payout produces $9.25 next-step which we
    must size as $10 (not $9). Mirrors how the dashboard label
    'next $10' would show.
    """
    return int(math.ceil(value))
```

If `import math` is not already at the top of the file, move the inline import to the module's import block.

- [ ] **Step 2.4: Verify test passes**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_round_stake_ceils_to_int_for_quotex -v
```

Expected: 9 PASS (one per param row).

- [ ] **Step 2.5: Commit**

```bash
git add autotrader/backend/src/autotrader/services/risk_gate.py \
        autotrader/backend/tests/test_risk.py
git commit -m "feat(autotrader/risk): _round_stake helper for Quotex integer stakes

Quotex rejects fractional stakes; binary-options payouts produce
fractional next-step values (e.g. \$9.25 after a \$5 win at 85%
payout). Always ceiling-round so the operator's risk intent isn't
silently reduced.

Pure helper, parametrised test covers boundary values + the
realistic streak-step sequence \$5 → \$10 → \$19 → \$37.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 — Extend `record_outcome` to track winning-streak state

**Files:**
- Modify: `autotrader/backend/src/autotrader/models/martingale_state.py` (signature + body)
- Modify: `autotrader/backend/src/autotrader/services/executor.py` (caller passes new args)
- Test: `autotrader/backend/tests/test_risk.py` (new tests for win streak)

- [ ] **Step 3.1: Write the failing tests**

Append to `autotrader/backend/tests/test_risk.py` near the existing `_settle_watchers` helper:

```python
async def test_winning_streak_advances_on_win_and_resets_on_loss(
    async_client: httpx.AsyncClient,
) -> None:
    """When winning_streak is enabled, a win advances current_win_streak
    and records last_payout (= stake + profit). A loss resets both."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": False,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
            "winning_streak_enabled": True,
            "winning_streak_max_level": 3,
        },
        default_stake=5.0,
    )
    await _activate()

    # T1: win — streak ticks to 1, last_payout = stake + profit.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415

    async def _read() -> tuple[int, float]:
        async with AsyncSessionLocal() as s:
            row = await s.get(MartingaleState, 1)
            assert row is not None
            return row.current_win_streak, row.last_payout

    win, payout = await _read()
    assert win == 1
    assert payout == pytest.approx(9.25)  # 5.0 stake + 4.25 profit

    # T2: loss — streak resets, last_payout cleared.
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    win, payout = await _read()
    assert win == 0
    assert payout == 0.0


async def test_winning_streak_resets_at_max_level(
    async_client: httpx.AsyncClient,
) -> None:
    """current_win_streak >= max_level after a win triggers reset to 0
    and clears last_payout — next channel signal goes back to base."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": False,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
            "winning_streak_enabled": True,
            "winning_streak_max_level": 2,
        },
        default_stake=5.0,
    )
    await _activate()

    # Two wins in a row hit max=2 → reset.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    WatcherFakeQuotex.next_outcomes = [("win", 8.50)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        row = await s.get(MartingaleState, 1)
        assert row is not None
        assert row.current_win_streak == 0, "max hit → reset"
        assert row.last_payout == 0.0
```

- [ ] **Step 3.2: Run to confirm they fail**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_winning_streak_advances_on_win_and_resets_on_loss -v
```

Expected: FAIL — current `record_outcome` doesn't touch `current_win_streak` or `last_payout`.

- [ ] **Step 3.3: Extend `record_outcome` signature + body**

In `autotrader/backend/src/autotrader/models/martingale_state.py`, replace the `record_outcome` function (around lines 53-93) with:

```python
async def record_outcome(  # noqa: PLR0913  (lots of small flags — alternative is a config struct, deferred)
    session: AsyncSession,
    parser_config_id: int,
    *,
    won: bool,
    last_stake: float,
    last_profit: float,
    max_streak: int,
    reset_on_win: bool,
    winning_streak_enabled: bool,
    winning_streak_max_level: int,
) -> MartingaleState:
    """Tick the loss-recovery + winning-streak counters after a settle.

    Args:
        won: True for win, False for loss.
        last_stake: stake the executor placed on the trade we just settled.
        last_profit: broker-reported profit (positive on win, negative
            on loss). Used to compute ``last_payout = last_stake +
            last_profit`` so the next streak step's stake is honest
            about real broker payout rather than a flat multiplier.
        max_streak: martingale recovery cap. Same semantic as before.
        reset_on_win: when True, a win clears current_streak.
        winning_streak_enabled: when True, advance current_win_streak
            on win + record last_payout. When False, the win-side
            counters stay at 0.
        winning_streak_max_level: cap for current_win_streak. After
            a win that hits the cap, reset to 0 and clear last_payout
            so the next channel signal returns to base. ``0`` means
            uncapped (rare).

    Implementation note: the martingale reset condition is
    ``current_streak > max_streak`` (strictly greater) — this is
    deliberate; see the existing comment block.
    """
    row = await get_state(session, parser_config_id)
    row.last_stake = last_stake
    row.last_outcome = "won" if won else "lost"
    if won:
        # Martingale: existing reset-on-win path.
        if reset_on_win:
            row.current_streak = 0
        # Winning-streak: advance + record payout, with max-level reset.
        if winning_streak_enabled:
            row.current_win_streak += 1
            row.last_payout = last_stake + last_profit
            if (
                winning_streak_max_level > 0
                and row.current_win_streak >= winning_streak_max_level
            ):
                row.current_win_streak = 0
                row.last_payout = 0.0
    else:
        # Loss: martingale advances; winning-streak resets unconditionally.
        row.current_streak += 1
        if max_streak > 0 and row.current_streak > max_streak:
            row.current_streak = 0
        row.current_win_streak = 0
        row.last_payout = 0.0
    row.updated_at = utc_now()
    await session.commit()
    await session.refresh(row)
    return row
```

Note the win-reset uses `>=` (not `>` like martingale's loss path) because for the *winning* ladder, hitting the cap on the current win means "this trade was the cap" — the next channel signal goes back to base. That matches the user's spec walkthrough: T4 wins at $10, win=2 (max), reset → T5 starts at $5.

- [ ] **Step 3.4: Update `reset_state` to clear both ladders**

Replace the `reset_state` function (around lines 96-104) with:

```python
async def reset_state(session: AsyncSession, parser_config_id: int) -> None:
    """Manual reset (for the dashboard). Clears BOTH ladders so the
    operator's "Reset" button is a single clean rewind."""
    row = await session.get(MartingaleState, parser_config_id)
    if row is None:
        return
    row.current_streak = 0
    row.current_win_streak = 0
    row.last_stake = 0.0
    row.last_payout = 0.0
    row.last_outcome = ""
    row.updated_at = utc_now()
    await session.commit()
```

- [ ] **Step 3.5: Update the only caller of `record_outcome` in `executor.py`**

In `autotrader/backend/src/autotrader/services/executor.py`, find the `record_outcome` call inside `_watch_result` (around line 480) and update it to pass the new parameters:

```python
                    new_state = await record_outcome(
                        session,
                        cfg.id or 0,
                        won=(status == "win"),
                        last_stake=updated.stake,
                        last_profit=float(profit),
                        max_streak=cfg.martingale_max_streak,
                        reset_on_win=cfg.martingale_reset_on_win,
                        winning_streak_enabled=cfg.winning_streak_enabled,
                        winning_streak_max_level=cfg.winning_streak_max_level,
                    )
```

- [ ] **Step 3.6: Verify the new tests pass**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_winning_streak_advances_on_win_and_resets_on_loss tests/test_risk.py::test_winning_streak_resets_at_max_level -v
```

Expected: 2 PASS.

- [ ] **Step 3.7: Run the full risk module to catch regressions in the existing martingale tests**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py -v
```

Expected: every test PASS, including the 3 pre-existing `test_martingale_auto_recovery_*` tests.

- [ ] **Step 3.8: Commit**

```bash
git add autotrader/backend/src/autotrader/models/martingale_state.py \
        autotrader/backend/src/autotrader/services/executor.py \
        autotrader/backend/tests/test_risk.py
git commit -m "feat(autotrader/state): record_outcome tracks winning-streak ladder

record_outcome gains winning_streak_enabled / winning_streak_max_level
parameters + last_profit so it can:

  - on win: advance current_win_streak; record last_payout =
    last_stake + last_profit; reset both at max_level (>=).
  - on loss: reset current_win_streak + last_payout to 0
    (martingale advances unchanged).

reset_state now clears BOTH ladders so the dashboard's manual
\"Reset\" button is a single clean rewind. Caller in executor
updated to pass the new args.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 — Risk-gate streak sizing branch

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/risk_gate.py` (sizing logic)
- Test: `autotrader/backend/tests/test_risk.py`

- [ ] **Step 4.1: Write the failing test**

Append to `autotrader/backend/tests/test_risk.py`:

```python
async def test_winning_streak_sizes_next_channel_signal_at_ceil_payout(
    async_client: httpx.AsyncClient,
) -> None:
    """After a winning trade, the NEXT channel signal must stake at
    ceil(last_payout) instead of base. Quotex integer constraint —
    9.25 → 10."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": False,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
            "winning_streak_enabled": True,
            "winning_streak_max_level": 3,
        },
        default_stake=5.0,
    )
    await _activate()

    # T1 wins — primes last_payout = 5 + 4.25 = 9.25.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    # T2 channel signal — must stake at ceil(9.25) = 10, not base 5.
    WatcherFakeQuotex.next_outcomes = [("win", 8.50)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [5, 10], (
        f"second trade must size at ceil(last_payout)=10; got {amounts}"
    )
```

- [ ] **Step 4.2: Confirm it fails**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_winning_streak_sizes_next_channel_signal_at_ceil_payout -v
```

Expected: FAIL — second amount is 5.0 (not 10) because risk_gate doesn't yet honour the streak.

- [ ] **Step 4.3: Add the streak-sizing branch in `risk_gate.evaluate`**

In `autotrader/backend/src/autotrader/services/risk_gate.py`, replace the existing stake-calculation block (lines ~163-176, beginning with the `# Stake — base from signal/config…` comment) with:

```python
    # ------------------------------------------------------------------
    # Stake — base from signal/config, then ladder-shaped.
    #
    # Three priority order:
    #   1. winning_streak active (current_win_streak > 0)
    #          → ceil(state.last_payout)
    #   2. martingale active (current_streak > 0 + martingale_enabled)
    #          → base * multiplier^step
    #   3. otherwise → base
    # The two ladders are mutually exclusive at runtime (record_outcome
    # resets the opposite counter on every settle), so this if/elif
    # tree is total.
    # ------------------------------------------------------------------
    base_stake = (
        signal.stake if signal.stake is not None else parser_config.default_stake
    )
    martingale_step = 0
    win_step = 0
    stake: float = base_stake

    needs_state = (
        parser_config.martingale_enabled
        or parser_config.winning_streak_enabled
    ) and parser_config.id is not None
    if needs_state:
        state = await get_state(session, parser_config.id)
        martingale_step = state.current_streak
        win_step = state.current_win_streak

        if (
            parser_config.winning_streak_enabled
            and win_step > 0
            and state.last_payout > 0
        ):
            stake = float(_round_stake(state.last_payout))
        elif parser_config.martingale_enabled and martingale_step > 0:
            stake = base_stake * (
                parser_config.martingale_multiplier ** martingale_step
            )

    # Final round-up: even when stake = base_stake, Quotex needs an
    # integer. Operators typically configure base as an integer
    # already; this is belt-and-braces.
    stake = float(_round_stake(stake))

    if stake < _MIN_STAKE:
        return RiskDecision(
            outcome="block",
            reason=f"stake {stake:.2f} below minimum {_MIN_STAKE}",
        )
    if stake > _MAX_STAKE:
        return RiskDecision(
            outcome="block",
            reason=f"stake {stake:.2f} above maximum {_MAX_STAKE}",
        )
```

If `_round_stake` isn't already in scope at this point of the file (it's defined nearby in Task 2), the import resolves automatically since both are in the same module.

- [ ] **Step 4.4: Verify the new test passes**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_winning_streak_sizes_next_channel_signal_at_ceil_payout -v
```

Expected: PASS.

- [ ] **Step 4.5: Run the full risk + executor suites to catch regressions**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py tests/test_e2e_elite_scenario.py -v 2>&1 | tail -10
```

Expected: every test PASS. The existing martingale tests (which use integer stakes already) shouldn't be affected by the new `_round_stake(stake)` final pass.

- [ ] **Step 4.6: Commit**

```bash
git add autotrader/backend/src/autotrader/services/risk_gate.py \
        autotrader/backend/tests/test_risk.py
git commit -m "feat(autotrader/risk): winning-streak sizes next signal at ceil(payout)

risk_gate.evaluate now picks the stake from a three-priority
tree:

  1. winning_streak active → ceil(state.last_payout)
  2. martingale active     → base * multiplier^step
  3. otherwise             → base

The ladders are mutually exclusive at runtime (record_outcome
resets the opposite counter on every settle) so the if/elif is
total. Final stake also runs through _round_stake() so the
broker always sees an integer, even on flat / base-stake trades.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5 — API: extend MartingalePayload + StreakRow + reset endpoint

**Files:**
- Modify: `autotrader/backend/src/autotrader/routers/parsers.py` (payload + response + flatten)
- Modify: `autotrader/backend/src/autotrader/routers/risk.py` (StreakRow + reset)
- Test: `autotrader/backend/tests/test_parsers.py`
- Test: `autotrader/backend/tests/test_risk.py` (reset endpoint)

- [ ] **Step 5.1: Write the failing payload round-trip test**

Append to `autotrader/backend/tests/test_parsers.py`:

```python
def test_winning_streak_fields_round_trip_in_martingale_payload(
    client: TestClient,
) -> None:
    """POST a parser with winning_streak_enabled + winning_streak_max_level
    → GET it back → both fields preserved."""
    headers = _login(client)
    body = _new_config_body(
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
            "winning_streak_enabled": True,
            "winning_streak_max_level": 3,
        },
    )
    r = client.post("/parsers/configs", headers=headers, json=body)
    assert r.status_code == 201, r.text
    cfg_id = r.json()["id"]

    r = client.get(f"/parsers/configs/{cfg_id}", headers=headers)
    assert r.status_code == 200
    saved = r.json()
    assert saved["martingale"]["winning_streak_enabled"] is True
    assert saved["martingale"]["winning_streak_max_level"] == 3
```

- [ ] **Step 5.2: Confirm it fails**

```bash
cd autotrader/backend
uv run pytest tests/test_parsers.py::test_winning_streak_fields_round_trip_in_martingale_payload -v
```

Expected: FAIL — Pydantic rejects unknown fields, or the response doesn't include them.

- [ ] **Step 5.3: Extend `MartingalePayload` and conversions**

In `autotrader/backend/src/autotrader/routers/parsers.py`, find the `MartingalePayload` class (around line 61) and add:

```python
class MartingalePayload(BaseModel):
    enabled: bool = False
    multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    max_streak: int = Field(default=5, ge=0, le=20)
    reset_on_win: bool = True
    auto_recovery: bool = False
    # Winning-streak (Paroli) sizing — opt-in, lives alongside the
    # loss-recovery knobs because both ladders share runtime state
    # on MartingaleState.
    winning_streak_enabled: bool = False
    winning_streak_max_level: int = Field(default=2, ge=0, le=20)
```

Then update `_to_response` (around line 157) to populate the new fields:

```python
        martingale=MartingalePayload(
            enabled=row.martingale_enabled,
            multiplier=row.martingale_multiplier,
            max_streak=row.martingale_max_streak,
            reset_on_win=row.martingale_reset_on_win,
            auto_recovery=row.martingale_auto_recovery,
            winning_streak_enabled=row.winning_streak_enabled,
            winning_streak_max_level=row.winning_streak_max_level,
        ),
```

And update `_payload_to_dict` (around line 200) to flatten the new fields:

```python
        "martingale_auto_recovery": p.martingale.auto_recovery,
        "winning_streak_enabled": p.martingale.winning_streak_enabled,
        "winning_streak_max_level": p.martingale.winning_streak_max_level,
        "enabled": p.enabled,
```

- [ ] **Step 5.4: Verify the round-trip test passes**

```bash
cd autotrader/backend
uv run pytest tests/test_parsers.py::test_winning_streak_fields_round_trip_in_martingale_payload -v
```

Expected: PASS.

- [ ] **Step 5.5: Write the failing StreakRow + reset endpoint tests**

Append to `autotrader/backend/tests/test_risk.py`:

```python
async def test_streak_row_includes_win_streak_fields(
    async_client: httpx.AsyncClient,
) -> None:
    """GET /risk/overview must include current_win_streak + last_payout
    per parser so the dashboard can render both ladders."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
            "winning_streak_enabled": True,
            "winning_streak_max_level": 2,
        },
        default_stake=5.0,
    )
    await _activate()

    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    r = await async_client.get("/risk/overview", headers=headers)
    body = r.json()
    rows = body["streaks"]
    assert len(rows) == 1
    row = rows[0]
    assert row["current_win_streak"] == 1
    assert row["last_payout"] == pytest.approx(9.25)


async def test_reset_streak_clears_both_ladders(
    async_client: httpx.AsyncClient,
) -> None:
    """POST /risk/streaks/{id}/reset must zero current_streak,
    current_win_streak, last_payout, and last_stake atomically."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    cfg_id = await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
            "winning_streak_enabled": True,
            "winning_streak_max_level": 3,
        },
        default_stake=5.0,
    )
    await _activate()

    # Prime both counters: a win then a loss (loss won't fully clear
    # win_streak in record_outcome — actually it WILL, but we want
    # both ladders to have *had* state so the reset is meaningful).
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    # Re-prime current_streak by losing.
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    # Hit reset.
    r = await async_client.post(
        f"/risk/streaks/{cfg_id}/reset", headers=headers,
    )
    assert r.status_code == 200, r.text

    r = await async_client.get("/risk/overview", headers=headers)
    row = next(
        s for s in r.json()["streaks"] if s["parser_config_id"] == cfg_id
    )
    assert row["current_streak"] == 0
    assert row["current_win_streak"] == 0
    assert row["last_payout"] == 0.0
    assert row["last_stake"] == 0.0
```

- [ ] **Step 5.6: Confirm they fail**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_streak_row_includes_win_streak_fields tests/test_risk.py::test_reset_streak_clears_both_ladders -v
```

Expected: 2 FAIL. The first because StreakRow doesn't expose the new fields; the second because reset_state already clears both per Task 3 — but the test should pass once StreakRow exposes the fields. (Confirm: Task 3 already updated reset_state. The FAIL on the second test is purely the assertion on `current_win_streak` field absence.)

- [ ] **Step 5.7: Extend StreakRow in `routers/risk.py`**

In `autotrader/backend/src/autotrader/routers/risk.py`, find the `StreakRow` class (around line 50; locate via `grep -n 'class StreakRow' src/autotrader/routers/risk.py`) and add fields:

```python
class StreakRow(BaseModel):
    parser_config_id: int
    parser_name: str
    chat_id: int
    martingale_enabled: bool
    multiplier: float
    max_streak: int
    current_streak: int
    last_outcome: str
    last_stake: float
    updated_at: datetime | None
    # Winning-streak ladder.
    winning_streak_enabled: bool = False
    winning_streak_max_level: int = 2
    current_win_streak: int = 0
    last_payout: float = 0.0
```

Then find the function that builds StreakRow rows from DB rows (search for `StreakRow(` in the same file) and populate the new fields from `cfg.winning_streak_enabled`, `cfg.winning_streak_max_level`, `state.current_win_streak`, `state.last_payout`.

- [ ] **Step 5.8: Verify both new tests pass**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_streak_row_includes_win_streak_fields tests/test_risk.py::test_reset_streak_clears_both_ladders -v
```

Expected: 2 PASS.

- [ ] **Step 5.9: Run the full backend suite to catch regressions in serialization**

```bash
cd autotrader/backend
uv run pytest -x 2>&1 | tail -3
```

Expected: every test pass.

- [ ] **Step 5.10: Commit**

```bash
git add autotrader/backend/src/autotrader/routers/parsers.py \
        autotrader/backend/src/autotrader/routers/risk.py \
        autotrader/backend/tests/test_parsers.py \
        autotrader/backend/tests/test_risk.py
git commit -m "feat(autotrader/api): MartingalePayload + StreakRow gain win-streak fields

Both ladders surface in the API:
  - POST/GET /parsers/configs roundtrips
    martingale.winning_streak_enabled + winning_streak_max_level.
  - GET /risk/overview StreakRow includes current_win_streak +
    last_payout per parser.
  - POST /risk/streaks/{id}/reset clears BOTH ladders atomically
    (already true at the model layer; this commit pins the
    invariant via test).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6 — Pipeline.warm_up + Pipeline.prebuild + lifespan invocation

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/pipeline.py` (new methods)
- Modify: `autotrader/backend/src/autotrader/main.py` (lifespan invocation)
- Test: `autotrader/backend/tests/test_startup_recovery.py`

- [ ] **Step 6.1: Write the failing test**

Append to `autotrader/backend/tests/test_startup_recovery.py`:

```python
def test_lifespan_warm_up_caches_all_enabled_parsers(
    fake_quotex: None,
) -> None:
    """After lifespan startup, cached_parser_count must equal
    enabled_parser_count (minus any build failures). Today's lazy
    cache leaves it at 0 until first message."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.parser_config import create_config  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    # Seed 3 enabled parsers across 2 chats + 1 disabled parser.
    async def _seed() -> None:
        async with AsyncSessionLocal() as s:
            s.add(
                WatchedChannel(
                    chat_id=-1001, title="A", chat_type="channel",
                    username="a", enabled=True,
                ),
            )
            s.add(
                WatchedChannel(
                    chat_id=-1002, title="B", chat_type="channel",
                    username="b", enabled=True,
                ),
            )
            await s.commit()

            for chat_id, name, enabled in [
                (-1001, "A1", True),
                (-1001, "A2", True),
                (-1002, "B1", True),
                (-1002, "Bdisabled", False),
            ]:
                await create_config(
                    s,
                    chat_id=chat_id,
                    payload={
                        "name": name,
                        "priority": 100,
                        "parser_type": "template",
                        "parser_config": {
                            "template": "{DIRECTION} {ASSET} {DURATION}",
                        },
                        "default_stake": 1.0,
                        "default_duration_seconds": 60,
                        "trade_mode": "live",
                        "enabled": enabled,
                        "martingale_enabled": False,
                        "martingale_multiplier": 2.0,
                        "martingale_max_streak": 5,
                        "martingale_reset_on_win": True,
                        "martingale_auto_recovery": False,
                        "winning_streak_enabled": False,
                        "winning_streak_max_level": 2,
                        "asset_aliases": {},
                        "aggregate_window_seconds": 0,
                        "timezone": "UTC",
                        "timezone_offset_minutes": 0,
                    },
                )

    asyncio.new_event_loop().run_until_complete(_seed())

    # Re-enter the lifespan; warm_up runs and populates the cache.
    with TestClient(app) as client:
        r = client.post("/auth/login", json={"passcode": "test-passcode"})
        token = r.json()["token"]
        r = client.get(
            "/pipeline/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = r.json()
        assert body["enabled_parser_count"] == 3
        assert body["cached_parser_count"] == 3, (
            f"warm_up must materialise all 3 enabled parsers; got {body}"
        )


def test_lifespan_warm_up_tolerates_invalid_parser(
    fake_quotex: None,
) -> None:
    """A parser with a bad regex must not crash startup — warm_up
    records a build_failed decision and the remaining parsers cache
    fine."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.parser_config import create_config  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    async def _seed() -> None:
        async with AsyncSessionLocal() as s:
            s.add(
                WatchedChannel(
                    chat_id=-1001, title="A", chat_type="channel",
                    username="a", enabled=True,
                ),
            )
            await s.commit()

            await create_config(
                s,
                chat_id=-1001,
                payload={
                    "name": "good",
                    "priority": 100,
                    "parser_type": "template",
                    "parser_config": {"template": "{DIRECTION} {ASSET}"},
                    "default_stake": 1.0,
                    "default_duration_seconds": 60,
                    "trade_mode": "live",
                    "enabled": True,
                    "martingale_enabled": False,
                    "martingale_multiplier": 2.0,
                    "martingale_max_streak": 5,
                    "martingale_reset_on_win": True,
                    "martingale_auto_recovery": False,
                    "winning_streak_enabled": False,
                    "winning_streak_max_level": 2,
                    "asset_aliases": {},
                    "aggregate_window_seconds": 0,
                    "timezone": "UTC",
                    "timezone_offset_minutes": 0,
                },
            )
            await create_config(
                s,
                chat_id=-1001,
                payload={
                    "name": "broken",
                    "priority": 110,
                    "parser_type": "regex",
                    "parser_config": {"pattern": "(["},  # invalid regex
                    "default_stake": 1.0,
                    "default_duration_seconds": 60,
                    "trade_mode": "live",
                    "enabled": True,
                    "martingale_enabled": False,
                    "martingale_multiplier": 2.0,
                    "martingale_max_streak": 5,
                    "martingale_reset_on_win": True,
                    "martingale_auto_recovery": False,
                    "winning_streak_enabled": False,
                    "winning_streak_max_level": 2,
                    "asset_aliases": {},
                    "aggregate_window_seconds": 0,
                    "timezone": "UTC",
                    "timezone_offset_minutes": 0,
                },
            )

    asyncio.new_event_loop().run_until_complete(_seed())

    with TestClient(app) as client:
        # Lifespan completed despite the broken parser.
        r = client.post("/auth/login", json={"passcode": "test-passcode"})
        token = r.json()["token"]
        r = client.get(
            "/pipeline/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = r.json()
        assert body["enabled_parser_count"] == 2
        assert body["cached_parser_count"] == 1, (
            f"warm_up must skip the broken parser; got {body}"
        )

        # The build_failed decision is in the ring buffer.
        r = client.get(
            "/pipeline/decisions?limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )
        decisions = r.json()
        build_failures = [d for d in decisions if d["outcome"] == "build_failed"]
        assert len(build_failures) >= 1
        assert build_failures[0]["parser_name"] == "broken"
```

NOTE: this test file already has an autouse `_wipe_after_each_test` fixture (added in Task 5 of the previous plan); these new tests will get clean state automatically.

- [ ] **Step 6.2: Confirm they fail**

```bash
cd autotrader/backend
uv run pytest tests/test_startup_recovery.py::test_lifespan_warm_up_caches_all_enabled_parsers -v
```

Expected: FAIL — `cached_parser_count == 0` (lazy cache).

- [ ] **Step 6.3: Add `warm_up` and `prebuild` to `Pipeline`**

In `autotrader/backend/src/autotrader/services/pipeline.py`, find the cache-management region (after `invalidate_for_chat`, around line 165) and add:

```python
    async def warm_up(self) -> dict[str, int]:
        """Materialise every enabled parser_config into the cache.

        Called by the lifespan after reconcile_pending and before the
        Telegram message handler is attached, so by the time messages
        flow in every parser is ready. Failures (bad regex / missing
        required field) record a ``build_failed`` decision and
        continue — the lifespan is not aborted.

        Returns ``{built: N, failed: M}`` for log + telemetry.
        Idempotent: re-running re-validates configs.
        """
        async with AsyncSessionLocal() as session:
            configs = await _list_configs(session)
        built = 0
        failed = 0
        for cfg in configs:
            if not cfg.enabled:
                continue
            if self.prebuild(cfg):
                built += 1
            else:
                failed += 1
        log.info("pipeline.warm_up", built=built, failed=failed)
        return {"built": built, "failed": failed}

    def prebuild(self, cfg: ParserConfig) -> bool:
        """Build a single parser into the cache. Returns True on
        success, False on ParserBuildError. Failures emit a
        ``build_failed`` decision so the dashboard surfaces them
        immediately, not on first message arrival.
        """
        try:
            self._get_or_build(cfg)
        except ParserBuildError as exc:
            log.error(
                "pipeline.prebuild_failed",
                config_id=cfg.id,
                name=cfg.name,
                error=str(exc),
            )
            self._record_decision(
                {
                    "chat_id": cfg.chat_id,
                    "parser_config_id": cfg.id,
                    "parser_name": cfg.name,
                    "parser_type": cfg.parser_type,
                    "outcome": "build_failed",
                    "reasons": [str(exc)],
                    "signals": 0,
                    "text_preview": "(warm-up)",
                },
            )
            return False
        return True
```

- [ ] **Step 6.4: Wire `warm_up` into the lifespan**

In `autotrader/backend/src/autotrader/main.py`, find the call to `executor.reconcile_pending()` (around line 273) and add the warm-up call right after:

```python
    try:
        await executor.reconcile_pending()
    except Exception as exc:  # pragma: no cover - best-effort startup task
        log.warning("executor.reconcile.failed", error=str(exc))

    # Eagerly materialise every enabled parser. Without this, the
    # ``cached_parser_count`` gauge sits at 0 until each chat
    # receives its first message — operators can't tell whether
    # their configs compile cleanly until traffic arrives.
    try:
        await pipeline.warm_up()
    except Exception as exc:  # pragma: no cover - belt + braces
        log.warning("pipeline.warm_up.failed", error=str(exc))
```

- [ ] **Step 6.5: Verify the new tests pass**

```bash
cd autotrader/backend
uv run pytest tests/test_startup_recovery.py::test_lifespan_warm_up_caches_all_enabled_parsers tests/test_startup_recovery.py::test_lifespan_warm_up_tolerates_invalid_parser -v
```

Expected: 2 PASS.

- [ ] **Step 6.6: Run the full startup-recovery module**

```bash
cd autotrader/backend
uv run pytest tests/test_startup_recovery.py -v
```

Expected: every test pass (4 pre-existing + 2 new = 6 total… or whatever the current count is, but no regressions).

- [ ] **Step 6.7: Commit**

```bash
git add autotrader/backend/src/autotrader/services/pipeline.py \
        autotrader/backend/src/autotrader/main.py \
        autotrader/backend/tests/test_startup_recovery.py
git commit -m "feat(autotrader/pipeline): eager parser warm-up at lifespan startup

Pipeline gains warm_up() and prebuild() methods. The lifespan calls
warm_up() after reconcile_pending and before the Telegram handler
is attached, so by the time messages flow in every enabled parser
is materialised in the cache.

A bad regex / missing template field now surfaces immediately as a
build_failed decision in the ring buffer instead of silently
failing on first message. Lifespan tolerates failures: bad parsers
are skipped, healthy ones continue caching.

Invariant after warm-up: cached_parser_count == enabled_parser_count
- build_failures.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7 — Wire prebuild into parser save + watch endpoints

**Files:**
- Modify: `autotrader/backend/src/autotrader/routers/parsers.py` (POST/PUT)
- Modify: `autotrader/backend/src/autotrader/routers/telegram.py` (POST /watch)
- Test: `autotrader/backend/tests/test_startup_recovery.py`

- [ ] **Step 7.1: Write the failing test**

Append to `autotrader/backend/tests/test_startup_recovery.py`:

```python
def test_save_parser_immediately_caches_it(
    fake_quotex: None,
) -> None:
    """POST /parsers/configs with enabled=True must materialise the
    parser into the cache before the response returns — no waiting
    for first message arrival."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    async def _seed() -> None:
        async with AsyncSessionLocal() as s:
            s.add(
                WatchedChannel(
                    chat_id=-1001, title="A", chat_type="channel",
                    username="a", enabled=True,
                ),
            )
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_seed())

    with TestClient(app) as client:
        r = client.post("/auth/login", json={"passcode": "test-passcode"})
        token = r.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # No parsers yet → cache empty.
        r = client.get("/pipeline/status", headers=headers)
        assert r.json()["cached_parser_count"] == 0

        # Save a parser → cache count ticks up.
        r = client.post(
            "/parsers/configs",
            headers=headers,
            json={
                "chat_id": -1001,
                "name": "live",
                "priority": 100,
                "parser_type": "template",
                "parser_config": {"template": "{DIRECTION} {ASSET}"},
                "timezone": "UTC",
                "timezone_offset_minutes": 0,
                "asset_aliases": {},
                "default_stake": 1.0,
                "default_duration_seconds": 60,
                "trade_mode": "live",
                "aggregate_window_seconds": 0,
                "martingale": {
                    "enabled": False,
                    "multiplier": 2.0,
                    "max_streak": 5,
                    "reset_on_win": True,
                    "auto_recovery": False,
                    "winning_streak_enabled": False,
                    "winning_streak_max_level": 2,
                },
                "enabled": True,
            },
        )
        assert r.status_code == 201, r.text

        r = client.get("/pipeline/status", headers=headers)
        assert r.json()["cached_parser_count"] == 1, (
            "save must prebuild the parser; got "
            f"{r.json()['cached_parser_count']}"
        )

        # Toggle enabled=False → cache count drops.
        cfg_id = r.json().get("id")  # may be None on /pipeline/status
        # (re-fetch via /parsers/configs to get the id)
        r = client.get("/parsers/configs", headers=headers)
        cfg_id = r.json()[0]["id"]
        r = client.put(
            f"/parsers/configs/{cfg_id}",
            headers=headers,
            json={
                "name": "live",
                "priority": 100,
                "parser_type": "template",
                "parser_config": {"template": "{DIRECTION} {ASSET}"},
                "timezone": "UTC",
                "timezone_offset_minutes": 0,
                "asset_aliases": {},
                "default_stake": 1.0,
                "default_duration_seconds": 60,
                "trade_mode": "live",
                "aggregate_window_seconds": 0,
                "martingale": {
                    "enabled": False,
                    "multiplier": 2.0,
                    "max_streak": 5,
                    "reset_on_win": True,
                    "auto_recovery": False,
                    "winning_streak_enabled": False,
                    "winning_streak_max_level": 2,
                },
                "enabled": False,
            },
        )
        assert r.status_code == 200, r.text

        r = client.get("/pipeline/status", headers=headers)
        assert r.json()["cached_parser_count"] == 0, (
            "disabling a parser must drop it from the cache"
        )
```

- [ ] **Step 7.2: Confirm it fails**

```bash
cd autotrader/backend
uv run pytest tests/test_startup_recovery.py::test_save_parser_immediately_caches_it -v
```

Expected: FAIL — POST returns 201 but cache stays at 0 because dispatch never fired.

- [ ] **Step 7.3: Wire prebuild into POST /parsers/configs**

In `autotrader/backend/src/autotrader/routers/parsers.py`, find `create_config_endpoint` (around line 297) and update it to receive `pipeline: PipelineDep` and call prebuild:

```python
@router.post(
    "/configs",
    response_model=ConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_endpoint(
    body: CreateConfigRequest,
    session: SessionDep,
    pipeline: PipelineDep,
) -> ConfigResponse:
    _validate_compiles(body)
    payload = _payload_to_dict(body)
    payload.pop("chat_id", None)
    row = await create_config(
        session,
        chat_id=body.chat_id,
        payload=payload,
    )
    # Eagerly materialise the parser so the cache reflects the save
    # immediately — no waiting for first message arrival.
    if row.enabled:
        pipeline.prebuild(row)
    return _to_response(row)
```

Then update `update_config_endpoint` to also prebuild when the row stays enabled (or invalidate when it goes disabled — the existing `pipeline.invalidate(config_id)` already handles invalidation; we just need to prebuild after when enabled):

```python
@router.put("/configs/{config_id}", response_model=ConfigResponse)
async def update_config_endpoint(
    config_id: int,
    body: ConfigPayload,
    session: SessionDep,
    pipeline: PipelineDep,
) -> ConfigResponse:
    _validate_compiles(body)
    row = await update_config(
        session,
        config_id=config_id,
        payload=_payload_to_dict(body),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="parser config not found",
        )
    pipeline.invalidate(config_id)
    if row.enabled:
        pipeline.prebuild(row)
    return _to_response(row)
```

- [ ] **Step 7.4: Wire prebuild into POST /telegram/watch**

In `autotrader/backend/src/autotrader/routers/telegram.py`, update `watch_endpoint` to also prebuild the chat's parsers after a successful subscribe:

```python
@router.post("/watch", response_model=OkResponse)
async def watch_endpoint(
    body: WatchRequest,
    session: SessionDep,
    manager: TelegramDep,
    pipeline: PipelineDep,
) -> OkResponse:
    await upsert_watch(
        session,
        chat_id=body.chat_id,
        title=body.title,
        chat_type=body.chat_type,
        username=body.username,
        enabled=body.enabled,
    )
    if body.enabled:
        try:
            await manager.subscribe_chat(body.chat_id)
        except TelegramManagerError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"chat saved but subscribe failed: {exc}",
            ) from exc
        # Prebuild any parsers already configured for this chat so
        # the first signal that arrives via the freshly-subscribed
        # update stream lands in the cache without a build round-trip.
        from autotrader.models.parser_config import (  # noqa: PLC0415
            list_configs as _list_chat_configs,
        )
        chat_configs = await _list_chat_configs(session, chat_id=body.chat_id)
        for cfg in chat_configs:
            if cfg.enabled:
                pipeline.prebuild(cfg)
    return OkResponse()
```

- [ ] **Step 7.5: Verify the new test passes**

```bash
cd autotrader/backend
uv run pytest tests/test_startup_recovery.py::test_save_parser_immediately_caches_it -v
```

Expected: PASS.

- [ ] **Step 7.6: Run full backend suite (signature changes ripple)**

```bash
cd autotrader/backend
uv run pytest -x 2>&1 | tail -3
```

Expected: every test pass. The new `pipeline: PipelineDep` parameter on create/update/watch endpoints is added — existing tests should pass through dependency injection cleanly.

- [ ] **Step 7.7: Commit**

```bash
git add autotrader/backend/src/autotrader/routers/parsers.py \
        autotrader/backend/src/autotrader/routers/telegram.py \
        autotrader/backend/tests/test_startup_recovery.py
git commit -m "feat(autotrader/api): prebuild parsers on save + watch

POST/PUT /parsers/configs with enabled=true now calls
pipeline.prebuild() so the cache reflects the save immediately —
no \"wait for first message\" gap. Bad configs surface as
build_failed decisions in the ring buffer at save time, before
the next signal even arrives.

POST /telegram/watch with enabled=true also walks the chat's
configured parsers and prebuilds each so a freshly-subscribed
chat starts dispatching to materialised parsers from the first
update.

Closes the gap operators reported: \"7 parsers configured, only
1 shows in the pipeline cache\".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8 — Frontend: types + WinningStreakBlock UI + table columns

**Files:**
- Modify: `autotrader/frontend/lib/api.ts` (interface extensions)
- Modify: `autotrader/frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx` (WinningStreakBlock)
- Modify: `autotrader/frontend/app/dashboard/pipeline/page.tsx` (or panel-martingale-streaks.tsx — locate first)

- [ ] **Step 8.1: Extend the TypeScript types**

In `autotrader/frontend/lib/api.ts`, find the `MartingalePayload` interface (around line 270) and add fields:

```ts
export interface MartingalePayload {
  enabled: boolean;
  multiplier: number;
  max_streak: number;
  reset_on_win: boolean;
  auto_recovery: boolean;
  winning_streak_enabled: boolean;       // NEW
  winning_streak_max_level: number;      // NEW
}
```

Find the `StreakRow` interface (around line 494) and add fields:

```ts
export interface StreakRow {
  parser_config_id: number;
  parser_name: string;
  chat_id: number;
  martingale_enabled: boolean;
  multiplier: number;
  max_streak: number;
  current_streak: number;
  last_outcome: string;
  last_stake: number;
  updated_at: string | null;
  // Winning streak (Paroli) ladder.
  winning_streak_enabled: boolean;       // NEW
  winning_streak_max_level: number;      // NEW
  current_win_streak: number;            // NEW
  last_payout: number;                   // NEW
}
```

Update `DEFAULT_PARSER_CONFIG` (around line 327) to include defaults for the new martingale fields:

```ts
export const DEFAULT_PARSER_CONFIG: ParserConfigPayload = {
  // existing fields…
  martingale: {
    enabled: false,
    multiplier: 2,
    max_streak: 5,
    reset_on_win: true,
    auto_recovery: false,
    winning_streak_enabled: false,       // NEW
    winning_streak_max_level: 2,         // NEW
  },
  // …
};
```

- [ ] **Step 8.2: Type-check the frontend to confirm shape**

```bash
cd autotrader/frontend
bun run type-check 2>&1 | tail -10
```

Expected: errors pointing at the parser editor and pipeline page (which destructure the old shapes). Those will be fixed in 8.3 and 8.4.

- [ ] **Step 8.3: Add `WinningStreakBlock` to the parser editor**

In `autotrader/frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx`, find the `MartingaleBlock` component (around line 560) and add `WinningStreakBlock` immediately after it. Replace the call site (look for `<MartingaleBlock cfg={cfg} setMartingale={setMartingale} />`, around line 529) with both blocks stacked:

```tsx
        <MartingaleBlock cfg={cfg} setMartingale={setMartingale} />
        <WinningStreakBlock cfg={cfg} setMartingale={setMartingale} />
```

Then add the new component definition near the existing `MartingaleBlock`:

```tsx
function WinningStreakBlock({
  cfg,
  setMartingale,
}: {
  cfg: ParserConfigPayload;
  setMartingale: <K extends keyof ParserConfigPayload["martingale"]>(
    key: K,
    value: ParserConfigPayload["martingale"][K],
  ) => void;
}) {
  const m = cfg.martingale;
  return (
    <div className="space-y-3 rounded-md border border-emerald-500/20 bg-emerald-500/5 p-3">
      <div className="flex items-center justify-between gap-2">
        <Label className="flex items-center gap-2">
          Winning streak (Paroli)
          {m.winning_streak_enabled && <Badge variant="success">on</Badge>}
        </Label>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={m.winning_streak_enabled}
            onChange={(e) =>
              setMartingale("winning_streak_enabled", e.target.checked)
            }
            className="h-4 w-4"
          />
          Enable
        </label>
      </div>

      <p className="text-xs text-muted-foreground">
        On a win, the next channel signal stakes at{" "}
        <code>ceil(prev_stake + prev_profit)</code> up to max level,
        then resets to base. A loss at any point also resets to base.
        Stakes round up to the nearest integer (Quotex constraint).
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Max win streak level"
          value={m.winning_streak_max_level}
          onChange={(v) =>
            setMartingale(
              "winning_streak_max_level",
              Math.max(0, Math.min(20, Number(v) || 0)),
            )
          }
          type="number"
          help="0 = uncapped"
          disabled={!m.winning_streak_enabled}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 8.4: Add the columns to the pipeline streaks table**

```bash
grep -rln "current_streak\|StreakRow" autotrader/frontend/app/dashboard 2>/dev/null
```

Find the file rendering the streaks table (likely `app/dashboard/pipeline/page.tsx` per the snapshot earlier showing `Parser | Mult | Recovery | Step | …`). In that file, locate the table header row and add two columns:

```tsx
<TableHead>Win step</TableHead>
<TableHead>Last payout</TableHead>
```

In the body row template, after the existing `Last stake` cell, add:

```tsx
<TableCell className="font-mono">
  {row.current_win_streak === 0
    ? "0"
    : `${row.current_win_streak} / ${row.winning_streak_max_level}`}
</TableCell>
<TableCell className="font-mono">
  {row.last_payout > 0 ? `$${row.last_payout.toFixed(2)}` : "—"}
</TableCell>
```

The "0" for the win-step cell when no streak is in progress matches the existing "base" rendering style for current_streak.

- [ ] **Step 8.5: Type-check + build**

```bash
cd autotrader/frontend
bun run type-check
bun run build 2>&1 | tail -5
```

Expected: both clean.

- [ ] **Step 8.6: Commit**

```bash
git add autotrader/frontend/lib/api.ts \
        autotrader/frontend/app/dashboard/parsers/\[chat_id\]/\[config_id\]/page.tsx \
        autotrader/frontend/app/dashboard/pipeline/page.tsx
git commit -m "feat(autotrader/frontend): WinningStreakBlock + Paroli streak columns

Parser editor gains a Winning Streak (Paroli) card next to the
Martingale Recovery card. Same shape as martingale (toggle +
numeric input) so operators recognise the pattern. Help copy
explains the ceil(prev_stake + prev_profit) formula and the
loss-resets-to-base rule.

Pipeline page streaks table gains 'Win step' (current/max) and
'Last payout' columns so operators see both ladder positions per
parser at a glance.

Type definitions extended in lib/api.ts: MartingalePayload and
StreakRow gain four winning-streak fields each. DEFAULT_PARSER_CONFIG
includes safe defaults so existing forms render correctly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9 — Admin bot: ladder snapshot in trade.upserted + formatter updates

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/executor.py` (`_attempt_to_payload`)
- Modify: `autotrader/backend/src/autotrader/services/admin_bot_notify.py` (`format_trade_placed`, `format_trade_settled`)
- Test: `autotrader/backend/tests/test_admin_bot.py`

- [ ] **Step 9.1: Examine the existing `_attempt_to_payload` shape**

Look at `autotrader/backend/src/autotrader/services/executor.py:61-87`. The function currently takes only `attempt: TradeAttempt` and returns the dict that the WebSocket/admin-bot consume. We need to extend it to *optionally* include a ladder snapshot when the caller has fresh state in hand.

- [ ] **Step 9.2: Write the failing tests**

Append to `autotrader/backend/tests/test_admin_bot.py`:

```python
def test_format_trade_settled_includes_win_streak_when_active() -> None:
    """A settled win on a streak-enabled parser must surface the
    streak progress + next-stake hint in the bot DM."""
    from autotrader.services.admin_bot_notify import format_trade_settled  # noqa: PLC0415

    payload = {
        "id": 1,
        "asset": "EURUSD",
        "direction": "call",
        "duration_seconds": 60,
        "stake": 5.0,
        "trade_mode": "live",
        "status": "won",
        "profit": 4.25,
        "settled_at": "2026-05-10T05:00:00Z",
        "ladder": {
            "current_streak": 0,
            "max_streak": 5,
            "current_win_streak": 1,
            "max_win_streak": 2,
            "next_stake_hint": 10,
        },
    }
    msg = format_trade_settled(payload)
    assert "win streak" in msg.lower()
    assert "1/2" in msg or "1 / 2" in msg
    assert "10" in msg  # next_stake_hint surfaces somewhere


def test_format_trade_settled_notes_martingale_recovery_on_loss() -> None:
    """A settled loss with martingale auto_recovery active must
    surface the next-recovery-stake hint."""
    from autotrader.services.admin_bot_notify import format_trade_settled  # noqa: PLC0415

    payload = {
        "id": 1,
        "asset": "EURUSD",
        "direction": "call",
        "duration_seconds": 60,
        "stake": 5.0,
        "trade_mode": "live",
        "status": "lost",
        "profit": -5.0,
        "settled_at": "2026-05-10T05:00:00Z",
        "ladder": {
            "current_streak": 1,
            "max_streak": 2,
            "current_win_streak": 0,
            "max_win_streak": 0,
            "next_stake_hint": 10,
        },
    }
    msg = format_trade_settled(payload)
    assert "recovery" in msg.lower() or "martingale" in msg.lower()
    assert "10" in msg  # next stake


def test_format_trade_settled_no_ladder_lines_when_at_base() -> None:
    """When neither ladder is in progress (both counters at 0), the
    settled message is the unchanged one-liner — no extra noise."""
    from autotrader.services.admin_bot_notify import format_trade_settled  # noqa: PLC0415

    payload = {
        "id": 1,
        "asset": "EURUSD",
        "direction": "call",
        "duration_seconds": 60,
        "stake": 5.0,
        "trade_mode": "live",
        "status": "won",
        "profit": 4.25,
        "settled_at": "2026-05-10T05:00:00Z",
        "ladder": {
            "current_streak": 0,
            "max_streak": 5,
            "current_win_streak": 0,
            "max_win_streak": 2,
            "next_stake_hint": 5,
        },
    }
    msg = format_trade_settled(payload)
    assert "win streak" not in msg.lower()
    assert "recovery" not in msg.lower()
```

- [ ] **Step 9.3: Confirm they fail**

```bash
cd autotrader/backend
uv run pytest tests/test_admin_bot.py::test_format_trade_settled_includes_win_streak_when_active -v
```

Expected: FAIL — no streak text in the formatter.

- [ ] **Step 9.4: Extend `_attempt_to_payload` to embed the ladder**

In `autotrader/backend/src/autotrader/services/executor.py`, replace `_attempt_to_payload` (lines 61-87) with:

```python
def _attempt_to_payload(
    attempt: TradeAttempt,
    *,
    state: object | None = None,
    cfg: object | None = None,
) -> dict[str, object]:
    """Mirror of TradeAttemptResponse for the event bus.

    Optional ``state`` (MartingaleState) + ``cfg`` (ParserConfig) embed
    a ``ladder`` snapshot so admin-bot notifications + frontend rows
    can render streak progress without a re-fetch. Both default to
    ``None`` for callers that don't have the state in hand (e.g.
    on insert, before the watcher has settled).
    """
    payload: dict[str, object] = {
        "id": attempt.id or 0,
        "chat_id": attempt.chat_id,
        "parser_config_id": attempt.parser_config_id,
        "asset": attempt.asset,
        "asset_raw": attempt.asset_raw,
        "direction": attempt.direction,
        "duration_seconds": attempt.duration_seconds,
        "stake": attempt.stake,
        "trade_mode": attempt.trade_mode,
        "fire_at": attempt.fire_at.isoformat() if attempt.fire_at else None,
        "status": attempt.status,
        "broker_order_id": attempt.broker_order_id,
        "profit": attempt.profit,
        "error": attempt.error,
        "received_at": attempt.received_at.isoformat(),
        "placed_at": attempt.placed_at.isoformat() if attempt.placed_at else None,
        "settled_at": attempt.settled_at.isoformat() if attempt.settled_at else None,
    }
    if state is not None and cfg is not None:
        # Compute the next-stake hint the same way risk_gate would on
        # the next signal: streak first, martingale second, base last.
        cur_win = getattr(state, "current_win_streak", 0)
        last_payout = getattr(state, "last_payout", 0.0)
        cur_loss = getattr(state, "current_streak", 0)
        if (
            getattr(cfg, "winning_streak_enabled", False)
            and cur_win > 0
            and last_payout > 0
        ):
            next_hint = math.ceil(last_payout)
        elif getattr(cfg, "martingale_enabled", False) and cur_loss > 0:
            next_hint = math.ceil(
                cfg.default_stake * (cfg.martingale_multiplier ** cur_loss),
            )
        else:
            next_hint = math.ceil(getattr(cfg, "default_stake", 0))
        payload["ladder"] = {
            "current_streak": cur_loss,
            "max_streak": getattr(cfg, "martingale_max_streak", 0),
            "current_win_streak": cur_win,
            "max_win_streak": getattr(cfg, "winning_streak_max_level", 0),
            "next_stake_hint": int(next_hint),
        }
    return payload
```

Add `import math` at the top of `executor.py` if not present.

In the `_watch_result` method (around line 488), update the `_publish` call to pass the freshly-loaded state + cfg:

```python
        if updated is not None:
            # Pass state+cfg so the admin-bot notification has ladder
            # context. Both came from the same session above.
            self._publish(updated, state=new_state, cfg=cfg)
```

Update the `_publish` method (around line 235-245) to forward the kwargs:

```python
    def _publish(
        self,
        attempt: TradeAttempt,
        *,
        state: object | None = None,
        cfg: object | None = None,
    ) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            "trade.upserted", _attempt_to_payload(attempt, state=state, cfg=cfg),
        )
```

- [ ] **Step 9.5: Extend `format_trade_settled` to render the ladder lines**

In `autotrader/backend/src/autotrader/services/admin_bot_notify.py`, replace `format_trade_settled` (around lines 237-253) with:

```python
def format_trade_settled(payload: dict[str, Any]) -> str:
    """Format a settled-trade notification, optionally appending
    a ladder progress line when one of the parsers' ladders is
    active.
    """
    status = payload.get("status", "")
    asset = payload.get("asset", "")
    direction = payload.get("direction", "")
    stake = payload.get("stake", 0)
    profit = payload.get("profit", 0)

    if status == "won":
        head = f"✅ WON +${profit:.2f} {asset} {direction.upper()} ${stake}"
    elif status == "lost":
        head = f"❌ LOST ${profit:.2f} {asset} {direction.upper()} ${stake}"
    else:
        head = f"⚠️ {status.upper()} {asset} {direction.upper()} ${stake}"

    ladder_line = _ladder_line(payload)
    if ladder_line:
        return f"{head}\n   {ladder_line}"
    return head


def _ladder_line(payload: dict[str, Any]) -> str:
    """Return a one-line ladder hint, or empty string when neither
    ladder is in progress."""
    ladder = payload.get("ladder")
    if not isinstance(ladder, dict):
        return ""
    cur_win = ladder.get("current_win_streak", 0) or 0
    max_win = ladder.get("max_win_streak", 0) or 0
    cur_loss = ladder.get("current_streak", 0) or 0
    max_loss = ladder.get("max_streak", 0) or 0
    next_hint = ladder.get("next_stake_hint", 0) or 0
    if cur_win > 0:
        if max_win > 0 and cur_win >= max_win:
            return f"📈 win streak {cur_win}/{max_win} (max hit, reset) → next ${next_hint}"
        return f"📈 win streak {cur_win}/{max_win} → next ${next_hint}"
    if cur_loss > 0:
        return f"📉 next: martingale recovery ${next_hint} (step {cur_loss}/{max_loss})"
    return ""
```

The `format_trade_placed` formatter (around lines 218-235) gets the same `_ladder_line` treatment for consistency:

```python
def format_trade_placed(payload: dict[str, Any]) -> str:
    asset = payload.get("asset", "")
    direction = payload.get("direction", "")
    duration = payload.get("duration_seconds", 0)
    stake = payload.get("stake", 0)
    head = (
        f"🎯 {direction.upper()} {asset} {duration}s · ${stake}"
    )
    ladder_line = _ladder_line(payload)
    if ladder_line:
        return f"{head}\n   {ladder_line}"
    return head
```

- [ ] **Step 9.6: Verify the new formatter tests pass**

```bash
cd autotrader/backend
uv run pytest tests/test_admin_bot.py::test_format_trade_settled_includes_win_streak_when_active tests/test_admin_bot.py::test_format_trade_settled_notes_martingale_recovery_on_loss tests/test_admin_bot.py::test_format_trade_settled_no_ladder_lines_when_at_base -v
```

Expected: 3 PASS.

- [ ] **Step 9.7: Run the full backend suite (executor signature changes ripple)**

```bash
cd autotrader/backend
uv run pytest -x 2>&1 | tail -3
```

Expected: every test pass.

- [ ] **Step 9.8: Commit**

```bash
git add autotrader/backend/src/autotrader/services/executor.py \
        autotrader/backend/src/autotrader/services/admin_bot_notify.py \
        autotrader/backend/tests/test_admin_bot.py
git commit -m "feat(autotrader/admin-bot): trade.upserted carries ladder snapshot

executor._attempt_to_payload optionally embeds a 'ladder' object
with current/max for both streaks + a next_stake_hint computed
the same way risk_gate would size the next signal. The watcher's
_publish call passes the freshly-settled MartingaleState + cfg so
the snapshot is current.

format_trade_settled and format_trade_placed gain a ladder-line
appendix:
  📈 win streak 1/2 → next \$10        (winning streak active)
  📉 next: martingale recovery \$20    (loss-recovery active)

When neither ladder is in progress, the formatters render the
existing one-liner unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10 — End-to-end coexistence test

**Files:**
- Test: `autotrader/backend/tests/test_e2e_elite_scenario.py`

- [ ] **Step 10.1: Write the comprehensive coexistence test**

Append to `autotrader/backend/tests/test_e2e_elite_scenario.py`:

```python
async def test_full_coexistence_walkthrough_from_spec(
    async_client: httpx.AsyncClient,
) -> None:
    """End-to-end pin of the spec's Section-1 walkthrough.

    Both ladders enabled; mart_max=2, win_max=2, base=$5.
    The full sequence:
      T1 channel BUY  $5  LOSS → mart=1
      T2 bot recovery $10 LOSS → mart=2 (max), reset
      T3 channel SELL $5  WIN  → win=1, last_payout=$9.25
      T4 channel BUY  $10 WIN  → win=2 (max), reset
      T5 channel BUY  $5  WIN  → win=1, last_payout=$9.25
      T6 channel SELL $10 LOSS → mart=1, win reset
      T7 bot recovery $20 WIN  → mart=0, win=1, last_payout=$37
      T8 channel BUY  $37 (don't settle; just verify the stake)

    Mirrors the design spec line-for-line so any future refactor
    that drifts from the documented behaviour fails this test.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 2,
            "reset_on_win": True,
            "auto_recovery": True,
            "winning_streak_enabled": True,
            "winning_streak_max_level": 2,
        },
        default_stake=5.0,
    )
    await _activate()

    # T1 + T2: loss → mart recovery → loss → cap → reset.
    WatcherFakeQuotex.next_outcomes = [("loss", -5.0), ("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)
    await _settle_watchers(async_client)
    assert [c["amount"] for c in WatcherFakeQuotex.buy_calls] == [5, 10]

    # T3: clean win, base $5, win streak ticks to 1, last_payout=$9.25.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _dispatch(async_client, chat_id=-1001, text="SELL GBPJPY 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[2]["amount"] == 5

    # T4: streak step 1 → next $10 (ceil(9.25)). Win at $10, hit max=2, reset.
    WatcherFakeQuotex.next_outcomes = [("win", 8.5)]
    await _dispatch(async_client, chat_id=-1001, text="BUY USDCAD 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[3]["amount"] == 10

    # T5: reset → base $5 again. Win → win streak=1.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURJPY 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[4]["amount"] == 5

    # T6: streak step 1 → next $10. Lose at $10 → win resets, mart=1.
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="SELL CHFJPY 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[5]["amount"] == 10

    # T7: bot recovery at $20. Win → mart resets, recovery wins
    # advance the streak to 1, last_payout = 20+17 = $37.
    WatcherFakeQuotex.next_outcomes = [("win", 17.0)]
    await _settle_watchers(async_client)  # drain T6's loss-then-recovery
    await _settle_watchers(async_client)  # recovery's own watcher
    assert WatcherFakeQuotex.buy_calls[6]["amount"] == 20

    # T8: streak step 1 → next $37 (ceil(37.0)).
    WatcherFakeQuotex.next_outcomes = [("win", 31.45)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[7]["amount"] == 37, (
        f"T8 must size at ceil(last_payout=37.0); got "
        f"{WatcherFakeQuotex.buy_calls[7]['amount']}"
    )
```

- [ ] **Step 10.2: Run it**

```bash
cd autotrader/backend
uv run pytest tests/test_e2e_elite_scenario.py::test_full_coexistence_walkthrough_from_spec -v
```

Expected: PASS. (If the recovery-wins-advance-streak rule from Task 3 is wired correctly, T7→T8 stake is $37 not $5.)

- [ ] **Step 10.3: Run the full backend suite + ruff one final time**

```bash
cd autotrader/backend
uv run pytest 2>&1 | tail -3
uv run ruff check src tests 2>&1 | tail -5
```

Expected: all tests pass; ruff delta clean (same baseline error count as before this plan started).

- [ ] **Step 10.4: Commit**

```bash
git add autotrader/backend/tests/test_e2e_elite_scenario.py
git commit -m "test(autotrader/e2e): full martingale + winning-streak coexistence walkthrough

Pins the spec's Section-1 walkthrough as an executable contract:
8 trades exercising all the interesting state transitions (loss
recovery, streak progression, max-level reset, mid-streak loss,
recovery-wins-advance-streak, last_payout compounding).

Any future refactor that drifts from documented behaviour now
fails this test instead of silently changing operator-visible
sizing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11 — Final live verification

**Files:** None (operational).

- [ ] **Step 11.1: Backend full suite + lint**

```bash
cd autotrader/backend
uv run pytest 2>&1 | tail -3
uv run ruff check src tests 2>&1 | tail -3
```

Expected: ~333 passed (319 baseline + ~14 new across this plan); ruff clean delta.

- [ ] **Step 11.2: Frontend type-check + build**

```bash
cd autotrader/frontend
bun run type-check
bun run build 2>&1 | tail -3
```

Expected: clean.

- [ ] **Step 11.3: Existing Playwright e2e smoke**

```bash
cd autotrader/frontend
bun run test:e2e 2>&1 | tail -10
```

Expected: 4/4 still passing.

- [ ] **Step 11.4: Rebuild and restart the live container**

```bash
cd autotrader
docker compose build api web 2>&1 | tail -3
docker compose up -d --force-recreate api web 2>&1 | tail -3
sleep 8
curl -s http://localhost:8000/health
```

Expected: API healthy, no startup errors.

- [ ] **Step 11.5: Verify warm-up landed in logs**

```bash
docker logs autotrader-api 2>&1 | grep "pipeline.warm_up\|pipeline.peer_cache" | tail -5
```

Expected: a `pipeline.warm_up` log line with `built=N, failed=M` where N matches the operator's enabled-parser count.

- [ ] **Step 11.6: Verify cached_parser_count == enabled_parser_count via API**

```bash
PASS=$(grep AUTOTRADER_PASSCODE autotrader/.env | cut -d= -f2)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"passcode\":\"$PASS\"}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/pipeline/status | python3 -m json.tool | grep -E "cached|enabled|subscribed"
```

Expected: `cached_parser_count` equals `enabled_parser_count` (or `enabled_parser_count - <build_failures>` if any parsers have invalid config). `subscribed_chat_count == watched_chat_count`.

- [ ] **Step 11.7: Manual scenario verification (operator-driven, optional but recommended)**

In the dashboard:

1. Open a parser with both martingale + winning-streak enabled.
2. Verify the editor shows both blocks correctly.
3. Open `/dashboard/pipeline` and verify the streaks table renders the new "Win step" and "Last payout" columns.
4. Trigger a winning trade via the test channel; verify:
   - The streaks table updates `Win step` from "0" to "1 / 2".
   - The next signal's pre-trade log shows `next stake = ceil(last_payout)`.
   - The admin bot DM (if configured) includes a `📈 win streak 1/2 → next $X` line.
5. Trigger a losing trade; verify the win streak resets to 0 in the streaks table.

- [ ] **Step 11.8: No commit if all green; record any follow-up bugs as a fresh patch**

This task ends with no commit if every gate is green.

---

## Self-Review

**Spec coverage check:**

- §1 Behavior model — covered by Task 3 (record_outcome) + Task 4 (risk_gate) + Task 10 (E2E walkthrough)
- §2 Schema — Task 1 (DB) + Task 5 (API)
- §3 UI — Task 8
- §4 Eager warm-up — Task 6 + Task 7
- §5 Testing — distributed across all tasks; the E2E walkthrough in Task 10 is the integration spine
- §6 Admin bot — Task 9

All six spec sections have an implementing task.

**Placeholder scan:** Every code step has full code. No "implement N appropriately" / "fill in details" — every assertion, every regex, every commit message is shown.

**Type / signature consistency:**

- `record_outcome` signature in Task 3 matches the call in Task 3.5 (executor) and the test in Task 3.1.
- `_round_stake(value: float) -> int` defined in Task 2, referenced in Task 4 risk-gate and Task 9 admin-bot payload.
- `Pipeline.warm_up()` and `Pipeline.prebuild()` defined in Task 6, referenced in Task 7 routers + Task 6.4 lifespan.
- `MartingalePayload` field names (`winning_streak_enabled`, `winning_streak_max_level`) consistent across Task 1 (model), Task 5 (API schema), Task 8 (frontend type), Task 10 (E2E test fixture).
- `StreakRow` field names (`current_win_streak`, `last_payout`, `winning_streak_enabled`, `winning_streak_max_level`) consistent across Task 5 + Task 8.
- `_attempt_to_payload(attempt, *, state=None, cfg=None)` signature in Task 9 used at the single call site updated in Task 9.

No drift detected.
