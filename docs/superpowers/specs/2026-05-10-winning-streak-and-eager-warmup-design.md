# Winning-Streak (Paroli) Sizing + Eager Parser Warm-Up

**Date:** 2026-05-10
**Author:** Claude (with <imran.ahmedani@gmail.com>)
**Branch:** `claude/ui-modernization-phase-3-analytics-depth`
**Status:** drafted — awaiting user approval

## Problem

Two related gaps surfaced while operating the live container:

1. **No win-side stake-progression rule.** The existing martingale handles
   the loss side (double up to N levels, then reset) but there's no
   symmetric win-side compounding. Operators following channel guidance
   like *"after a win, ride the winnings up to 2 levels"* have to size
   manually or write external bookkeeping. Most binary-options
   strategies pair a loss-recovery with a small Paroli ladder; only
   shipping one half forces operators to choose.

2. **Lazy parser cache misleads the dashboard.** `Pipeline._get_or_build`
   only materialises a parser the first time its chat receives a
   message. With 7 enabled parsers across 4 chats, the live
   `cached_parser_count` gauge sits at 1 until each chat happens to
   post a signal — operators cannot tell at a glance whether their
   configs are *valid* (compile cleanly) until a real message
   arrives. A bad regex slips through `_validate_compiles` (which
   only validates structure) and silently emits `build_failed`
   decisions only when traffic reaches it. Reload-then-wait is the
   debugging UX, when reload-then-immediately-see-status would be
   sharper.

## Non-goals

- The streak's stake formula is fixed at `ceil(prev_stake + prev_profit)`
  per user decision — payout-rate variability is intrinsic, not a knob.
  No "use only profit" or "fixed multiplier" alternatives ship in
  this design.
- Streak-mode auto-fire (firing same-direction trades on win without
  waiting for a channel signal) is explicitly out of scope. Streak
  resizes the *next channel signal*; martingale's `auto_recovery`
  is the only auto-fire path.
- The admin-bot notification update is *in scope* (Section 6) but
  limited to extending existing message formats with ladder context.
  No new admin-bot commands.

## Design

Six independent sections, each landing as its own commit.

### Section 1 — Behavior model

**Two independent toggles per parser config, both may coexist.** A loss
resets the win counter; a win resets the martingale counter (with
existing `reset_on_win=True`). At runtime they're mutually exclusive
— `current_streak > 0 ↔ current_win_streak == 0` and vice versa —
so risk-gate sizing never has to reconcile both.

| Outcome | Martingale enabled (existing) | Winning-streak enabled (new) |
|---|---|---|
| **Win** | reset `current_streak` to 0 | increment `current_win_streak`; record `last_payout = stake + profit`; if `current_win_streak >= max_win_level`, reset to 0; the **next channel signal** stakes at `ceil(last_payout)` |
| **Loss** | if `auto_recovery=True`, fire same asset/direction/duration recovery at `base × multiplier^step`; advance `current_streak`; reset on hit max | reset `current_win_streak` to 0; clear `last_payout`; **next channel signal** stakes at base |

**Recoveries advance the streak.** A martingale recovery trade that
wins is treated identically to a channel-fired winning trade for
streak-progression purposes: `current_win_streak` advances and the
next channel signal compounds. This matches operator intuition
("a win is a win").

**Concrete walkthrough** (base=$5, mart_mult=2, mart_max=2, win_max=2):

```text
T1: channel BUY EURUSD  $5    LOSS  → mart step=1, win=0
T2: bot recovery (auto) $10   LOSS  → mart step=2 (max), reset to base; win=0
T3: channel SELL GBPJPY $5    WIN   → profit $4.25; mart=0; win=1, last_payout=$9.25
T4: channel BUY USDCAD  $10   WIN   → profit $8.50; mart=0; win=2 (max), reset
T5: channel BUY EURJPY  $5    WIN   → profit $4.25; mart=0; win=1, last_payout=$9.25
T6: channel SELL CHFJPY $10   LOSS  → mart step=1; win reset to 0
T7: bot recovery (auto) $20   WIN   → profit $17.00; mart=0; win=1, last_payout=$37.00
T8: channel BUY EURUSD  $37   …
```

