# Production-Ready Tier-0 — Design Spec
**Date:** 2026-05-14
**Branch base:** `master` (post audit 2026-05-13 phases 0–4)
**Companion docs:** `AUDIT_2026-05-13.md`, `FOLLOWUPS_2026-05-13.md`
**Goal:** Land the minimum set of changes required to flip
`AUTOTRADER_LIVE_TRADING_ENABLED=true` and trade real money on the
Quotex broker with operator-acceptable risk.

---

## 1. Context and motivation

The autotrader is being prepared for real-money cutover on a single
VPS running the existing `docker-compose.prod.yml` (Caddy/Nginx in
front, restart-always, online SQLite backups, optional Sentry).
Audit 2026-05-13 closed every Critical, High, and verified Medium
finding; the items that remain in `FOLLOWUPS_2026-05-13.md` are
deferrals that need real-world data, owner judgement, or have a
clear "later, with trigger" disposition.

The blocker the operator named when scoping this work is **broker
reliability**, with two failure modes already seen in the wild:

1. **Connect-rejection at startup** — pyquotex returns the generic
   `Websocket connection rejected.` from `client.connect()`. Current
   active incident at the time of writing (2026-05-14). A `curl_cffi`
   profile sweep against `qxbroker.com/en/sign-in` ruled out
   Cloudflare fingerprint regression for the operator's existing
   `firefox144` profile; the most likely cause is a soft-flag from
   the 2026-05-12 PIN-email storm.
2. **WS drops mid-session with the pyquotex reconnect supervisor
   looping** — the cosmetic `_HARD_OUTAGE_AFTER_ATTEMPTS = 10`
   downgrade does not actually stop the supervisor, which means a
   soft-flagged account gets deeper soft-flagged by retry spam.

This spec is **Tier-0 only**: changes load-bearing for the env-var
flip. Everything else stays in `FOLLOWUPS_2026-05-13.md`.

## 2. Non-goals (explicit out-of-scope)

The following items are recognised as future work but deliberately
NOT in this spec:

- Decimal money columns (FOLLOWUPS §A1) — `round(..., 2)` workaround
  shipped Phase 4 holds.
- Alembic adoption (§A2) — `_migrate_in_place` continues for now.
- Event-bus persistence (§A3) — Sentry covers the immediate gap.
- Frontend retry/backoff (§A4) — dashboard reload still works.
- M6 broker auth error taxonomy (§B3) — **the probe in §3.2 below
  collects the data; the dictionary is built later from real samples.**
  Shipping a guessed dictionary now invites false confidence.
- L3 (`/auth/me` wiring), L5 (ticker alias externalisation),
  M2 (`prep_trigger` silent expiry) — no operator trigger reported.
- Pyrofork test seam baseline (FOLLOWUPS §C, 31 stable-red tests) —
  promote only when blocking a real PR.

If any of these turn out to be load-bearing after the soak in §7
they get their own follow-up PR — not this one.

## 3. Components (six work items)

### 3.1 Pre-startup connect probe — `quotex_manager._do_connect:SETUP`

**Problem.** pyquotex burns OTP-supervisor retry budget against
broker errors that aren't actually credentials- or OTP-related
(Cloudflare 403, broker 5xx, regional block). When `client.connect()`
returns `(False, ...)` we lose the original cause and present a
generic error to the operator.

**Change.** Add `_preflight_check()` helper, called inside
`_do_connect`'s SETUP phase (the locked phase from Phase 3a)
*before* the lock-free `client.connect()` call.

