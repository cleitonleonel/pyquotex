# UI Modernization Phase 3 — Analytics Depth (Part A: Backend)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the backend pieces Phase 3's frontend depends on: a distinct-asset endpoint for the new asset filter pill, parser-streak sub-stats so the frontend can render martingale/streak panels, and a wiring fix so `/stats/v2/funnel` uses the live `Pipeline.recent_decisions` ring instead of returning zeros for `messages_received` and `matched`.

**Architecture:** Three small, independently-testable backend changes inside the existing `backend/src/autotrader/routers/stats_v2.py` (+ helpers in `services/filters.py`). No new tables, no new migrations — all reads from `trade_attempts` and the in-process `Pipeline` instance. Each task ships its own pytest coverage and is committed individually so review is bite-sized.

**Tech Stack:** FastAPI, SQLModel + aiosqlite, Pydantic v2, pytest-asyncio, ruff strict.

---

## File Structure

**Modify:**
- `backend/src/autotrader/routers/stats_v2.py` — add `/assets` endpoint, extend `/breakdown?dim=parser` with `streaks` sub-stat, wire `PipelineDep` into `/funnel`
- `backend/src/autotrader/services/filters.py` — small helper for the parser-streak roll-up so the router stays thin

**Create:**
- `backend/tests/test_stats_v2_phase3.py` — fresh test module so Phase 3 changes are reviewable in isolation

**Untouched on purpose:**
- `backend/src/autotrader/db.py` — no new indices needed; the existing `ix_trade_attempts_received_parser` covers all three new code paths
- `backend/src/autotrader/models/*` — no schema changes

---

## Task 0: Pre-flight (worktree, baseline, commit plan files)

**Files:**
- Create worktree: `/Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/`
- Branch: `claude/ui-modernization-phase-3-analytics-depth`
- Forks from: head of `claude/ui-modernization-phase-2-analytics-core` (Phase 2 HEAD `4f94431`)

- [ ] **Step 1: Create the worktree**

Run from the main checkout `/Users/imranahmedani/Desktop/pyquotex/`:

```bash
git worktree add -b claude/ui-modernization-phase-3-analytics-depth \
  /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3 \
  claude/ui-modernization-phase-2-analytics-core
```

Expected: `Preparing worktree (new branch ...)` then `HEAD is now at 4f94431`.

- [ ] **Step 2: Verify branch + HEAD**

Run from the new worktree `/Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader/`:

```bash
git branch --show-current
git log -1 --oneline
```

Expected: `claude/ui-modernization-phase-3-analytics-depth` and `4f94431 feat(autotrader/frontend): signal funnel panel`.

- [ ] **Step 3: Bring the venv up (greenlet matters)**

```bash
cd backend
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e '.[dev]'
uv pip install greenlet
```

Expected: `Installed N packages` with no errors. `greenlet` is required at runtime by SQLAlchemy's async layer; pip occasionally drops it during fresh installs in worktrees.

- [ ] **Step 4: Baseline test run on Phase 2 HEAD**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db uv run pytest -q
```

Expected: `288 passed`. If this fails, fix the env before adding Phase 3 code — a green baseline is the only way to attribute new failures correctly.

- [ ] **Step 5: Copy the Phase 3 plan files into the worktree and commit**

```bash
cp /Users/imranahmedani/Desktop/pyquotex/autotrader/docs/superpowers/plans/2026-05-09-ui-modernization-phase-3-analytics-depth.md \
   /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader/docs/superpowers/plans/
cp /Users/imranahmedani/Desktop/pyquotex/autotrader/docs/superpowers/plans/2026-05-09-ui-modernization-phase-3-analytics-depth-frontend.md \
   /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader/docs/superpowers/plans/
git add docs/superpowers/plans/2026-05-09-ui-modernization-phase-3-*.md
git commit -m "docs(autotrader): phase 3 analytics-depth implementation plans"
```

Expected: `1 file changed, ... insertions(+)` then a single commit on top of Phase 2.

---

## Task 1: `/stats/v2/assets` endpoint (distinct asset symbols)

**Why this exists:** The frontend needs the universe of asset symbols to populate the asset filter pill (Phase 3 deferral, spec §11). Hardcoding is not viable — operators add new channels with new symbols regularly. Reading `SELECT DISTINCT asset FROM trade_attempts` is the source of truth.

**Files:**
- Modify: `backend/src/autotrader/routers/stats_v2.py` — add new GET handler
- Test: `backend/tests/test_stats_v2_phase3.py` (new)

**Endpoint shape:**

```http
GET /stats/v2/assets?range=30d
→ { "assets": ["AUDCAD", "EURUSD", "GBPJPY", ...] }
```

Sorted alphabetically (case-insensitive). Honours the same `range`/`from`/`to` window as the other v2 endpoints so an operator looking at "last 24h" only sees assets that actually traded recently. No chat/parser filters — this populates a top-level filter, so it must be independent of the other pills.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_stats_v2_phase3.py`:

```python
"""Phase 3 stats/v2 additions: assets endpoint, parser streaks, funnel ring wiring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from autotrader.app import app
from autotrader.models import TradeAttempt


@pytest.mark.asyncio
async def test_assets_endpoint_returns_sorted_distinct_symbols(seeded_session) -> None:
    """Distinct asset symbols within the window, alphabetical."""
    now = datetime.now(UTC)
    seeded_session.add_all(
        [
            TradeAttempt(
                chat_id=-100, parser_config_id=1, asset="EURUSD",
                direction="call", expiration_seconds=60, amount=1.0,
                received_at=now - timedelta(hours=1), status="placed",
            ),
            TradeAttempt(
                chat_id=-100, parser_config_id=1, asset="audcad",
                direction="put", expiration_seconds=60, amount=1.0,
                received_at=now - timedelta(hours=2), status="placed",
            ),
            TradeAttempt(
                chat_id=-100, parser_config_id=1, asset="EURUSD",
                direction="call", expiration_seconds=60, amount=1.0,
                received_at=now - timedelta(hours=3), status="placed",
            ),
        ],
    )
    await seeded_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/stats/v2/assets", params={"range": "24h"})

    assert r.status_code == 200
    body = r.json()
    # Case-insensitive sort, deduped.
    assert body == {"assets": ["audcad", "EURUSD"]}
```

The `seeded_session` fixture already exists in `backend/tests/conftest.py` from Phase 2 — it yields an `AsyncSession` bound to the test DB and clears `trade_attempts` between tests.

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2_phase3.py::test_assets_endpoint_returns_sorted_distinct_symbols -v
```

Expected: `404 NOT FOUND` (route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

In `backend/src/autotrader/routers/stats_v2.py`, add this handler near the other v2 routes (after `/funnel`, before the module ends). Reuse the existing `_resolve_filters_from_query` helper for window resolution but ignore its `chat_ids`/`parser_ids` outputs — assets is window-scoped only:

```python
@router.get("/assets")
async def assets(
    session: SessionDep,
    range: RangeLabel = "24h",
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
) -> dict[str, list[str]]:
    """Distinct asset symbols traded inside the time window.

    Powers the asset filter pill on the frontend. We deliberately do not
    accept chat/parser/direction filters here: the pill must show the
    full universe so an operator can pivot across channels. Sorted
    case-insensitively for stable UI ordering.
    """
    since, until = resolve_range(range, now=utc_now(), custom_from=from_, custom_to=to)
    rows = (
        await session.exec(
            select(TradeAttempt.asset)
            .where(TradeAttempt.received_at >= since)
            .where(TradeAttempt.received_at < until)
            .distinct(),
        )
    ).all()
    return {"assets": sorted({r for r in rows if r}, key=str.lower)}
```

Imports already present at top of file: `select`, `SessionDep`, `Query`, `RangeLabel`, `resolve_range`, `utc_now`, `TradeAttempt`. Verify before adding new imports.

- [ ] **Step 4: Run the test to confirm it passes**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2_phase3.py::test_assets_endpoint_returns_sorted_distinct_symbols -v
```

Expected: `1 passed`.

- [ ] **Step 5: Add an empty-window test**

Append to the test file:

```python
@pytest.mark.asyncio
async def test_assets_endpoint_empty_window_returns_empty_list(seeded_session) -> None:
    """No trades in window → empty assets list, still 200."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/stats/v2/assets", params={"range": "24h"})

    assert r.status_code == 200
    assert r.json() == {"assets": []}
```