Stakes always integer (Quotex constraint): `ceil(prev_stake +
prev_profit)`. The base stake is whatever's stored on the parser
config; integer expected.

### Section 2 — Schema + API

**`parser_config` table** — two new fields, default off so existing
rows are byte-identical:

```python
winning_streak_enabled: bool = Field(default=False, nullable=False)
winning_streak_max_level: int = Field(default=2, nullable=False)
```

**`martingale_state` table** — extend the existing per-parser runtime
row (one row per `parser_config_id`) rather than introduce a parallel
table:

```python
current_win_streak: int = Field(default=0, nullable=False)
last_payout: float = Field(default=0.0, nullable=False)
```

`last_payout` is the explicit source of truth for the next streak
step's stake. Storing it avoids re-deriving from the trade-attempts
audit log on every signal (fast + simple) and keeps the
reset-on-config-edit semantics atomic.

**`/parsers/configs` payload** — extend `MartingalePayload` (already
the umbrella for recovery knobs):

```typescript
interface MartingalePayload {
  enabled: boolean;
  multiplier: number;
  max_streak: number;
  reset_on_win: boolean;
  auto_recovery: boolean;
  // NEW
  winning_streak_enabled: boolean;
  winning_streak_max_level: number;
}
```

`max_streak` (martingale's recovery cap) and `winning_streak_max_level`
are independent fields; `multiplier` is currently re-used for both
ladders (loss-side from `martingale_multiplier`; win-side derives
from actual broker payout via `last_payout`, so multiplier doesn't
gate it). Forward-compatible: payloads omitting the new fields
default to `winning_streak_enabled=false, max_level=2`.

**`/risk/overview` response** — extend `StreakRow` with two new fields
so the dashboard table can show both ladder positions per parser:

```python
class StreakRow(BaseModel):
    # existing fields…
    current_win_streak: int = 0
    last_payout: float = 0.0
```

**`/risk/streaks/{id}/reset` endpoint** — extend to also zero
`current_win_streak` + `last_payout`. One button, both ladders
cleared.

### Section 3 — UI

**Parser editor** (`autotrader/frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx`)
— add a `WinningStreakBlock` component below the existing
`MartingaleBlock`. Same visual shape (toggle + numeric input)
so operators recognise the pattern. Copy:

> **Winning streak.** On a win, the next channel signal stakes at
> `ceil(prev_stake + prev_profit)` up to max level, then resets to
> base. A loss at any point also resets to base. Stakes round up
> to the nearest integer (Quotex constraint).
>
> Max win streak level: [ 2 ]   ☑ Enable

**Pipeline page martingale-streaks table** (`/dashboard/pipeline`,
`panel-martingale-roi.tsx` and the streaks panel) — extend the
existing table with two columns: `Win step`, `Last payout`. Both
ladder positions visible at a glance:

| Parser | Mult | Recovery | Step | **Win step** | **Last payout** | Last | Last stake | Updated | |
|---|---|---|---|---|---|---|---|---|---|
| EliteLive | ×2 | 1 | base | 0 | — | — | — | — | Reset |
| TestSignal | ×2 | 1 | base | 1 | $9.25 | won | $5.00 | … | Reset |

The "Reset" button per row continues to call `/risk/streaks/{id}/reset`
which zeroes both ladders.

**Frontend types** — `lib/api.ts`:

```typescript
interface MartingalePayload {
  // existing…
  winning_streak_enabled: boolean;
  winning_streak_max_level: number;
}
interface StreakRow {
  // existing…
  current_win_streak: number;
  last_payout: number;
}
```

### Section 4 — Eager parser warm-up (bug fix)

Lazy `_get_or_build` is replaced with eager warm-up at three points:

**On lifespan startup** (`main.py`) — after `executor.reconcile_pending()`
completes and before the Telegram handler is attached:

```python
# Build every enabled parser so the cache reflects "what's live"
# rather than "what's been touched since boot". Failures surface
# as build_failed decisions in the ring buffer; the lifespan
# proceeds either way.
await pipeline.warm_up()
```

**On parser create/update** (`routers/parsers.py`) — after
`create_config` / `update_config` returns:

```python
pipeline.invalidate(config_id)
if row.enabled:
    pipeline.prebuild(row)
```

So the editor's "Save" makes the parser materially live in the
pipeline immediately — no "wait for next message" to confirm the
config compiles.

**On chat watch toggle** (`routers/telegram.py`) — when
`/telegram/watch` is `enabled=True`, after `subscribe_chat`
succeeds, walk the chat's parsers and warm them.

**New methods on `Pipeline`:**

```python
class Pipeline:
    async def warm_up(self) -> dict[str, int]:
        """Build parsers for every enabled config row.

        Idempotent: re-running re-validates configs. Returns
        ``{built: N, failed: M}`` for log + telemetry. Failures
        record a ``build_failed`` decision and continue.
        """

    def prebuild(self, cfg: ParserConfig) -> bool:
        """Build a single parser. Returns True on success.

        Surfaces ``build_failed`` decisions for invalid regex /
        missing required fields. The router uses this on POST/PUT
        so the editor's Save lands a working parser or surfaces
        the error in the decision feed before the next signal.
        """
```

**Invariant after warm-up**:
`cached_parser_count == enabled_parser_count - build_failures`. The
dashboard's pipeline-status gauge becomes meaningful.

**Race / sequencing notes:**

- Warm-up runs *after* `executor.reconcile_pending()` so any
  in-flight watcher tasks settle their state before parsers start
  dispatching.
- Warm-up runs *before* the Telegram message handler is attached
  (line 215 in `main.py`), so by the time messages flow in, all
  parsers are ready.

### Section 5 — Testing

Backend pytest:

1. **Streak ladder unit test** (`test_risk.py`):
   - base $5, max=2: T1 win → step=1, last_payout=$9.25; T2 channel
     → stake=ceil(9.25)=$10; T2 win → step=2 (max), reset; T3 channel
     → stake=$5.

2. **Mid-streak loss triggers martingale recovery, recovery win
   advances the streak** (`test_e2e_elite_scenario.py`): both
   ladders enabled. $5 win → next $10 (streak 1). $10 loss →
   win reset, martingale fires $20 recovery. $20 win → mart
   resets, **win_streak=1 (recovery wins count, per Section 1)**,
   next channel stakes at `ceil(20+17)=$37`. Asserts both
   ladder transitions explicitly.

3. **Quotex integer-stake constraint** — parametrised:
   `(5, 4.25) → 10`, `(10, 8.50) → 19`, `(19, 16.15) → 36`,
   `(50, 42.50) → 93`. Pin via a `_round_stake` helper test.

4. **Eager warm-up** (`test_startup_recovery.py`):
   - Seed 3 enabled + 2 disabled parsers. Lifespan startup →
     `cached_parser_count == 3`.
   - One parser has invalid regex → warm-up records `build_failed`;
     lifespan completes; remaining 2 parsers cached.
   - POST `/parsers/configs` with valid body → cache count ticks
     up by 1 immediately, before any message arrives.
   - PUT toggling `enabled: false` → cache count ticks down.
     Toggling back → ticks up.

5. **State reset on parser config edit** — `_config_signature`
   drift rebuilds the parser and zeroes `current_win_streak` +
   `last_payout`. Dropping in-progress streaks on edit is
   operator-friendly: editing a parser shouldn't carry stale
   ladder state.

6. **Risk overview API** — `GET /risk/overview` includes
   `current_win_streak` + `last_payout` per parser.

Frontend e2e:

7. Existing `dashboard.spec.ts` smoke stays green.
8. New step: hit `/dashboard/pipeline`, assert the streaks table
   renders both `Step` and `Win step` columns and a `Last payout`
   column.

Manual verification (DEMO + sandbox channel):

9. Configure a parser with both martingale and winning-streak
   enabled. Send 3 winning signals via the test channel; verify
   dashboard shows win_streak = 1, 2, then 0 (max reset). Send a
   losing signal; verify martingale fires recovery and win_streak
   stays 0. Verify integer stakes throughout.

### Section 6 — Admin bot notification updates

The admin bot's notifier already publishes `placed`, `settled`,
`risk_rejected`, `system_error` via `services/admin_bot_notify.py`.
The format functions live there as `format_trade_placed`,
`format_trade_settled`, etc. — extend their templates with ladder
context so operators DMs surface streak progress.

**`format_trade_placed`** — add a single-line ladder hint when the
parser has either ladder active:

```
🎯 BUY EURUSD 1m · $10 (DreamVIP)
   📈 win streak 1/2 (compounding)         ← NEW when current_win_streak > 0
   ⏱ scheduled at 14:30 UTC
```

**`format_trade_settled`** — append the post-trade ladder state so
operators see the next-stake hint:

```
✅ WON +$4.25 EURUSD 1m (DreamVIP)
   📈 win streak now 2/2 → next $5 (max hit, reset)   ← NEW
```

```
❌ LOST -$10 EURUSD 1m (DreamVIP)
   📉 next: martingale recovery $20             ← NEW (auto_recovery on)
```

**`format_risk_rejected`** — already includes the rejection reason;
no change needed (the new code paths produce honest reasons via
`auto_recovery.skipped` etc.).

**Implementation:** the notify pipeline already receives the full
`TradeAttempt` payload via `event_bus.publish("trade.upserted", ...)`.
To include ladder state, the executor's `_publish` payload extends
to embed the post-settle `MartingaleState` snapshot:

```python
def _attempt_to_payload(attempt, state):
    return {
        # existing fields…
        "ladder": {                          # NEW
            "current_streak": state.current_streak,
            "max_streak": cfg.martingale_max_streak,
            "current_win_streak": state.current_win_streak,
            "max_win_streak": cfg.winning_streak_max_level,
            "next_stake_hint": _next_stake_for_parser(cfg, state),
        } if state else None,
    }
```

Frontend's existing `TradeAttempt` type adds an optional `ladder`
field; the trades list and decision feed can render the next-stake
hint inline. This is additive — clients ignoring the field stay
green.

**Test:** `test_admin_bot.py` extends one existing notify scenario
to assert the new lines render with non-zero ladder state, and
that they're absent when both ladders are at base.

## Risk register

- **Quotex's actual minimum stake constraint.** Quotex DEMO accepts
  $1 minimum; REAL may be higher per asset. `_round_stake` returns
  ceil; if base + ladder produces a stake < min, the broker rejects
  with a confusing error. Mitigation: clamp `_round_stake` to a
  configurable floor (default $1) and surface a warning in the
  decision feed when the clamp triggers.
- **Long winning streaks compound the bet aggressively.** Three
  $5 → $10 → $19 → $36 wins in a row commit $36 on the 4th step.
  An unlucky loss there is large. Operator intent: that's the
  Paroli design — they enable it knowing the risk profile. Daily
  caps still apply via the existing `daily_max_loss` / `daily_max_stake`
  guards.
- **Broker payout variability invalidates fixed-multiplier
  assumptions.** Asset A pays 85%, Asset B pays 92%. The streak
  uses `last_payout` which captures actual broker numbers, so
  per-asset variance is handled. No assumption hardcoded.
- **State drift if the executor crashes mid-settle.** A trade
  settles but `record_outcome` fails before the streak counter
  ticks — the ladder is one step behind. Same risk class as
  martingale's existing edge case. Mitigation: same as today —
  the manual "Reset streak" button per parser. No new tooling.
- **Backwards compatibility.** Two new SQL columns, default values
  preserve existing-row behaviour. Existing parser configs see
  `winning_streak_enabled=False` so the new code paths are no-ops
  until an operator opts in.

## Verification plan (post-implementation)

1. `cd autotrader/backend && uv run pytest` — full suite passes
   (327+ tests after additions).
2. `cd autotrader/backend && uv run ruff check src tests` — clean
   delta.
3. `cd autotrader/frontend && bun run type-check && bun run build` —
   clean.
4. `cd autotrader/frontend && bun run e2e` — smoke + new pipeline
   columns assertion.
5. Live verification:
   - Restart container; `cached_parser_count == enabled_parser_count`
     within 5 seconds.
   - Configure a parser with both ladders enabled.
   - Trigger a 3-trade winning sequence via the sandbox channel;
     observe DM messages with `win streak 1/2 → next $X` lines.
   - Trigger a loss; observe martingale recovery DM.
   - Confirm `last_payout` matches what the broker reported in
     `/dashboard/trades`.