```python
async def _preflight_check(self) -> None:
    """One-shot HTTP HEAD/GET against the broker sign-in page to
    catch hard failures (Cloudflare 403, upstream 5xx) before
    pyquotex burns OTP budget on them. Network errors fall through
    to pyquotex — the probe isn't conclusive in that case."""
    try:
        resp = await asyncio.to_thread(
            curl_requests.get,
            "https://qxbroker.com/en/sign-in",
            impersonate=settings.curl_cffi_profile,  # firefox144 default
            timeout=5.0,
        )
    except (curl_requests.RequestsError, TimeoutError) as exc:
        log.warning("broker.preflight.network_error", detail=str(exc))
        return  # fall through to pyquotex
    if resp.status_code == 403:
        log.error("broker.preflight.cloudflare_403",
                  impersonate_profile=settings.curl_cffi_profile,
                  body_bytes=len(resp.content))
        raise BrokerPreflightFailed(
            "cloudflare 403 — fingerprint regression suspected; "
            "see runbook §B (rotate curl_cffi profile)"
        )
    if 500 <= resp.status_code < 600:
        log.error("broker.preflight.upstream_5xx",
                  status=resp.status_code)
        raise BrokerPreflightFailed(
            f"broker upstream returned {resp.status_code} — "
            "check brokerstatus + retry in 5 min"
        )
    log.info("broker.preflight.ok", status=resp.status_code)
```

The new `BrokerPreflightFailed` exception is caught by `_do_connect`'s
existing error branch (line ~429) so the failure populates
`last_error` cleanly. The `curl_cffi_profile` is exposed via the
existing settings module.

### 3.2 Rejection probe (data collection for future M6)

**Problem.** Today, when `client.connect()` returns `(False, ...)`
we set `last_error = f"{type(exc).__name__}: {exc}"` and lose
everything else. We can't tell whether the rejection is "credentials
stale", "soft-flagged IP", "broker maintenance", or "WS upgrade
denied". The audit deferred M6 (the taxonomy dictionary) because
building it blind is worse than not having one — but we never put
collection infrastructure in place.

**Change.** Right at the WS-rejected branch of `_do_connect:FINALIZE`
(currently around `quotex_manager.py:533`), emit a single structured
log line `broker.connect.rejection_probe` that captures whatever
forensic state pyquotex's client exposes. No behavior change.

```python
log.warning("broker.connect.rejection_probe",
    raw_error=str(exc),
    error_class=type(exc).__name__,
    elapsed_ms=int((time.monotonic() - connect_start_ms) * 1000),
    auth_status=getattr(client.api, "auth_status", None),
    ssid_loaded=bool(getattr(client.api, "ssid", None)),
    is_authenticated=getattr(client.api, "is_authenticated", None),
    ws_url=getattr(client.api, "ws_url", None),
    impersonate_profile=settings.curl_cffi_profile,
    consecutive_otp_failures=self._consecutive_otp_failures,
)
```

Two weeks of these logs gives us real data to build M6's dictionary
from. Until then, the operator's runbook says "if you hit a
rejection, grab the probe log line and escalate."

### 3.3 Hard reconnect ceiling — `quotex_manager._on_reconnect_attempt_failed`

**Problem.** The existing `_HARD_OUTAGE_AFTER_ATTEMPTS = 10`
constant only flips a `recoverable: bool` flag on the bus message
for the admin notifier. The pyquotex supervisor keeps retrying
forever underneath. For real money this is wrong: a soft-flagged
account gets deeper-flagged by retry spam, and the operator can't
tell whether to wait or intervene.

**Change.** Split the constant into two:

```python
# Soft downgrade — change admin-bot tone from "transient" to "outage"
# (existing behaviour, renamed for clarity)
_SOFT_DOWNGRADE_AFTER_ATTEMPTS = 10

# Hard ceiling — actually stop trying. Operator must run /reconnect.
_HARD_CEILING_AFTER_ATTEMPTS = 20
```

When the failed-reconnect count reaches the hard ceiling, the
manager:

1. Stops the pyquotex `ReconnectPolicy` supervisor
   (`await client.api.reconnect_supervisor.stop()` — verified async
   in pyquotex `utils/reconnect.py:134`).