Run it:

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2_phase3.py -v
```

Expected: `2 passed`.

- [ ] **Step 6: Lint + commit**

```bash
cd backend
uv run ruff check src/autotrader/routers/stats_v2.py tests/test_stats_v2_phase3.py
git add src/autotrader/routers/stats_v2.py tests/test_stats_v2_phase3.py
git commit -m "feat(autotrader): /stats/v2/assets endpoint for the asset filter pill"
```

Expected: `All checks passed!` and one commit.

---

## Task 2: Parser-streak sub-stats on `/breakdown?dim=parser`

**Why this exists:** The Phase 3 frontend needs streak data (longest losing streak, recovery rate) to render the martingale ladder ROI panel and the streak distribution panel. Computing it client-side would require shipping every trade row — wasteful. Aggregating server-side, per parser, keeps payloads bounded.

**Streak definition:** A *closed losing streak* is a maximal run of consecutive `lost` outcomes within a parser's chronologically-ordered attempts that is followed by at least one non-`lost` outcome (won, void, error, etc.). The trailing run of losses with no recovery is excluded — we only care about *closed* runs because their length is final.

**Output shape per parser row** (added under a new `streaks` key):

```json
{
  "parser_id": 1,
  "trades": 120,
  ...,
  "streaks": {
    "longest_loss": 5,
    "histogram": {"1": 8, "2": 4, "3": 2, "4": 0, "5": 1},
    "recovered_count": 12,
    "recovery_rate": 0.8
  }
}
```

`recovery_rate` is `recovered_count / total_closed_streaks`, where a streak is "recovered" iff the next outcome was `won`. `histogram` keys are stringified ints so JSON survives without numeric-key gymnastics.

**Files:**
- Modify: `backend/src/autotrader/services/filters.py` — add `compute_parser_streaks()` helper
- Modify: `backend/src/autotrader/routers/stats_v2.py` — call helper from `_build_breakdown_row` when `dim == "parser"`
- Test: `backend/tests/test_stats_v2_phase3.py`

- [ ] **Step 1: Write the failing helper test**

Append to `backend/tests/test_stats_v2_phase3.py`:

```python
from autotrader.services.filters import compute_parser_streaks


def test_compute_parser_streaks_basic() -> None:
    """L L L W L L W L (trailing losses excluded — no recovery yet)."""
    outcomes = ["lost", "lost", "lost", "won", "lost", "lost", "won", "lost"]
    result = compute_parser_streaks(outcomes)

    assert result["longest_loss"] == 3
    assert result["histogram"] == {"2": 1, "3": 1}
    assert result["recovered_count"] == 2
    assert result["recovery_rate"] == 1.0


def test_compute_parser_streaks_no_losses() -> None:
    result = compute_parser_streaks(["won", "won", "void"])
    assert result["longest_loss"] == 0
    assert result["histogram"] == {}
    assert result["recovered_count"] == 0
    assert result["recovery_rate"] == 0.0


def test_compute_parser_streaks_single_loss_recovered_by_void() -> None:
    """Recovery only counts if the next outcome is 'won' — voids close
    the streak but don't count as recovery."""
    result = compute_parser_streaks(["lost", "void"])
    assert result["longest_loss"] == 1
    assert result["histogram"] == {"1": 1}
    assert result["recovered_count"] == 0
    assert result["recovery_rate"] == 0.0
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd backend
uv run pytest tests/test_stats_v2_phase3.py::test_compute_parser_streaks_basic -v
```

Expected: `ImportError` — `compute_parser_streaks` doesn't exist yet.

- [ ] **Step 3: Implement the helper**

Add to `backend/src/autotrader/services/filters.py`:

```python
def compute_parser_streaks(outcomes: list[str]) -> dict[str, object]:
    """Aggregate closed losing streaks for one parser's chronological run.

    A *closed* losing streak is a maximal run of consecutive ``lost``
    outcomes followed by at least one non-``lost`` outcome. The trailing
    run with no recovery yet is ignored — its length is not yet final.

    ``recovered`` counts streaks whose next outcome is exactly ``won``;
    voids/errors close a streak but do not count as recovery.
    """
    longest = 0
    histogram: dict[str, int] = {}
    closed_total = 0
    recovered = 0

    run = 0
    for outcome in outcomes:
        if outcome == "lost":
            run += 1
            continue
        if run > 0:
            histogram[str(run)] = histogram.get(str(run), 0) + 1
            longest = max(longest, run)
            closed_total += 1
            if outcome == "won":
                recovered += 1
            run = 0

    rate = recovered / closed_total if closed_total else 0.0
    return {
        "longest_loss": longest,
        "histogram": histogram,
        "recovered_count": recovered,
        "recovery_rate": rate,
    }
