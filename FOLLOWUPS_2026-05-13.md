# Audit 2026-05-13 — Follow-ups (Phase 5)

Companion to `AUDIT_2026-05-13.md`. Each entry below is an item the
audit identified but the in-session phased work intentionally
**did not ship** — usually because the change is larger than the
audit framed, requires owner judgement, or depends on real-world
data that doesn't exist yet. Each carries a recommended trigger
condition for promotion to a real PR.

The phased work (Phase ‑1 … Phase 4) closed every Critical, High,
and verified Medium finding in the audit. What follows are the
items where the right answer is "later, with more context".

---

## A. Phase 5 originals (audit doc §3 listed these as "file as issues")

### A1. Decimal money columns
**Where.** `models/trade_attempt.py:stake/profit`,
`models/martingale_state.py:last_stake/last_payout`,
`models/parser_config.py:default_stake`, plus every `float` money
field in `services/risk_gate.py`.

**Why.** Float arithmetic drift on long winning streaks (M5 fix
clamps `last_payout` to 2 decimals but doesn't fix the type). The
audit's pragmatic-now / proper-later split has us shipping
`round(..., 2)` today; the real fix is `Numeric(12, 2)` columns
and `decimal.Decimal` throughout.

**Trigger to promote.** First report of a stake ladder calculation
that "should be `$10` but came back `$9.99` / `$10.01`" in
production. (The Phase 4 round-to-2 fix should prevent this; the
trigger is the bug recurring despite that.)

**Rough scope.** ~150 LOC across 6 files + a single ALTER‑TABLE
migration that copies float → numeric. Test impact is wide (every
test that asserts on money math may need `Decimal(...) ==` vs `==`
tolerance). 3–4 hour PR, recommended after Alembic adoption (A2)
to avoid another in‑place migration block.

### A2. Alembic adoption
**Where.** Replace `db._migrate_in_place` with proper Alembic
revisions.

**Why.** The in-place migration system has served well (commits
b338c56 through e2d2224 are all ALTER TABLE additions handled
correctly), but each new column adds friction. Alembic gives
us reversible migrations, version-stamping, and the standard tooling
contributors expect.

**Trigger to promote.** The next column change that's anything
beyond "ADD COLUMN NULL". Specifically: anything renaming, dropping,
type-changing, or adding a non-nullable column without server_default.

**Rough scope.** ~1 day. Steps: install Alembic, generate baseline
revision from current schema, port each `_migrate_in_place` block
to a real revision (chronological — the existing comments document
the order), wire `alembic upgrade head` into the lifespan startup
in place of `_migrate_in_place`.

### A3. Event-bus persistence
**Where.** `services/event_bus.py` — currently in-memory only.

**Why.** Every audit (this one included) would have been trivial if
we could replay the last N hours of `trade.upserted` /
`risk.rejected` / `system.error` / `pipeline.decision` events. Today
debugging requires correlating logs.

**Trigger to promote.** Either (a) the team starts running multiple
audits per quarter (>2 needed), or (b) a P0 incident where the
operator can't reconstruct what happened.

**Rough scope.** ~200 LOC. Add an SQLite append-only `events` table
+ a write-behind queue (don't block publishers). 24‑hour retention
default, env-tuneable. ½‑day PR.

### A4. Frontend retry/backoff in lib/api.ts
**Where.** `autotrader/frontend/lib/api.ts`.

**Why.** Audit §1 M-tier noted that the dashboard surfaces transient
5xx immediately to the user. A small backoff would smooth out
proxy hiccups and lifespan-restart windows.

**Trigger to promote.** Either a user complaint about "the dashboard
keeps flashing errors during deploys", or anyone starting unrelated
frontend work in `lib/api.ts`.

**Rough scope.** ~30 LOC. Exponential backoff on 5xx and network
errors, max 3 retries, 200ms base. Existing SWR usage handles
de‑dup, so the retry just lives inside the `fetch` wrapper.

---

## B. Phase 4 deferrals (audit §3 Phase 4 listed these as ship-now;
they ended up being larger than the audit framed)

### B1. L3 — wire `/auth/me` into the UI header
**Why deferred.** The audit said "wire to UI or remove" — removing
is a breaking change to a public route, wiring needs a header
component decision (username chip placement, click‑to-open profile
menu, etc.). The route currently has no consumer; leaving it
in-place is the conservative call.

**Trigger.** Either an explicit ask for a "logged in as X" chip in
the dashboard topbar, or the first call from an external client
(monitoring, CLI). Either way, the route is ready.

**Rough scope.** Removing: 10 LOC. Wiring: ~50 LOC + a new
`UserChip` component. Either is a half-hour PR; the decision is
the long pole.

### B2. L5 — externalise `_TICKER_ALIASES`
**Where.** `services/executor.py:120`.

**Why deferred.** Two real implementations:
- *Per-parser overrides via `ParserConfig.asset_aliases_json`* —
  this column already exists for asset names; widening it to also
  carry ticker aliases is a small schema-comment change but the
  parser-config UI in the dashboard needs a row for it.
- *Global config file* — `~/.config/autotrader/ticker_aliases.json`
  loaded at startup, watched for changes via inotify. New code path,
  needs operator docs.

Both are real PRs. The audit's "cleanup" framing was wrong.

**Trigger.** Next operator request for a new alias that requires
a redeploy ("the broker just renamed GOLD → XAUUSD and I can't
push it without a release"). The dashboard-row approach probably
wins because it gives operators self-service without infrastructure.

**Rough scope.** Per-parser route: ~100 LOC backend + ~60 LOC
frontend + a migration. Half-day PR.

### B3. M6 — distinct broker auth error codes
**Where.** `services/quotex_manager.py:_do_connect:error` —
currently sets `last_error = f"{type(exc).__name__}: {exc}"`.

**Why deferred.** The fix is a string-matching dictionary mapping
broker error strings to operator-facing codes
(`invalid_credentials`, `captcha_required`, `account_locked`,
`temporary_block`, …). We don't have a sample of the strings
yet — building the dictionary blind invites false confidence ("we
handled it" when the next variant slips through).

**Trigger.** Two-week observation window after Phase 1 ships:
collect every distinct `broker.connect.error` log string, build
the dictionary from real data, then ship.

**Rough scope.** Once the data exists: 1 hour. Without data: do
not ship.

---

## C. Pyrofork test-seam baseline (the 31 pre-existing failures)

The full pytest suite enters this work with 31 failing tests, all
caused by the pyrofork upgrade not being fully reconciled with
the test seams:

* **25 in `tests/test_admin_bot.py`** — `FakePyrogramBot` setup
  drift. Phase ‑1 unblocked the `chat.username` / `from_user.username`
  attribute check, but a deeper issue remains: many tests use
  `AsyncSessionLocal` directly without `init_db`, which used to
  work because pyrogram's old dispatch path didn't reach the DB
  query. Pyrofork's dispatch now does, so the missing table
  surfaces.
* **5 in `tests/test_e2e_elite_scenario.py`** — the e2e flow
  expects more assets in the broker catalog than `FakeQuotex`
  provides; the recently shipped asset-pre-flight bounces with
  `executor.asset.unrecognized`. Test fixture issue, not a
  production bug.
* **1 in `tests/test_telegram.py::test_watch_then_unwatch`** —
  `FakeTelegramClient` is missing `add_handler` and `resolve_peer`
  stubs; pyrofork now invokes both as part of the live-channel
  subscription path.

**Why deferred.** This is **not** in the audit. It's pre-existing
infrastructure debt from the pyrofork upgrade. Fixing it cleanly
needs: (a) an autouse `init_db` fixture for tests that use the
DB without going through `TestClient(app)`, (b) `add_handler` and
`resolve_peer` no-op stubs on `FakeTelegramClient`, and (c) a
larger `FakeQuotex` asset catalog or test-config knob to seed it.

**Trigger.** Whenever the team blocks on the red baseline for a
real PR. The phased audit work above was careful to leave the
baseline failures unchanged so the cause of every red test is
unambiguous; the moment anyone needs to land code that touches
the same tests, this is the unblock.

**Rough scope.** Half-day. The shape of each fix is well-understood
from the audit work.

---

## D. Additional medium-severity items the audit flagged but the
phased work did not address

### D1. M2 — `prep_trigger` silent prep expiry
**Where.** `services/parsers/prep_trigger.py`.

**Why deferred.** Confirmed in the audit's "plausible, not
verified" tier. Fix is a `pipeline.decision` row emitted on
prep expiry — small, but needs to thread the parser → decision
ring callback (the parser doesn't currently have a reference).

**Trigger.** Owner request OR first reported case of a two-phase
signal silently dropping.

**Rough scope.** ~50 LOC. Half-hour PR.

---

## E. Process notes for whoever picks this up next

1. **Branch off `master`, not `fix/audit-2026-05-13`.** The audit
   work itself is already in flight; each follow-up should be its
   own focused PR.

2. **Each item names the audit ID** (e.g. "fix: M6 broker auth
   error codes (audit 2026-05-13)") — keeps the lineage searchable
   in `git log`.

3. **The Phase 0 instrumentation is your friend.** Before shipping
   any of B1–B3 / D1, check the Phase 0 logs in staging for the
   conditions they target. If a finding doesn't actually fire in
   production, deprioritise it.

4. **Don't fix the C-bucket (pyrofork seams) speculatively.** The
   baseline being stable-red is more useful for the audit's
   verification than partial-green would be. Promote only when
   blocked.

---

## F. Tier-0 spec drift (audit 2026-05-14) — do NOT copy the spec pseudocode

The Tier-0 production-readiness work
(`docs/superpowers/specs/2026-05-14-production-ready-tier0-design.md`)
shipped with two places where the spec's illustrative pseudocode
diverged from what was actually implemented (the implementation is
correct; the spec was not updated — it is a frozen design artifact).
A maintainer copying the spec pseudocode verbatim would reintroduce a
real defect. Flagged by the final branch-level review; recorded here
so the next person isn't misled.

### F1. Spec §3.4 / §3.2 — `is_authenticated` is a METHOD, not a bool
**Spec pseudocode says.** `not getattr(client.api, "is_authenticated", False)`
(and a `is_authenticated` field on the rejection probe in §3.2).

**Reality.** pyquotex's `client.api.is_authenticated` is a bound
*method* — always truthy — so that check never fires. The shipped
code (`quotex_manager.py` `assert_live` + the rejection probe) uses
`client.api.state.auth_status == AuthStatus.AUTHENTICATED` instead and
documents the trap inline. **Use `state.auth_status`; ignore the spec's
`is_authenticated`.** (Caught twice during implementation — Tasks 3
and 5 — because the spec carried the wrong path.)

### F2. Spec §3.5 — `wait_for_pendings` is NOT a `list_pending` poll
**Spec pseudocode says.** Poll `list_pending()` every 2s until empty
or timeout.

**Reality.** That design conflated live trades (real broker outcome
coming — must wait) with `reconcile_pending` give-up timers (no
outcome — must not wait) and caused a 300s shutdown hang. Superseded
(commit `36020a9`) by an event-driven `asyncio.wait` over a dedicated
`TradeExecutor._result_watchers` snapshot. **Read the
`wait_for_pendings` docstring in `executor.py`, not spec §3.5.**

**Trigger to promote either.** Only if the spec doc is ever revived as
a live design reference; otherwise these are inert (the code is
correct and self-documenting). Cheap fix if wanted: a one-line
"superseded — see &lt;file&gt;" annotation at §3.4/§3.5.