2. Calls `await client.disconnect()` to release the TCP socket cleanly.
3. Flips state to `awaiting_manual_recovery` (the existing literal,
   reused from the OTP-cap path).
4. Sets `last_error = "auto reconnect ceiling reached after N "
   "attempts; check account + IP, then run /reconnect"`.
5. Emits a new event-bus event
   `broker.reconnect_ceiling_reached` (also `recoverable=False`).

`/reconnect` already handles `awaiting_manual_recovery` — the change
is widening the cause set, not adding a new state. The admin bot's
notifier formats the existing "manual recovery required" message
without modification.

### 3.4 Pre-trade WS health gate — `executor._place`

**Problem.** The status watcher is event-driven, so there's a tiny
race between "WS dropped" and "watcher reacts". An order placed in
that window goes into a dead socket: pyquotex may report success
locally while the server never receives the order, leaving us with
a `pending` row that will never settle. The audit's reconcile path
handles it on restart, but the *correct* behaviour is to refuse the
trade upfront.

**Change.** Add `QuotexManager.assert_live(asset: str) -> None` —
raises `BrokerNotLive(reason: str)` when the broker layer is not
ready to accept an order.

```python
class BrokerNotLive(RuntimeError):
    """Raised by ``QuotexManager.assert_live`` when the executor
    must not send an order. The caller marks the attempt
    ``broker_error`` and does NOT advance the martingale ladder —
    the trade never reached the broker, so the ladder state is
    unchanged."""
    def __init__(self, reason: str, **detail: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail

async def assert_live(self, asset: str) -> None:
    if self._state != "connected":
        raise BrokerNotLive("not_connected", state=self._state)
    client = self._client
    if client is None or not getattr(client.api, "is_authenticated", False):
        raise BrokerNotLive("ws_not_authed")
    last_tick = self._last_tick_age_seconds(asset)
    if last_tick is None:
        raise BrokerNotLive("no_tick_seen", asset=asset)
    threshold = settings.stale_feed_max_age_seconds  # default 10
    if last_tick > threshold:
        raise BrokerNotLive(
            "stale_feed", asset=asset,
            age_seconds=last_tick, threshold=threshold,
        )
```

`_last_tick_age_seconds` is a new helper that reads from pyquotex's
internal candle/tick buffer for the asset (the same buffer the
candle subscribe machinery populates). If pyquotex doesn't expose
this cleanly, the fallback is a manager-side last-seen-per-asset
dict updated by the existing realtime subscriptions.

In `executor._place`, wrap the existing `client.buy` /
`client.open_pending` calls:

```python
try:
    await self.manager.assert_live(signal.asset)
except BrokerNotLive as exc:
    log.warning("executor.healthgate_blocked",
                attempt_id=attempt.id, reason=exc.reason, **exc.detail)
    await self._update_attempt(
        attempt.id,
        status="broker_error",
        broker_error=f"healthgate:{exc.reason}",
    )
    self._publish_decision(attempt, outcome="broker_error",
                           reason=f"healthgate:{exc.reason}")
    return attempt
```

**Why this doesn't tick the martingale.** The ladder is advanced by
`record_outcome` on settlement — `broker_error` is a non-settling
status, so the ladder is untouched. This is critical: a stale-feed
block must not be confused for a loss.

### 3.5 Graceful drain on shutdown — `main.py:lifespan` + `pipeline.py`

**Problem.** `executor.shutdown()` today cancels all watchers. Any
in-flight trade at shutdown loses outcome tracking — `reconcile_pending`
on next startup will mark it expired with the documented "outcome
unknown" note, and the martingale ladder is intentionally not
advanced. That's correct as a *backstop* but wrong as a *primary
path*: a planned deploy mid-trading-session shouldn't corrupt the
ladder.

**Change.** Add a one-way drain latch on `Pipeline`:

```python
class Pipeline:
    def __init__(self, ...):
        ...
        self._draining: bool = False

    def start_draining(self) -> None:
        """One-way latch. After this, dispatch() refuses all new
        signals with reason='draining'."""
        self._draining = True
        log.info("pipeline.draining")

    async def dispatch(self, message: RawMessage, ...) -> None:
        if self._draining:
            log.info("pipeline.refused", reason="draining",
                     chat_id=message.chat_id)
            return
        # ... existing dispatch logic
```

In `main.py:lifespan` shutdown sequence (before the existing
`executor.shutdown()`):

```python
pipeline.start_draining()
await executor.wait_for_pendings(timeout=300.0)  # 5 minutes
await executor.shutdown()
await manager.disconnect()
```

`executor.wait_for_pendings(timeout)` is new:

```python
async def wait_for_pendings(self, timeout: float) -> int:
    """Poll list_pending() every 2s, return when len==0 or timeout.
    Returns the number of still-pending rows at exit (0 on success).
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with AsyncSessionLocal() as session:
            remaining = await list_pending(session)
        if not remaining:
            log.info("lifespan.drain.complete")
            return 0
        if asyncio.get_running_loop().time() >= deadline:
            log.warning("lifespan.drain.timeout",
                        remaining=len(remaining))
            return len(remaining)
        await asyncio.sleep(2.0)
```

The 5-minute timeout: a 60s binary option needs `placed + 60 + 30
slack ≈ 90s` to settle. Doubling for the case of two stacked trades
in the concurrency-cap window → 180s. Rounding up to 300s gives a
generous margin without unbounded waits.

### 3.6 Smoke harness + runbook

**`tests/test_real_money_invariants.py`** — three integration tests
running against `FakeQuotex`, asserting invariants the operator
must trust before flipping the env var:

```python
async def test_kill_switch_blocks_all_signals_with_decision_row():
    # settings.kill_switch_engaged = True
    # dispatch a parseable signal
    # assert: NO executor.place log, ONE pipeline.decision row
    #         with outcome="block" reason="kill switch engaged"

async def test_daily_max_loss_blocks_mid_ladder():
    # seed: martingale step=2, last_stake=$20 (mid-ladder)
    # settings.daily_max_loss = $30, realised_pnl = -$30
    # dispatch a parseable signal
    # assert: blocked even though we're "due" for the recovery trade

async def test_disconnect_mid_ladder_does_not_advance_state():
    # seed: martingale step=1 (one loss)
    # _place succeeds; manager flips to reconnecting before settle
    # assert: attempt ends broker_error, current_streak still 1
```

These are the *load-bearing tests* — the only ones whose failure
should block the env-var flip. The per-component tests in §6 are
plumbing.

**`docs/RUNBOOK.md`** (new, ~1 page):

```markdown
# Autotrader Production Runbook

## A. Flipping the env var (going live)
... step-by-step: confirm soak, set caps to smallest workable
   values, edit .env, docker compose up -d, watch admin bot for
   first connect ...

## B. Broker rejection diagnostics
1. Grep last `broker.connect.rejection_probe` line.
2. If `error_class == "WebsocketConnectionRejectedException"`:
   most likely soft-flag. Try incognito web login. If that fails,
   wait 30 min, retry. If still failing, broker support.
3. If `broker.preflight.cloudflare_403` fires: rotate
   `AUTOTRADER_CURL_CFFI_PROFILE` (firefox144 → safari170 → firefox147).
4. ...

## C. Halt the bot (kill switch)
   /reconnect → /kill from admin bot. Confirms zero new dispatches.

## D. Restore from backup
   docker exec autotrader-api ls /data/backups/  (24 hourly retained)
   docker compose down
   cp <chosen-backup> /var/lib/.../autotrader.db
   docker compose up -d

## E. Reconnect ceiling escalation
   ... what to do when admin bot pings
   "auto reconnect ceiling reached" ...
```

## 4. State machine changes