```

- [ ] **Step 4: Run helper tests to confirm they pass**

```bash
cd backend
uv run pytest tests/test_stats_v2_phase3.py -k streaks -v
```

Expected: `3 passed`.

- [ ] **Step 5: Write the failing endpoint test**

Append to the test file:

```python
@pytest.mark.asyncio
async def test_breakdown_parser_includes_streaks(seeded_session) -> None:
    """dim=parser response carries per-parser streak roll-up."""
    now = datetime.now(UTC)
    # Three losses then a win (one closed streak of length 3, recovered).
    for i, status in enumerate(["lost", "lost", "lost", "won"]):
        seeded_session.add(
            TradeAttempt(
                chat_id=-100, parser_config_id=1, asset="EURUSD",
                direction="call", expiration_seconds=60, amount=1.0,
                received_at=now - timedelta(minutes=10 - i),
                status=status,
            ),
        )
    await seeded_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/stats/v2/breakdown", params={"dim": "parser", "range": "24h"})

    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["streaks"]["longest_loss"] == 3
    assert rows[0]["streaks"]["recovered_count"] == 1
    assert rows[0]["streaks"]["recovery_rate"] == 1.0
```

- [ ] **Step 6: Run it to confirm it fails**

```bash
cd backend
uv run pytest tests/test_stats_v2_phase3.py::test_breakdown_parser_includes_streaks -v
```

Expected: `KeyError: 'streaks'` (or assertion failure on missing key).

- [ ] **Step 7: Wire the helper into the router**

In `backend/src/autotrader/routers/stats_v2.py`, locate `_build_breakdown_row` and add the streaks block when the dimension is parser. Sketch:

```python
def _build_breakdown_row(
    *,
    dim: str,
    key: int | str,
    attempts: list[TradeAttempt],
) -> dict[str, object]:
    row: dict[str, object] = {
        # ... existing fields unchanged ...
    }
    if dim == "parser":
        # attempts arrive grouped by parser; sort chronologically so
        # streaks are computed in temporal order, not insertion order.
        ordered = sorted(attempts, key=lambda a: a.received_at)
        row["streaks"] = compute_parser_streaks([a.status for a in ordered])
    return row
```

Add the import at the top of the file alongside the other `services.filters` imports:

```python
from autotrader.services.filters import compute_parser_streaks
```

- [ ] **Step 8: Run all Phase 3 tests**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2_phase3.py -v
```

Expected: all green.

- [ ] **Step 9: Run the full Phase 2 suite to catch regressions**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2.py tests/test_filters.py -q
```

Expected: previous Phase 2 totals still pass (was 32 in those two files combined; should remain 32).

- [ ] **Step 10: Lint + commit**

```bash
cd backend
uv run ruff check src/autotrader/services/filters.py src/autotrader/routers/stats_v2.py tests/test_stats_v2_phase3.py
git add src/autotrader/services/filters.py src/autotrader/routers/stats_v2.py tests/test_stats_v2_phase3.py
git commit -m "feat(autotrader): parser streaks on /stats/v2/breakdown for the streak panel"
```

---

## Task 3: Wire `Pipeline.recent_decisions` into `/stats/v2/funnel`

**Why this exists:** Today `/stats/v2/funnel` reports `messages_received: 0` and `matched: 0` because it only counts `trade_attempts` rows — but those are inserted *after* a parser matches and risk gates pass. The earlier funnel stages live in `Pipeline._recent_decisions` (the deque the pipeline router already exposes). Joining the two gives us a true top-of-funnel count instead of zeros.

**Caveat (call out in code):** The ring is bounded (`_DECISION_RING_SIZE`, currently 200) and lives in process memory. So `messages_received` from the ring is "last N decisions" not "last 24h of decisions." We document this in the response (a `messages_received_window` field) so the frontend can label the stage honestly rather than implying it scales with `range`.

**Files:**
- Modify: `backend/src/autotrader/routers/stats_v2.py`
- Test: `backend/tests/test_stats_v2_phase3.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_stats_v2_phase3.py`:

```python
@pytest.mark.asyncio
async def test_funnel_uses_pipeline_ring_for_top_stages(seeded_session) -> None:
    """messages_received and matched count Pipeline.recent_decisions, not just trade_attempts."""
    from autotrader.app import app as live_app
    from autotrader.dependencies import get_pipeline

    class _StubPipeline:
        recent_decisions = [
            {"chat_id": -100, "outcome": "no_match", "ts": "2026-05-09T00:00:00Z"},
            {"chat_id": -100, "outcome": "matched", "ts": "2026-05-09T00:01:00Z"},
            {"chat_id": -100, "outcome": "matched", "ts": "2026-05-09T00:02:00Z"},
        ]

    live_app.dependency_overrides[get_pipeline] = lambda: _StubPipeline()
    try:
        async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://t") as ac:
            r = await ac.get("/stats/v2/funnel", params={"range": "24h"})
    finally:
        live_app.dependency_overrides.pop(get_pipeline, None)

    body = r.json()
    assert r.status_code == 200
    assert body["messages_received"] == 3
    assert body["matched"] == 2
    # Honesty about ring semantics — frontend uses this to label the stage.
    assert body["messages_received_window"] == "ring"
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2_phase3.py::test_funnel_uses_pipeline_ring_for_top_stages -v
```

Expected: assertion failure — `messages_received` is currently 0 (or KeyError on `messages_received_window`).

- [ ] **Step 3: Add `PipelineDep` to the funnel handler**

In `backend/src/autotrader/routers/stats_v2.py`, add the import (top of file, with the other dependency imports):

```python
from autotrader.dependencies import PipelineDep, SessionDep
```

Then update the funnel handler signature and body. Locate the existing `async def funnel(...)` handler and modify:

```python
@router.get("/funnel")
async def funnel(
    session: SessionDep,
    pipeline: PipelineDep,
    range: RangeLabel = "24h",
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
    chats: str | None = None,
    parsers: str | None = None,
) -> dict[str, object]:
    # ... existing window/filter resolution unchanged ...

    # Snapshot the in-process decision ring once. The ring is bounded
    # (Pipeline._DECISION_RING_SIZE) so this is "last N", not "last
    # range." We surface that distinction via messages_received_window
    # so the frontend can label the stage truthfully.
    ring = list(pipeline.recent_decisions)
    messages_received = len(ring)
    matched_via_pipeline = sum(
        1 for d in ring if d.get("outcome") == "matched"
    )

    # ... existing per-stage counts from trade_attempts ...

    return {
        "messages_received": messages_received,
        "messages_received_window": "ring",
        "matched": matched_via_pipeline,
        # ... existing downstream stages unchanged ...
    }
