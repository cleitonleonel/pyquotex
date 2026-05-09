# Parser & Trade-Pipeline Reliability Fixes

**Date:** 2026-05-10
**Author:** Claude (with imran.ahmedani@gmail.com)
**Branch:** `claude/ui-modernization-phase-3-analytics-depth`
**Status:** approved — ready for plan

## Problem

Two operator-visible regressions and one knowledge gap surfaced during a
day of live trading on top of the autotrader:

1. **Newly-watched channels don't deliver signals.** A user added the
   "Elite" channel mid-session; the `parsers/decisions` page never
   showed a single dispatch from it, while "DreamVIP" and the test
   channel (both added at login time) worked normally. Cause: when the
   `/telegram/watch` endpoint adds a new row to `watched_channels`, it
   never tells the live Pyrogram client to subscribe the channel's
   update stream. Pyrogram's update dispatcher silently drops
   `UpdateNewChannelMessage` for channels its in-memory peer cache
   hasn't touched this session, and `_prime_peer_cache` only runs at
   login or session-restore. Restarting the API picks the new chat up
   on the next prime — but operators shouldn't have to bounce the
   container to start trading a new channel.

2. **Pending trades all expire on every restart.** The screenshot in
   the bug report shows three trades stuck on `expired` with the note
   _"watcher lost on restart — pyquotex doesn't track tickets across
   reconnects, so the outcome can't be tied back."_ This is
   `executor.reconcile_pending` doing exactly what it was written to
   do: nuking every `pending` row at startup because the in-memory
   `_watch_result` task didn't survive. The current behaviour is
   correct in spirit but too aggressive — a trade whose
   `placed_at + duration_seconds` is still in the future hasn't even
   reached its expiry on the broker side. Marking it `expired` lies
   about its real state and (worse) silently confuses the martingale
   ladder for parsers in the middle of a recovery sequence.

3. **No first-class "how to write a parser" doc.** The four parser
   types (`template`, `regex`, `prep_trigger`, `batch`) are
   well-implemented, but the editor only carries inline help; an
   operator who hasn't read the code has no canonical reference. The
   troubleshooting section ("why isn't my parser firing?") doesn't
   exist in any docs file.

Two latent issues uncovered while auditing the pipeline:

4. **`executor._fire_auto_recovery` reads a stale parser config.**
   It uses the `cfg` snapshot captured at the original signal's
   settle. If the operator disables the parser mid-loss-streak, the
   next recovery still fires.

5. **Unwatching a chat leaks its parser cache** in
   `Pipeline._parsers`. Dispatch already filters via
   `watched.enabled`, so this is memory-only — but if the operator
   re-watches the same chat after editing one of its parser configs,
   the cached parser keeps the old shape until it next triggers a
   signature mismatch.

## Non-goals

This spec **does not** add, remove, or restructure parser features.
The four parser types, the `martingale_max_streak` field, the
streak-distribution analytics panel, and every existing UI control
stay exactly as they are. Per-field-regex, "Live" pill collapse, and
similar UX redesign ideas are out of scope.

## Design

Five patches across two services, one router, one frontend
invalidation hook, and one new docs file. Each patch is independently
reviewable; later patches don't depend on earlier ones landing first.

### Patch A — Subscribe new chats on `/telegram/watch`

**Files:** `services/telegram_manager.py`, `routers/telegram.py`,
`tests/test_telegram.py`, `frontend/lib/api.ts` (no change),
`frontend/app/dashboard/parsers/page.tsx` (cache-invalidation hook).

#### Backend

Add a single new method to `TelegramManager`:

```python
async def subscribe_chat(self, chat_id: int) -> None:
    """Force the live Pyrogram client to resolve + subscribe a chat.

    Uses the same ``get_chat_history(limit=1)`` touch that
    ``_prime_peer_cache`` already runs per watched chat. Idempotent —
    re-running on an already-subscribed chat is harmless. Raises
    :class:`TelegramManagerError` when not logged in or the chat
    can't be resolved (caller turns this into a 502).

    Bumps the live-update health gauge ``subscribed_chat_count`` so
    the dashboard can verify the new chat is actually subscribed,
    not just stored in SQLite.
    """
```