The existing `BrokerState` literal type
(`quotex_manager.py:53`) already includes
`awaiting_manual_recovery`. The hard-ceiling change widens the
cause set into that state, with no new state introduced.

```
            idle
              │
              ▼
         connecting ──────► error                    (creds bad, pre-flight 403/5xx)
              │              │
              ▼              ▼
         awaiting_otp     awaiting_manual_recovery ◄──┐
              │              ▲                        │
              ▼              │ (OTP cap exhausted —   │
         connected     ◄─────┘  existing path)         │
              ▲              ▲                        │
              │              │  NEW EDGE:             │
              │              │  after _HARD_CEILING_  │
              │              │  AFTER_ATTEMPTS failed │
              │              │  reconnects, disconnect│
              │              │  + flip                │
              │              │                        │
              │           reconnecting ───────────────┘
              │              │
              └──────────────┘
                (success — existing path)
```

`Pipeline._draining` is a one-way latch — once true, never resets.
The lifespan owns its only transition.

## 5. Data flow summaries

(See §3.1 → §3.5 in-line diagrams. The five new flows are:
pre-flight, rejection probe, ceiling, health-gate, drain.)

## 6. Test plan

### Per-component (plumbing tests)

| File | Tests |
|---|---|
| `tests/test_phase_tier0_preflight.py` | 4: 403 blocks; 5xx blocks; network-timeout falls through; 200 continues silently |
| `tests/test_phase_tier0_rejection_probe.py` | 2: probe captures pyquotex state on WS reject; silent on success |
| `tests/test_phase_tier0_reconnect_ceiling.py` | 3: soft downgrade at 10 keeps supervisor running; hard ceiling at 20 disconnects + flips state; success resets counter |
| `tests/test_phase_tier0_health_gate.py` | 4: blocked when not authed; blocked when stale; blocked does NOT tick ladder; allowed when authed + fresh |
| `tests/test_phase_tier0_graceful_drain.py` | 3: drain refuses new dispatches; drain waits then shuts down; drain timeout logs remaining |

### Load-bearing (real-money invariants)

| File | Tests |
|---|---|
| `tests/test_real_money_invariants.py` | 3: kill switch blocks all; daily_max_loss blocks mid-ladder; disconnect mid-ladder does NOT advance martingale |

### Test-seam guardrails (from Phase 0 lessons)

- Every test defers autotrader imports inside the test function
  (`# noqa: PLC0415`) — module-level imports reorder Python's
  import graph and break unrelated tests (documented in
  `test_phase0_instrumentation.py`).
- Tests that mutate global settings or DB use `monkeypatch`
  fixture (not ad-hoc `MonkeyPatch()` instance) so teardown
  runs.
- DB-touching tests use an autouse fixture to wipe
  `trade_attempts`, `parser_configs`, `watched_channels`,
  `global_settings` before/after (Phase 2 pattern).

## 7. Acceptance criteria — observable signals

Six structured-log observations must hold across a 7-day demo-mode
soak before flipping the env var. Each is grep-able from container
logs.

| # | Log signal | Asserts |
|---|---|---|
| 1 | One of `broker.preflight.{ok,network_error}` before every `broker.connect.ok` | Pre-flight on critical path. `.network_error` is acceptable — it means probe ran and fell through, not that probe is broken. Absence of either log = bug. |
| 2 | Zero `broker.connect.rejection_probe` without a preceding `broker.preflight.{cloudflare_403,upstream_5xx}` *or* pyquotex-level cause | All rejections accounted for |
| 3 | `consecutive_failed_reconnects` never > 20 in any log | Hard ceiling holds |
| 4 | Every `executor.healthgate_blocked` followed by a `pipeline.decision` row with `outcome="broker_error"` | Health gate wired into decisions |
| 5 | After admin `kill switch engaged`, zero `executor.place` in next 5 min | Kill switch load-bearing |
| 6 | Every restart shows `lifespan.drain.complete` or `lifespan.drain.timeout` *before* `executor.shutdown` | Drain runs in correct order |