```

Read the existing handler first — keep all current stage counts (`risk_passed`, `placed`, `won`, etc.) intact. Only the top two stages and the new `messages_received_window` field change.

- [ ] **Step 4: Run the new test to confirm it passes**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2_phase3.py::test_funnel_uses_pipeline_ring_for_top_stages -v
```

Expected: pass.

- [ ] **Step 5: Re-run the existing funnel tests (regression guard)**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest tests/test_stats_v2.py -k funnel -v
```

Expected: existing funnel tests still pass — they assert downstream stages, which we didn't touch. If a test previously asserted `messages_received == 0`, update it (those expectations were a workaround for the bug we're fixing).

- [ ] **Step 6: Full backend test sweep**

```bash
cd backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db \
  uv run pytest -q
```

Expected: 288 (Phase 2 baseline) + new Phase 3 tests, all green. If `test_admin_bot.py` flakes due to its `autotrader.db` reload trick, that's pre-existing and not caused by this task.

- [ ] **Step 7: Lint + commit**

```bash
cd backend
uv run ruff check src/autotrader/routers/stats_v2.py tests/test_stats_v2_phase3.py
git add src/autotrader/routers/stats_v2.py tests/test_stats_v2_phase3.py
git commit -m "fix(autotrader): wire Pipeline ring into /stats/v2/funnel top stages"
```

---

## Self-Review Checklist (run before handing off to executor)

1. **Spec coverage:** Phase 3 deferrals from the original spec covered by Part A — asset filter pill backend (Task 1), streak distribution + martingale backend (Task 2), funnel ring wiring (Task 3). Frontend deferrals covered in Part B.
2. **Placeholders:** None — every step has explicit code, file paths, and expected output.
3. **Type consistency:** `compute_parser_streaks` returns `dict[str, object]` everywhere. Histogram keys are stringified ints in both helper and test.
4. **No schema changes:** Confirmed — every read uses existing columns and the existing `ix_trade_attempts_received_parser` composite index.
5. **No breaking changes to Phase 2:** Funnel response gains a field (`messages_received_window`) and the meaning of `messages_received`/`matched` flips from "always zero" to "last N from ring." Frontend update lives in Part B.

---

## Handoff

Part B (frontend) is in `2026-05-09-ui-modernization-phase-3-analytics-depth-frontend.md` and depends on Tasks 1–3 here being merged into the Phase 3 branch first (no PR needed between A and B — same branch).