Implementation: hold `self._lock`, return early if `not self.logged_in`,
call `async for _ in client.get_chat_history(chat_id, limit=1): break`.
Wrap in try/except and surface `subscribe_failed` events on the bus
for the admin notifier (mirrors `peer_cache.failed`). Increment
`self._subscribed_chat_count` on success — `_prime_peer_cache` already
maintains this gauge; we keep it consistent.

`/telegram/watch` route:

```python
async def watch_endpoint(
    body: WatchRequest,
    session: SessionDep,
    manager: TelegramDep,           # NEW
) -> OkResponse:
    await upsert_watch(session, ...)
    if body.enabled and manager.logged_in:
        try:
            await manager.subscribe_chat(body.chat_id)
        except TelegramManagerError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"chat saved but subscribe failed: {exc}",
            ) from exc
    return OkResponse()
```

Order is `upsert_watch` first, then `subscribe_chat`: that way when
Pyrogram's `getChannelDifference` catch-up fires after the resolve, the
WatchedChannel row is already there for `Pipeline._dispatch_locked` to
find. We only subscribe when `enabled=True` so an operator can save a
"draft" disabled watch row without forcing a Pyrogram round-trip.

`DELETE /telegram/watch/{chat_id}`: also call
`pipeline.invalidate_for_chat(chat_id)` (new helper, see Patch E).
This drops cached parsers belonging to the unwatched chat. Plain
unwatch + re-watch already worked correctly because parser caches
rebuild lazily on signature mismatch; the explicit invalidation is
hygiene for the "unwatch and walk away" case.

#### Frontend

`app/dashboard/parsers/page.tsx` — when the watch mutation succeeds,
invalidate `["pipeline", "status"]` so the "Channels subscribed"
gauge refreshes. The existing query already polls every 5s; the
explicit invalidation just removes the polling-window delay.

#### Tests

- `test_watch_subscribes_chat`: a `FakeTelegramManager` with a
  recording `subscribe_chat` mock; assert it's called exactly once
  per `POST /telegram/watch` with the right `chat_id`.
- `test_watch_disabled_does_not_subscribe`: posting with
  `enabled=False` skips the subscribe call.
- `test_watch_subscribe_failure_returns_502`: when `subscribe_chat`
  raises `TelegramManagerError`, the route returns 502 and the
  watch row IS still persisted (so a retry is just another POST).
- `test_unwatch_invalidates_parser_cache`: assert
  `pipeline.invalidate_for_chat(chat_id)` is called.
- Existing `test_watch_then_unwatch` stays green (it doesn't log in,
  so the `manager.logged_in` short-circuit skips `subscribe_chat`).

### Patch B — Restart reconciliation that doesn't lie

**Files:** `services/executor.py`, `tests/test_startup_recovery.py`
(new tests), no schema change.

`reconcile_pending` is rewritten so each pending row is classified
into one of three buckets:

1. **`placed_at is None`** — broker never accepted the order. Mark
   `expired` immediately with the existing "watcher lost" note.
   This is the only case today's behaviour is correct for.
2. **`placed_at + duration_seconds + 60s slack > utcnow()`** — the
   broker is still in the binary-options window. Leave the row as
   `pending` and spawn a deferred-reconcile task that sleeps until
   `placed_at + duration_seconds + 60s` and then marks `expired` with
   a clearer note: _"settle window passed; broker likely settled this
   trade but pyquotex couldn't tie the result back. Check broker
   history if the outcome matters."_ Martingale state is **not**
   touched (no `record_outcome` call) — the row is excluded from
   win/loss accounting until manually adjudicated.
3. **`placed_at + duration_seconds + 60s slack <= utcnow()`** — the
   broker has already settled. Mark `expired` immediately with the
   same clearer note as case 2. No martingale tick.