## 8. Rollout sequence

```
Day  0    Land all 6 work items on a feature branch (one PR per item
          or one combined PR; CI must be green either way).
Day  0    Merge to master. Deploy to VPS in demo mode.
Day  1-7  7-day demo-mode soak. Operator monitors Sentry + admin-bot
          pings. Acceptance criteria §7 verified against real logs.
Day  7    If clean: AUTOTRADER_LIVE_TRADING_ENABLED=true with
            daily_max_stake=5, daily_max_loss=10
          (smallest workable caps).
Day  7-14 Live-but-tiny soak. Raise caps only after 7 consecutive
          days with no daily-loss cap trigger.
```

The Tier-0 engineering work ends at Day 0. The soak is patience,
not work.

## 9. Risk register

| Risk | Mitigation |
|---|---|
| Pre-flight check times out routinely and hides real broker failures | Fall-through behaviour (§3.1) ensures pyquotex still runs. Probe logs the timeout so we'd notice in soak. |
| Hard ceiling at 20 attempts too aggressive — flaky network looks like a flag | Configurable via env var (`AUTOTRADER_RECONNECT_HARD_CEILING`). Default 20 is conservative; operator can raise to 50 if needed. |
| `_last_tick_age_seconds` reads stale internal pyquotex state, false stale-feed blocks | Fallback to manager-side last-seen dict updated by realtime subscription. Test §3.4's "fresh after subscribe" case. |
| Drain timeout traps shutdown for 5 min on a stuck broker | Log on timeout with remaining count; existing reconcile_pending backstops on restart. Operator can `docker compose kill` after 5min if needed. |
| Rejection probe leaks credentials in `raw_error` | `raw_error` is `str(exc)` — pyquotex exception messages don't include credentials by inspection. Add a `_redact()` helper if soak shows otherwise. |
| Smoke tests give false confidence — kill switch test passes but real switch is wired wrong | Acceptance criterion #5 spot-checks the *live* kill switch in production logs during the soak. |

## 10. File touch list

Modify:
- `autotrader/backend/src/autotrader/services/quotex_manager.py`
  (preflight, rejection probe, hard ceiling, `assert_live`)
- `autotrader/backend/src/autotrader/services/executor.py`
  (health gate in `_place`, `wait_for_pendings`)
- `autotrader/backend/src/autotrader/services/pipeline.py`
  (`_draining` latch, refuse-on-drain in `dispatch`)
- `autotrader/backend/src/autotrader/main.py`
  (lifespan drain sequence)
- `autotrader/backend/src/autotrader/settings.py`
  (`stale_feed_max_age_seconds`, `reconnect_hard_ceiling`,
   `curl_cffi_profile` if not present)

Create:
- `tests/test_phase_tier0_preflight.py`
- `tests/test_phase_tier0_rejection_probe.py`
- `tests/test_phase_tier0_reconnect_ceiling.py`
- `tests/test_phase_tier0_health_gate.py`
- `tests/test_phase_tier0_graceful_drain.py`
- `tests/test_real_money_invariants.py`
- `docs/RUNBOOK.md`

Estimated LOC: ~600 production + ~700 tests + ~250 runbook.

## 11. Open questions for the implementation plan

These are deliberately surfaced to the writing-plans phase, not
decided here:

1. **PR shape.** One bundled PR vs. six stacked? Recommend one
   bundle because the six items only make sense together (a
   reviewer asked "why a probe with no taxonomy?" needs the whole
   answer).
2. **`_last_tick_age_seconds` implementation.** Read from pyquotex
   internal buffer if available; otherwise add a manager-side
   per-asset last-seen dict. Decide during implementation after
   reading pyquotex's candle subscription code.
3. **`curl_cffi_profile` settings field.** May already exist —
   check `settings.py` before adding.

These are implementation choices, not design choices, so the spec
stays silent on them.