The deferred-reconcile tasks register with the executor's
`_watchers` set so `shutdown()` awaits them, identical to the
existing `_watch_result` lifecycle. The 60-second slack is a
module-level constant `_RECONCILE_SLACK_SECONDS = 60` so the new
tests can monkey-patch it without sleeping in real time.

We deliberately keep the status as `expired` (not a new `unknown`
literal). The behavioural change — don't pre-emptively kill rows
that may still be live on the broker — is independent of the
status string. Adding a new literal would ripple through
`models.trade_attempt.TradeStatus`, `services/filters.py`,
`routers/stats.py`, `routers/stats_v2.py`, three frontend status
columns, and the admin-bot notification formatter. That refactor
is reasonable later when "I want to filter out indeterminate trades"
becomes a real ask; today, "expired with a clearer note + don't
touch the ladder" is enough.

#### Tests

- `test_reconcile_pending_placed_at_none_expires_immediately` —
  matches today's behaviour.
- `test_reconcile_pending_in_flight_stays_pending` — seed a row with
  `placed_at = now - 10s, duration_seconds = 60`; assert status
  stays `pending` after `reconcile_pending` returns; assert one
  background task is registered.
- `test_reconcile_pending_post_window_marks_expired` — seed a row
  with `placed_at = now - 600s, duration_seconds = 60`; assert
  status is `expired`, error mentions the new note, no martingale
  state was modified.
- `test_reconcile_pending_does_not_tick_martingale` — seed a parser
  with `martingale_enabled=True`, current_streak=2; reconcile a
  pending row; assert `martingale_state.current_streak == 2`
  unchanged.
- `test_reconcile_pending_deferred_task_marks_expired` — set system
  time forward, await the deferred task, assert status flips to
  `expired` with the new note.

### Patch C — Parser documentation

**Files:** `autotrader/docs/PARSERS.md` (new), `autotrader/README.md`
(link), `frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx`
(small "📖 Parser guide" link in the page header).

Markdown structure:

```
# Writing parsers
1. The four parser types (overview)
2. Live (template + regex) — how each placeholder maps to a regex group
3. Prep + Trigger — when to use it; common pitfalls (sticker direction)
4. Batch — header / row split, timezone handling
5. Direction tokens — full table from `normalize.py`
6. Duration units — full table; M5 / 1m / 60s
7. Asset resolution — alias → exact → OTC probe → fallback
8. Trade-mode pin — live / scheduled / auto semantics
9. Martingale — multiplier, max_streak, reset_on_win, auto_recovery
10. Why isn't my parser firing? — checklist:
    - watched chat enabled?
    - parser enabled? priority lower wins, but each enabled parser fires
    - dashboard /decisions page shows the dispatch?
    - subscribed_chat_count == watched_chat_count on /pipeline/status?
    - any errors in /pipeline/decisions for this chat?
11. Live tester — how to use the per-message blocks; sticker handling
```

The frontend link in the editor opens the doc in a new tab via a
hosted-readme path (relative `/docs/parsers` once the static-files
mount is added — see Patch E for that line, ~5 LOC). For now the
link target is the GitHub permalink to `autotrader/docs/PARSERS.md`
on the repo's master branch — works whether or not the API serves
static docs. The frontend env exposes `NEXT_PUBLIC_DOCS_URL` with
a sensible default.

No tests; this is documentation. Spec self-review covers the
checklist's accuracy.

### Patch D — Auto-recovery refetches the parser config

**Files:** `services/executor.py`, `tests/test_pipeline.py` (new test).

`_fire_auto_recovery` opens an `AsyncSessionLocal` and calls
`get_config(session, cfg.id)`. If the row is missing (deleted) or
`enabled=False`, log `executor.auto_recovery.skipped` with a reason
and return. Otherwise use the *fresh* row for the recovery
`submit(...)` call. The existing flow that uses the cached `cfg` is
replaced; no API change.

#### Test

- `test_auto_recovery_bails_when_parser_disabled` — disable the
  parser between the loss settle and the recovery dispatch; assert
  no recovery `TradeAttempt` is inserted; assert structlog
  `auto_recovery.skipped` was emitted.

### Patch E — Pipeline cache hygiene

**Files:** `services/pipeline.py`, `tests/test_pipeline.py`,
`routers/telegram.py` (already covered by Patch A).

Add `Pipeline.invalidate_for_chat(chat_id: int) -> None`:

```python
def invalidate_for_chat(self, chat_id: int) -> None:
    """Drop every cached parser whose row's chat_id matches.

    Called when a chat is unwatched so the cached parsers don't
    occupy memory until the lazy signature check on next dispatch.
    """
    for cfg_id in [
        cfg_id for cfg_id, cached in self._parsers.items()
        if cached.config_row.chat_id == chat_id
    ]:
        self._parsers.pop(cfg_id, None)
```

Plus a one-line static-file mount in `main.py` so the README link
in Patch C can target a local path when running offline:

```python
app.mount("/docs", StaticFiles(directory="docs", html=True), name="docs")
```

Behind a try/except — if the docs directory is missing in some
exotic deploy, the app still boots.

#### Tests

- `test_pipeline_invalidate_for_chat_drops_caches`: build two
  cached parsers (one for `chat_id=-100A`, one for `-100B`), call
  `invalidate_for_chat(-100A)`, assert only the A-side cache is
  gone.

## Verification plan

After all five patches land:

1. `cd autotrader/backend && uv run pytest` — full backend suite
   stays green; the new tests pass.
2. `cd autotrader/backend && uv run ruff check src tests` — lint
   stays clean.
3. `cd autotrader/frontend && bun run type-check && bun run build` —
   frontend types stay clean. (Patch A's only frontend change is
   one `qc.invalidateQueries` line; Patch C's is a `<a href>`.)
4. `cd autotrader/frontend && bun run e2e` — existing Playwright
   smoke test stays green; one new step in the watch flow
   verifies `subscribed_chat_count` ticks up after watching a
   new chat.
5. **Manual end-to-end check** (the user's reported regression):
   - Start the API + dashboard.
   - Add a watched chat (ideally a sandbox channel the user
     controls so they can trigger messages on demand).
   - Add a parser config and enable it.
   - Send a test message that the parser should match.
   - Confirm `/dashboard/decisions` shows the matched dispatch
     **without** restarting the API.
   - Confirm the trade row appears in `/dashboard/trades`.
   - Restart the API mid-trade window; confirm the in-flight row
     stays `pending` until its `placed_at + duration` passes,
     then flips to `expired` with the new note.

## Risk register

- **Pyrogram subscription latency.** `get_chat_history(limit=1)` is
  a single MTProto round-trip; from the codebase comments and
  experience, ~200 ms in steady state. The watch endpoint blocks on
  it, so a slow Telegram link will slow the watch POST. Mitigation:
  return 502 with the chat row already saved, so a retry just
  POSTs again; the SQLite write isn't undone.
- **Long-window deferred reconcilers.** A 4-hour binary option
  placed right before restart will leave a deferred task sleeping
  ~4 hours. `executor.shutdown()` already awaits the watcher set,
  so a fresh restart cleanly cancels them. The trade rows
  themselves stay `pending` across the cancel; the next restart's
  reconcile picks them up — same logic, same buckets, idempotent.
- **Static-file mount failure.** The `app.mount("/docs", ...)` line
  is wrapped in try/except so a missing directory doesn't break
  startup. Worst case: the in-page link 404s; the GitHub permalink
  fallback still works.
- **Backwards compatibility.** No schema changes, no config-shape
  changes, no API request/response shape changes. Every existing
  parser config keeps working; every existing trade row keeps its
  semantics. Patch A and Patch B are pure additive behaviour
  changes; Patch D is a bugfix in a previously-cached path; Patch
  E is internal hygiene; Patch C is text.
