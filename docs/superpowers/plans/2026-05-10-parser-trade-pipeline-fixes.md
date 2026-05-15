# Parser & Trade-Pipeline Reliability Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three operator-visible reliability issues in the autotrader pipeline (newly-watched chats not delivering signals, all pending trades expiring on every restart, no canonical parser-writing doc) without changing parser features, schema, or status enum.

**Architecture:** Five additive patches across `services/telegram_manager.py`, `routers/telegram.py`, `services/executor.py`, `services/pipeline.py`, plus a new `autotrader/docs/PARSERS.md`. No schema migration. No new status literal. No API request/response shape changes. Each patch is independently committable.

**Tech Stack:** Python 3.13 + FastAPI + SQLModel/aiosqlite + Pyrogram + structlog + pytest-asyncio (backend); Next.js 15 + React 19 + TanStack Query (frontend); `uv` for Python, `bun` for JS.

**Spec:** `docs/superpowers/specs/2026-05-10-parser-trade-pipeline-fixes-design.md`

---

## File map

**Created:**
- `autotrader/docs/PARSERS.md` — parser-writing reference + troubleshooting checklist

**Modified (backend):**
- `autotrader/backend/src/autotrader/services/telegram_manager.py` — adds `subscribe_chat`
- `autotrader/backend/src/autotrader/routers/telegram.py` — `watch_endpoint` awaits subscribe; `unwatch_endpoint` invalidates parser cache
- `autotrader/backend/src/autotrader/services/executor.py` — `reconcile_pending` rewrite + auto-recovery refetch
- `autotrader/backend/src/autotrader/services/pipeline.py` — `invalidate_for_chat`
- `autotrader/backend/src/autotrader/main.py` — `/docs` static-file mount (best-effort)
- `autotrader/backend/tests/test_telegram.py` — subscribe + unwatch invalidation tests
- `autotrader/backend/tests/test_startup_recovery.py` — reconcile bucket tests
- `autotrader/backend/tests/test_pipeline.py` — `invalidate_for_chat` test
- `autotrader/backend/tests/test_risk.py` — auto-recovery-on-disabled-parser test

**Modified (frontend):**
- `autotrader/frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx` — adds 📖 Parser guide link in header
- `autotrader/frontend/app/dashboard/parsers/[chat_id]/page.tsx` — invalidate `["pipeline","status"]` on watch (if a watch button exists here)
- `autotrader/frontend/app/dashboard/telegram/page.tsx` — invalidate `["pipeline","status"]` after the watch mutation succeeds

**Modified (config):**
- `autotrader/README.md` — link to `docs/PARSERS.md`

---

## Task 1: Add `subscribe_chat` to TelegramManager

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/telegram_manager.py` (add method between `_prime_peer_cache` and `_handle_incoming`)
- Test: `autotrader/backend/tests/test_telegram.py` (add at end of file)

- [ ] **Step 1.1: Write the failing test**

Append to `autotrader/backend/tests/test_telegram.py`:

```python
def test_subscribe_chat_calls_get_chat_history(client: TestClient) -> None:
    """``subscribe_chat`` should run a single ``get_chat_history``
    touch, mirroring what ``_prime_peer_cache`` does per chat. This
    is the call that registers the channel with the live update
    stream so ``UpdateNewChannelMessage`` events stop being silently
    dropped.
    """
    # Log into Telegram so the manager has a live client.
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    # Seed history for chat -1003 so we can prove subscribe_chat
    # actually walks it (and isn't just a no-op).
    FakeTelegramClient.history[-1003] = [
        _FakeMessage(901, text="probe", sender_id=200),
    ]

    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.telegram_manager
    initial_subscribed = manager.subscribed_chat_count

    asyncio.new_event_loop().run_until_complete(
        manager.subscribe_chat(-1003)
    )

    # The gauge ticks up by one and the fake client's get_chat_history
    # was actually called for the new chat.
    assert manager.subscribed_chat_count == initial_subscribed + 1


def test_subscribe_chat_idempotent_when_logged_out(client: TestClient) -> None:
    """``subscribe_chat`` returns silently when the manager isn't
    logged in. Avoids forcing the watch endpoint to know about the
    login state.
    """
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.telegram_manager
    # Not logged in — the call is a no-op, not a raise.
    asyncio.new_event_loop().run_until_complete(
        manager.subscribe_chat(-1004)
    )
    assert manager.subscribed_chat_count == 0


def test_subscribe_chat_raises_on_pyrogram_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Pyrogram exception bubbles as ``TelegramManagerError`` so
    the route can map it to 502 — caller knows the watch row was
    saved but the subscribe step failed."""
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.services.telegram_manager import TelegramManagerError  # noqa: PLC0415

    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    manager = app.state.telegram_manager
    client_obj = manager._client
    assert client_obj is not None

    async def _broken_history(*_: object, **__: object) -> object:  # noqa: ANN401
        raise RuntimeError("flood-wait")
        yield  # pragma: no cover  (unreachable; satisfies async-gen typing)

    monkeypatch.setattr(client_obj, "get_chat_history", _broken_history)

    with pytest.raises(TelegramManagerError):
        asyncio.new_event_loop().run_until_complete(
            manager.subscribe_chat(-1005)
        )
```

- [ ] **Step 1.2: Run the tests to verify they fail**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py::test_subscribe_chat_calls_get_chat_history -v
```

Expected: FAIL with `AttributeError: 'TelegramManager' object has no attribute 'subscribe_chat'`.

- [ ] **Step 1.3: Implement `subscribe_chat`**

In `autotrader/backend/src/autotrader/services/telegram_manager.py`, add this method right after the `_prime_peer_cache` method (insert before `_handle_incoming`):

```python
    async def subscribe_chat(self, chat_id: int) -> None:
        """Resolve + subscribe a single chat with the live update stream.

        Mirrors what ``_prime_peer_cache`` does per chat at login: a
        ``get_chat_history(limit=1)`` round-trip forces Pyrogram to
        resolve the peer and run the ``getDifference`` handshake that
        registers ``UpdateNewChannelMessage`` events for this channel.

        Without this, a chat added via ``/telegram/watch`` after login
        sits silently in the database — the row is correct but the
        live client never delivers messages for it until the next
        process restart re-runs ``_prime_peer_cache``.

        Idempotent. Returns silently when not logged in (the watch
        endpoint may be saving a draft row before the operator finishes
        the Telegram login).
        """
        if not self.logged_in or self._client is None:
            return
        try:
            async for _ in self._client.get_chat_history(chat_id, limit=1):
                break
        except Exception as exc:  # pragma: no cover - Pyrogram surfaces vary
            self._emit_system_error(
                kind="subscribe_failed",
                detail=f"chat_id={chat_id}: {type(exc).__name__}: {exc}",
            )
            log.warning(
                "telegram.subscribe.failed",
                chat_id=chat_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise TelegramManagerError(
                f"subscribe failed for chat {chat_id}: {exc}",
            ) from exc

        self._subscribed_chat_count += 1
        log.info("telegram.subscribe.ok", chat_id=chat_id)
```

- [ ] **Step 1.4: Run the new tests**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py::test_subscribe_chat_calls_get_chat_history tests/test_telegram.py::test_subscribe_chat_idempotent_when_logged_out tests/test_telegram.py::test_subscribe_chat_raises_on_pyrogram_failure -v
```

Expected: 3 PASS.

- [ ] **Step 1.5: Run the full Telegram test module**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py -v
```

Expected: every test PASS, including the existing 20+ in this module.

- [ ] **Step 1.6: Commit**

```bash
git add autotrader/backend/src/autotrader/services/telegram_manager.py autotrader/backend/tests/test_telegram.py
git commit -m "feat(autotrader/telegram): TelegramManager.subscribe_chat

Forces Pyrogram to resolve+subscribe a single chat by running the
same get_chat_history(limit=1) touch _prime_peer_cache uses per
chat. Wires up the live UpdateNewChannelMessage stream for chats
added after login.

Idempotent; no-op when not logged in; surfaces Pyrogram failures
as TelegramManagerError + system.error event.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `/telegram/watch` awaits `subscribe_chat`

**Files:**
- Modify: `autotrader/backend/src/autotrader/routers/telegram.py:266-279`
- Test: `autotrader/backend/tests/test_telegram.py`

- [ ] **Step 2.1: Write the failing test**

Append to `autotrader/backend/tests/test_telegram.py`:

```python
def test_watch_subscribes_chat_when_enabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /telegram/watch with enabled=True must call
    TelegramManager.subscribe_chat exactly once with the new chat_id.
    This is the regression: chats added after login were never
    subscribed, so UpdateNewChannelMessage events for them were
    silently dropped until the next API restart re-primed the cache.
    """
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    from autotrader.main import app  # noqa: PLC0415

    calls: list[int] = []

    async def _spy(self: object, chat_id: int) -> None:  # noqa: ARG001
        calls.append(chat_id)

    monkeypatch.setattr(
        type(app.state.telegram_manager),
        "subscribe_chat",
        _spy,
    )

    r = client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": -1009,
            "title": "Elite",
            "chat_type": "channel",
            "username": "elite",
            "enabled": True,
        },
    )
    assert r.status_code == 200, r.text
    assert calls == [-1009], (
        "watch endpoint must call subscribe_chat with the new chat_id; "
        f"got calls={calls}"
    )


def test_watch_disabled_does_not_subscribe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled watch row is a draft — don't pay the Pyrogram
    round-trip. The operator can flip enabled=True later, which
    reissues the watch POST."""
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    from autotrader.main import app  # noqa: PLC0415

    calls: list[int] = []

    async def _spy(self: object, chat_id: int) -> None:  # noqa: ARG001
        calls.append(chat_id)

    monkeypatch.setattr(
        type(app.state.telegram_manager),
        "subscribe_chat",
        _spy,
    )

    r = client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": -1010,
            "title": "Draft",
            "chat_type": "channel",
            "username": None,
            "enabled": False,
        },
    )
    assert r.status_code == 200, r.text
    assert calls == [], "subscribe_chat must not run for enabled=False"


def test_watch_returns_502_when_subscribe_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When subscribe_chat raises, the watch row IS still saved (so
    a retry is just another POST), but the route returns 502 with
    the error detail surfaced."""
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    from autotrader.main import app  # noqa: PLC0415
    from autotrader.services.telegram_manager import TelegramManagerError  # noqa: PLC0415

    async def _boom(self: object, chat_id: int) -> None:  # noqa: ARG001
        raise TelegramManagerError(f"flood-wait on {chat_id}")

    monkeypatch.setattr(
        type(app.state.telegram_manager),
        "subscribe_chat",
        _boom,
    )

    r = client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": -1011,
            "title": "Floody",
            "chat_type": "channel",
            "username": None,
            "enabled": True,
        },
    )
    assert r.status_code == 502, r.text
    assert "flood-wait" in r.text

    # The row IS still persisted — retrying the watch is just another POST.
    r = client.get("/telegram/watched", headers=headers)
    assert any(d["chat_id"] == -1011 for d in r.json())
```

- [ ] **Step 2.2: Run the tests to verify they fail**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py::test_watch_subscribes_chat_when_enabled -v
```

Expected: FAIL — calls list is empty because the route doesn't await `subscribe_chat` yet.

- [ ] **Step 2.3: Update `watch_endpoint` to await `subscribe_chat`**

Replace `autotrader/backend/src/autotrader/routers/telegram.py:266-279` with:

```python
@router.post("/watch", response_model=OkResponse)
async def watch_endpoint(
    body: WatchRequest,
    session: SessionDep,
    manager: TelegramDep,
) -> OkResponse:
    await upsert_watch(
        session,
        chat_id=body.chat_id,
        title=body.title,
        chat_type=body.chat_type,
        username=body.username,
        enabled=body.enabled,
    )
    # Force the live Pyrogram client to subscribe the new chat's
    # update stream. Without this, ``UpdateNewChannelMessage`` events
    # for chats added after login are silently dropped until the next
    # process restart re-runs ``_prime_peer_cache`` — i.e. trades from
    # the new channel never fire even though the row is correct.
    #
    # Subscribing AFTER ``upsert_watch`` so when Pyrogram's catch-up
    # ``getDifference`` runs the WatchedChannel row is already there
    # for ``Pipeline._dispatch_locked`` to find. We only subscribe
    # when ``enabled=True`` so an operator can save a draft disabled
    # watch row without paying the MTProto round-trip.
    if body.enabled:
        try:
            await manager.subscribe_chat(body.chat_id)
        except TelegramManagerError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"chat saved but subscribe failed: {exc}",
            ) from exc
    return OkResponse()
```

- [ ] **Step 2.4: Run the new tests**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py::test_watch_subscribes_chat_when_enabled tests/test_telegram.py::test_watch_disabled_does_not_subscribe tests/test_telegram.py::test_watch_returns_502_when_subscribe_fails -v
```

Expected: 3 PASS.

- [ ] **Step 2.5: Run the full Telegram suite**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py -v
```

Expected: every test PASS, including the existing `test_watch_then_unwatch` (which short-circuits because that test never logs in, leaving `manager.logged_in == False` and `subscribe_chat` a no-op).

- [ ] **Step 2.6: Commit**

```bash
git add autotrader/backend/src/autotrader/routers/telegram.py autotrader/backend/tests/test_telegram.py
git commit -m "fix(autotrader/telegram): /telegram/watch subscribes new chat live

Without this, Pyrogram's update dispatcher silently drops
UpdateNewChannelMessage events for chats added after login — the
WatchedChannel row is correct but Pipeline.dispatch never sees the
messages until the API is restarted.

The watch is awaited only when enabled=True (draft rows skip the
round-trip). Failures bubble as 502 with the SQLite row still
saved so a retry is just another POST.

Fixes the Elite-channel regression where DreamVIP and the test
channel (added at login time) worked but Elite never delivered.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `Pipeline.invalidate_for_chat` + unwatch wiring

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/pipeline.py` (next to `invalidate` / `invalidate_all`)
- Modify: `autotrader/backend/src/autotrader/routers/telegram.py:282-288`
- Test: `autotrader/backend/tests/test_pipeline.py`
- Test: `autotrader/backend/tests/test_telegram.py`

- [ ] **Step 3.1: Write the failing pipeline test**

Append to `autotrader/backend/tests/test_pipeline.py`:

```python
def test_pipeline_invalidate_for_chat_drops_only_matching_caches() -> None:
    """``invalidate_for_chat(X)`` removes every cached parser whose
    ``config_row.chat_id == X``, leaving caches for other chats
    intact. Called from the unwatch endpoint so cached parsers for
    a deleted watch row don't sit in memory until a signature drift
    rebuilds them.
    """
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.services.pipeline import Pipeline, _CachedParser  # noqa: PLC0415
    from autotrader.services.parsers import build_parser  # noqa: PLC0415

    class _StubManager:
        assets: tuple[str, ...] = ()

    class _StubExecutor:
        async def submit(self, **kwargs: object) -> None:  # noqa: ARG002
            return None

    pipe = Pipeline(manager=_StubManager(), executor=_StubExecutor())

    def _seed(cfg_id: int, chat_id: int) -> None:
        cfg = ParserConfig(
            id=cfg_id,
            chat_id=chat_id,
            parser_type="template",
            parser_config_json='{"template": "{DIRECTION} {ASSET} {DURATION}"}',
        )
        parser = build_parser(
            parser_type="template",
            parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
        )
        pipe._parsers[cfg_id] = _CachedParser(
            config_revision=("template", "{}", "0", "{}", "60", "0"),
            parser_type="template",
            parser=parser,
            aggregator=None,
            config_row=cfg,
        )

    _seed(cfg_id=10, chat_id=-1001)
    _seed(cfg_id=11, chat_id=-1001)
    _seed(cfg_id=20, chat_id=-1002)

    pipe.invalidate_for_chat(-1001)

    assert 10 not in pipe._parsers
    assert 11 not in pipe._parsers
    assert 20 in pipe._parsers, (
        "invalidate_for_chat must only drop caches matching the chat_id; "
        "parsers on other chats stay cached"
    )
```

- [ ] **Step 3.2: Run the test to verify it fails**

```bash
cd autotrader/backend
uv run pytest tests/test_pipeline.py::test_pipeline_invalidate_for_chat_drops_only_matching_caches -v
```

Expected: FAIL — `Pipeline` has no `invalidate_for_chat` attribute.

- [ ] **Step 3.3: Implement `invalidate_for_chat`**

In `autotrader/backend/src/autotrader/services/pipeline.py`, add this method right after the existing `invalidate_all` method (around line 149-150):

```python
    def invalidate_for_chat(self, chat_id: int) -> None:
        """Drop every cached parser whose config row's chat_id matches.

        Called by the unwatch endpoint so cached parsers belonging to
        a no-longer-watched chat don't occupy memory until the next
        signature-drift rebuild. Dispatch already filters out
        unwatched chats via ``WatchedChannel.enabled``, so this is
        memory-only hygiene; behaviour is unchanged either way.
        """
        for cfg_id in [
            cfg_id
            for cfg_id, cached in self._parsers.items()
            if cached.config_row.chat_id == chat_id
        ]:
            self._parsers.pop(cfg_id, None)
```

- [ ] **Step 3.4: Run the test to verify it passes**

```bash
cd autotrader/backend
uv run pytest tests/test_pipeline.py::test_pipeline_invalidate_for_chat_drops_only_matching_caches -v
```

Expected: PASS.

- [ ] **Step 3.5: Wire `unwatch_endpoint` to call `invalidate_for_chat`**

Replace `autotrader/backend/src/autotrader/routers/telegram.py:282-288` with:

```python
@router.delete("/watch/{chat_id}", response_model=OkResponse)
async def unwatch_endpoint(
    chat_id: int,
    session: SessionDep,
    pipeline: PipelineDep,
) -> OkResponse:
    await remove_watch(session, chat_id)
    # Drop cached parsers for this chat. Dispatch already filters
    # via ``WatchedChannel.enabled`` so behaviour was correct
    # without this — but cached parsers leaked memory until a
    # signature-drift rebuild.
    pipeline.invalidate_for_chat(chat_id)
    return OkResponse()
```

Also add `PipelineDep` to the import line at `autotrader/backend/src/autotrader/routers/telegram.py:19`:

```python
from autotrader.dependencies import PipelineDep, SessionDep, TelegramDep
```

- [ ] **Step 3.6: Write the unwatch invalidation test**

Append to `autotrader/backend/tests/test_telegram.py`:

```python
def test_unwatch_invalidates_pipeline_cache(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE /telegram/watch/{chat_id} should call
    Pipeline.invalidate_for_chat so cached parsers for a no-longer-
    watched chat don't sit in memory."""
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    # Pre-watch.
    client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": -1012,
            "title": "Trash",
            "chat_type": "channel",
            "username": None,
            "enabled": True,
        },
    )

    from autotrader.main import app  # noqa: PLC0415

    calls: list[int] = []

    def _spy(self: object, chat_id: int) -> None:  # noqa: ARG001
        calls.append(chat_id)

    monkeypatch.setattr(
        type(app.state.pipeline),
        "invalidate_for_chat",
        _spy,
    )

    r = client.delete("/telegram/watch/-1012", headers=headers)
    assert r.status_code == 200, r.text
    assert calls == [-1012], f"expected invalidate_for_chat(-1012); got {calls}"
```

- [ ] **Step 3.7: Run the unwatch test**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py::test_unwatch_invalidates_pipeline_cache -v
```

Expected: PASS.

- [ ] **Step 3.8: Run full Telegram + pipeline test modules**

```bash
cd autotrader/backend
uv run pytest tests/test_telegram.py tests/test_pipeline.py -v
```

Expected: every test PASS.

- [ ] **Step 3.9: Commit**

```bash
git add autotrader/backend/src/autotrader/services/pipeline.py autotrader/backend/src/autotrader/routers/telegram.py autotrader/backend/tests/test_pipeline.py autotrader/backend/tests/test_telegram.py
git commit -m "feat(autotrader): Pipeline.invalidate_for_chat + unwatch wiring

Adds Pipeline.invalidate_for_chat(chat_id) that drops every cached
parser belonging to that chat. /telegram/watch DELETE now calls it
so a no-longer-watched chat's parser caches don't leak memory.

Behavioural impact is nil — dispatch already filtered unwatched
chats via WatchedChannel.enabled — but the cleanup keeps the cache
size honest and frees memory for high-rotation operators.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Frontend — invalidate `/pipeline/status` after watch

**Files:**
- Modify: `autotrader/frontend/app/dashboard/telegram/page.tsx`

- [ ] **Step 4.1: Find the watch mutation in the Telegram page**

```bash
cd autotrader/frontend
grep -n "telegram.watch\|watch:.*useMutation\|telegram\.unwatch" app/dashboard/telegram/page.tsx | head
```

Note the line of the `useMutation` block that wraps `telegram.watch(...)` and the one wrapping `telegram.unwatch(...)`.

- [ ] **Step 4.2: Add `["pipeline","status"]` invalidation to both mutation success handlers**

Inside each of the watch/unwatch `useMutation` blocks in `autotrader/frontend/app/dashboard/telegram/page.tsx`, ensure the `onSuccess` does:

```ts
onSuccess: () => {
  qc.invalidateQueries({ queryKey: ["telegram", "dialogs"] });
  qc.invalidateQueries({ queryKey: ["telegram", "watched"] });
  qc.invalidateQueries({ queryKey: ["pipeline", "status"] });
},
```

If `qc` isn't already in scope, add `const qc = useQueryClient();` at the top of the component (it's almost certainly already there; check first).

If the existing `onSuccess` already invalidates `["telegram", "watched"]`, simply add the `["pipeline", "status"]` line alongside it — don't duplicate the dialog-list invalidation. Inspect first; minimal diff.

- [ ] **Step 4.3: Type-check the frontend**

```bash
cd autotrader/frontend
bun run type-check
```

Expected: 0 errors.

- [ ] **Step 4.4: Build to confirm no runtime regression**

```bash
cd autotrader/frontend
bun run build
```

Expected: build completes, no new warnings.

- [ ] **Step 4.5: Commit**

```bash
git add autotrader/frontend/app/dashboard/telegram/page.tsx
git commit -m "fix(autotrader/frontend): invalidate pipeline status after watch toggle

Watching/unwatching a chat changes Pyrogram's subscribed_chat_count
(now that /telegram/watch awaits subscribe_chat). The dashboard
gauge in /pipeline/status only refreshed on the 5s poll; explicit
invalidation lets the new count appear instantly so operators can
verify the new chat is actually subscribed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `reconcile_pending` — three-bucket rewrite

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/executor.py:174-206`
- Test: `autotrader/backend/tests/test_startup_recovery.py`

- [ ] **Step 5.1: Write the failing tests**

Append to `autotrader/backend/tests/test_startup_recovery.py`:

```python
import asyncio
from datetime import UTC, datetime, timedelta

import pytest


def _seed_pending(
    *,
    placed_at: datetime | None,
    duration_seconds: int,
    parser_config_id: int = 1,
) -> int:
    """Insert a pending TradeAttempt row and return its id."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt, insert_attempt  # noqa: PLC0415

    async def _do() -> int:
        async with AsyncSessionLocal() as s:
            row = await insert_attempt(
                s,
                TradeAttempt(
                    chat_id=-1001,
                    parser_config_id=parser_config_id,
                    asset="EURUSD",
                    asset_raw="EURUSD",
                    direction="call",
                    duration_seconds=duration_seconds,
                    stake=1.0,
                    trade_mode="live",
                    fire_at=None,
                    status="pending",
                    placed_at=placed_at,
                ),
            )
            return int(row.id or 0)

    return asyncio.new_event_loop().run_until_complete(_do())


def _read_status(attempt_id: int) -> tuple[str, str | None]:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    async def _do() -> tuple[str, str | None]:
        async with AsyncSessionLocal() as s:
            row = await s.get(TradeAttempt, attempt_id)
            assert row is not None
            return row.status, row.error

    return asyncio.new_event_loop().run_until_complete(_do())


def test_reconcile_pending_placed_at_none_expires_immediately(
    fake_quotex: None,
) -> None:
    """A pending row with placed_at=None means the broker never accepted
    the order. Today's behaviour — mark expired immediately — is
    correct for this case."""
    from autotrader.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app):
        attempt_id = _seed_pending(placed_at=None, duration_seconds=60)

    # Re-enter lifespan to trigger reconcile_pending.
    with TestClient(app):
        pass

    status_, error = _read_status(attempt_id)
    assert status_ == "expired"
    assert error is not None
    assert "watcher lost on restart" in error


def test_reconcile_pending_in_flight_stays_pending(
    fake_quotex: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending row whose placed_at + duration is still in the future
    represents a trade the broker is still running. We must NOT mark
    it expired; the deferred reconciler waits for the natural settle
    window before deciding."""
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.services import executor as exec_mod  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    # Stub the deferred sleep so the test doesn't actually wait 60s.
    monkeypatch.setattr(exec_mod, "_RECONCILE_SLACK_SECONDS", 0)

    placed = datetime.now(UTC) - timedelta(seconds=10)
    with TestClient(app):
        attempt_id = _seed_pending(placed_at=placed, duration_seconds=300)

    # Lifespan re-enters; reconcile sees the row's window still open.
    with TestClient(app):
        pass

    status_, error = _read_status(attempt_id)
    assert status_ == "pending", (
        f"in-flight rows must stay pending; got status={status_!r}, error={error!r}"
    )


def test_reconcile_pending_post_window_marks_expired_with_clearer_note(
    fake_quotex: None,
) -> None:
    """A pending row whose settle window has clearly passed gets
    expired with a clearer note than today's "watcher lost on
    restart" — the broker has already settled but pyquotex can't tie
    it back."""
    from autotrader.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    placed = datetime.now(UTC) - timedelta(seconds=600)
    with TestClient(app):
        attempt_id = _seed_pending(placed_at=placed, duration_seconds=60)

    with TestClient(app):
        pass

    status_, error = _read_status(attempt_id)
    assert status_ == "expired"
    assert error is not None
    assert "settle window passed" in error
    assert "check broker history" in error.lower()


def test_reconcile_pending_does_not_tick_martingale(
    fake_quotex: None,
) -> None:
    """The deferred reconciler must NOT call martingale_state.record_outcome —
    we don't know the outcome, so the ladder stays where it was at
    the loss/win that preceded the restart."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import create_config  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    async def _seed() -> int:
        async with AsyncSessionLocal() as s:
            cfg = await create_config(
                s,
                chat_id=-1001,
                payload={
                    "name": "p",
                    "priority": 100,
                    "parser_type": "template",
                    "parser_config": {"template": "{DIRECTION} {ASSET}"},
                    "default_stake": 1.0,
                    "default_duration_seconds": 60,
                    "trade_mode": "live",
                    "martingale_enabled": True,
                    "martingale_multiplier": 2.0,
                    "martingale_max_streak": 3,
                    "martingale_reset_on_win": True,
                    "martingale_auto_recovery": False,
                    "enabled": True,
                    "asset_aliases": {},
                    "aggregate_window_seconds": 0,
                    "timezone": "UTC",
                    "timezone_offset_minutes": 0,
                },
            )
            row = MartingaleState(
                parser_config_id=cfg.id or 0,
                current_streak=2,
                last_outcome="lost",
                last_stake=4.0,
            )
            s.add(row)
            await s.commit()
            return int(cfg.id or 0)

    with TestClient(app):
        cfg_id = asyncio.new_event_loop().run_until_complete(_seed())
        placed = datetime.now(UTC) - timedelta(seconds=600)
        _seed_pending(placed_at=placed, duration_seconds=60, parser_config_id=cfg_id)

    with TestClient(app):
        pass

    async def _read_state() -> int:
        async with AsyncSessionLocal() as s:
            row = await s.get(MartingaleState, cfg_id)
            assert row is not None
            return row.current_streak

    streak = asyncio.new_event_loop().run_until_complete(_read_state())
    assert streak == 2, f"reconcile must not touch martingale ladder; streak={streak}"
```

- [ ] **Step 5.2: Run the tests to verify they fail**

```bash
cd autotrader/backend
uv run pytest tests/test_startup_recovery.py::test_reconcile_pending_in_flight_stays_pending tests/test_startup_recovery.py::test_reconcile_pending_post_window_marks_expired_with_clearer_note -v
```

Expected: FAIL — today's `reconcile_pending` expires every pending row regardless of `placed_at`/`duration`.

- [ ] **Step 5.3: Add the slack constant + rewrite `reconcile_pending`**

In `autotrader/backend/src/autotrader/services/executor.py`, add this near the top of the module (after the existing imports, before `_wire_iso8601`):

```python
# Extra grace before a pending row whose nominal settle window has
# passed gets marked ``expired`` with the clearer note. The 60s
# slack covers broker-side processing jitter — pyquotex sometimes
# emits ``order_closed`` a beat after the natural expiry.
_RECONCILE_SLACK_SECONDS = 60
```

Replace the body of `reconcile_pending` (lines ~174-206 in the current file — the method whose docstring starts with "Sweep ``pending`` rows after a restart.") with:

```python
    async def reconcile_pending(self) -> None:
        """Reclassify ``pending`` rows after a restart.

        Three buckets:

        * ``placed_at is None`` — broker never accepted the order.
          Mark ``expired`` immediately with the historic
          "watcher lost on restart" note.
        * ``placed_at + duration_seconds + slack > utcnow()`` — the
          broker is still inside the binary-options window. Leave
          the row ``pending`` and spawn a deferred task that sleeps
          until ``placed_at + duration + slack`` and then marks
          ``expired`` with the clearer note.
        * ``placed_at + duration_seconds + slack <= utcnow()`` — the
          broker has already settled. Mark ``expired`` immediately
          with the clearer note.

        In every "settled but unrecoverable" case the martingale
        ladder is **not** ticked — we don't know the outcome and
        guessing would silently corrupt recovery sequences.
        """
        async with AsyncSessionLocal() as session:
            rows = await list_pending(session)
        if not rows:
            return

        legacy_note = (
            "watcher lost on restart — pyquotex doesn't track tickets "
            "across reconnects, so the outcome can't be tied back. "
            "Check broker history if needed; reset the martingale "
            "ladder if the recovery sequence got out of sync"
        )
        clearer_note = (
            "settle window passed; broker likely settled this trade "
            "but pyquotex couldn't tie the result back across the "
            "restart. Check broker history if the outcome matters; "
            "the martingale ladder is left untouched"
        )

        now = datetime.now(UTC)
        deferred = 0
        immediate = 0
        for row in rows:
            placed = row.placed_at
            if placed is None:
                await self._mark_reconciled(row.id or 0, legacy_note)
                immediate += 1
                continue

            placed_aware = (
                placed if placed.tzinfo is not None
                else placed.replace(tzinfo=UTC)
            )
            settle_at = placed_aware + timedelta(
                seconds=row.duration_seconds + _RECONCILE_SLACK_SECONDS,
            )
            wait_seconds = (settle_at - now).total_seconds()
            if wait_seconds <= 0:
                await self._mark_reconciled(row.id or 0, clearer_note)
                immediate += 1
            else:
                self._spawn_deferred_reconcile(
                    attempt_id=row.id or 0,
                    wait_seconds=wait_seconds,
                    note=clearer_note,
                )
                deferred += 1

        log.info(
            "executor.reconcile",
            immediate_expired=immediate,
            deferred=deferred,
        )

    def _spawn_deferred_reconcile(
        self,
        *,
        attempt_id: int,
        wait_seconds: float,
        note: str,
    ) -> None:
        """Schedule a delayed mark-expired so in-flight trades aren't
        nuked the moment the API restarts mid-window. Tracked in the
        same ``_watchers`` set as result-watchers so ``shutdown()``
        awaits cancellation cleanly."""
        async def _runner() -> None:
            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                return
            await self._mark_reconciled(attempt_id, note)

        task = asyncio.create_task(_runner())
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)
```

Add `from datetime import UTC, datetime, timedelta` to the imports at the top of the file if `timedelta` isn't already imported (it isn't — current import is `from datetime import UTC, datetime`).

- [ ] **Step 5.4: Run the new reconcile tests**

```bash
cd autotrader/backend
uv run pytest tests/test_startup_recovery.py -v
```

Expected: every test in the module PASSES (4 new + 2 existing).

- [ ] **Step 5.5: Run the full backend suite to catch any latent breakage**

```bash
cd autotrader/backend
uv run pytest -x
```

Expected: all tests pass. The reconcile change touches a startup hook the rest of the suite goes through — anything stale here surfaces here.

- [ ] **Step 5.6: Commit**

```bash
git add autotrader/backend/src/autotrader/services/executor.py autotrader/backend/tests/test_startup_recovery.py
git commit -m "fix(autotrader/executor): three-bucket reconcile_pending

Restart no longer pre-emptively marks every pending row 'expired'.
The new behaviour:

  placed_at is None    -> expire immediately (broker never got it).
  in-flight on broker  -> stay pending; deferred task expires it
                          after placed_at + duration + 60s slack.
  past settle window   -> expire immediately with clearer note.

Martingale state is NOT ticked in the deferred path — we don't
know the outcome, so guessing would corrupt recovery sequences.

Closes the screenshot regression where every restart killed
mid-window trades and confused the gale ladder.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Auto-recovery refetches the parser config

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/executor.py:521-587`
- Test: `autotrader/backend/tests/test_risk.py`

- [ ] **Step 6.1: Write the failing test**

Append to `autotrader/backend/tests/test_risk.py`:

```python
async def test_martingale_auto_recovery_skips_when_parser_disabled_mid_streak(
    async_client: httpx.AsyncClient,
) -> None:
    """If the operator disables the parser between the loss settle
    and the recovery dispatch, the recovery must NOT fire — the
    user has explicitly told the bot to stop trading on this parser.
    Today the recovery path uses a stale ``cfg`` snapshot captured
    at submit time and never refetches; this test pins the fix that
    re-reads the row inside ``_fire_auto_recovery`` and bails on
    ``enabled=False``."""
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
            "max_streak": 3,
            "reset_on_win": True,
            "auto_recovery": True,
        },
        default_stake=10.0,
    )
    await _activate()

    # Original signal loses; before the recovery fires, disable the
    # parser. The settle path runs synchronously inside _settle_watchers
    # so we disable BEFORE that drain.
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")

    # Disable the parser. The original trade is still pending in
    # WatcherFakeQuotex's queue — its settle will then attempt to
    # fire a recovery that should now be blocked.
    r = await async_client.put(
        f"/parsers/configs/{cfg_id}",
        headers=headers,
        json=_disable_parser_payload(cfg_id),
    )
    assert r.status_code == 200, r.text

    await _settle_watchers(async_client)
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [10.0], (
        "auto_recovery must skip when the parser was disabled mid-streak; "
        f"got buy_calls={amounts}"
    )
```

This test references a small helper `_disable_parser_payload(cfg_id)`. Add it to `autotrader/backend/tests/test_risk.py` near the existing `_create_parser` helper (find it via `grep -n "_create_parser" tests/test_risk.py | head` — typically around the file's top quarter). Helper:

```python
def _disable_parser_payload(cfg_id: int) -> dict[str, object]:
    """Minimal PUT body to flip enabled=False without changing other
    fields. Mirrors the ConfigPayload shape from routers/parsers.py."""
    return {
        "name": "p",
        "priority": 100,
        "parser_type": "template",
        "parser_config": {"template": "{DIRECTION} {ASSET}"},
        "timezone": "UTC",
        "timezone_offset_minutes": 0,
        "asset_aliases": {},
        "aggregate_window_seconds": 0,
        "default_stake": 10.0,
        "default_duration_seconds": 60,
        "trade_mode": "live",
        "martingale": {
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 3,
            "reset_on_win": True,
            "auto_recovery": True,
        },
        "enabled": False,
    }
```

If `_create_parser` returns the cfg id, use it directly. If it returns the response body, capture `r.json()["id"]` and pass that. Inspect the existing helper before writing the test.

- [ ] **Step 6.2: Run the test to verify it fails**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_martingale_auto_recovery_skips_when_parser_disabled_mid_streak -v
```

Expected: FAIL — current code uses the cached `cfg.enabled=True` snapshot from before the disable, so it fires a recovery anyway.

- [ ] **Step 6.3: Refactor `_fire_auto_recovery` to refetch the config**

In `autotrader/backend/src/autotrader/services/executor.py`, replace the body of `_fire_auto_recovery` (the method with that name, around lines 521-587). The new body opens a fresh session, refetches the config, and bails on disabled / missing rows:

```python
    async def _fire_auto_recovery(
        self,
        *,
        original: TradeAttempt,
        cfg: ParserConfig,
        streak: int,
    ) -> None:
        """Submit a recovery trade derived from the lost ``original``.

        Refetches the parser config inside this method so an operator
        who disables the parser mid-loss-streak doesn't get an extra
        recovery trade. The cached ``cfg`` from the calling settle
        path may be stale by the time we reach here.

        Goes through the full ``submit`` path so the same risk gate
        guards (kill switch, daily loss cap, max-concurrent, REAL-mode
        env flag) apply. Stake is left ``None`` on the synthesised
        signal so the risk gate computes ``base × multiplier^streak``
        from the freshly-incremented martingale state — a single
        source of truth for "what's the stake right now".
        """
        from autotrader.services.parsers import ParsedSignal  # noqa: PLC0415

        log.info(
            "executor.auto_recovery.entered",
            config_id=cfg.id,
            original_attempt_id=original.id,
            streak=streak,
        )
        async with AsyncSessionLocal() as session:
            fresh_cfg = (
                await get_config(session, cfg.id or 0)
                if cfg.id is not None
                else None
            )
            settings_row = await session.get(GlobalSettings, 1)
            if settings_row is None:
                settings_row = GlobalSettings(id=1)

        if fresh_cfg is None:
            log.info(
                "executor.auto_recovery.skipped",
                config_id=cfg.id,
                original_attempt_id=original.id,
                reason="parser_config deleted",
            )
            return
        if not fresh_cfg.enabled:
            log.info(
                "executor.auto_recovery.skipped",
                config_id=cfg.id,
                original_attempt_id=original.id,
                reason="parser_config disabled",
            )
            return
        if not fresh_cfg.martingale_enabled or not fresh_cfg.martingale_auto_recovery:
            log.info(
                "executor.auto_recovery.skipped",
                config_id=cfg.id,
                original_attempt_id=original.id,
                reason="martingale toggles flipped off mid-streak",
            )
            return

        signal = ParsedSignal(
            asset=original.asset,
            direction=original.direction,  # type: ignore[arg-type]
            duration_seconds=original.duration_seconds,
            stake=None,                          # risk gate computes
            fire_at=None,                        # ASAP / live
            raw_text=f"[auto-recovery for trade #{original.id}]",
            parser_id=f"cfg-{fresh_cfg.id}-recovery-{streak}",
            asset_raw=original.asset_raw,
        )
        try:
            attempt = await self.submit(
                signal=signal,
                parser_config=fresh_cfg,
                settings=settings_row,
            )
        except Exception as exc:  # pragma: no cover - belt + braces
            log.exception(
                "executor.auto_recovery.failed",
                config_id=fresh_cfg.id,
                original_attempt_id=original.id,
                streak=streak,
                error=str(exc),
            )
            return
        log.info(
            "executor.auto_recovery.fired",
            config_id=fresh_cfg.id,
            original_attempt_id=original.id,
            streak=streak,
            asset=original.asset,
            direction=original.direction,
            recovery_attempt_id=attempt.id,
            recovery_status=attempt.status,
            recovery_stake=attempt.stake,
        )
```

- [ ] **Step 6.4: Run the new test**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py::test_martingale_auto_recovery_skips_when_parser_disabled_mid_streak -v
```

Expected: PASS.

- [ ] **Step 6.5: Run the full risk module to confirm no regression**

```bash
cd autotrader/backend
uv run pytest tests/test_risk.py -v
```

Expected: every test PASS. The two `auto_recovery` tests at lines 322 / 373 / 412 must still pass — the refactor preserves the happy path.

- [ ] **Step 6.6: Commit**

```bash
git add autotrader/backend/src/autotrader/services/executor.py autotrader/backend/tests/test_risk.py
git commit -m "fix(autotrader/executor): auto-recovery refetches parser config

The recovery path used to read the cached cfg snapshot from the
original signal's settle. If the operator disabled the parser
between the loss and the recovery dispatch, the recovery fired
anyway because the cache still said enabled=True.

Now _fire_auto_recovery opens a fresh session, get_config()s the
row, and bails (with a logged reason) on:
  - parser_config deleted
  - parser_config disabled
  - martingale_enabled or martingale_auto_recovery flipped off

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Parser docs + editor link

**Files:**
- Create: `autotrader/docs/PARSERS.md`
- Modify: `autotrader/README.md`
- Modify: `autotrader/frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx` (header link)

- [ ] **Step 7.1: Write `autotrader/docs/PARSERS.md`**

Create `autotrader/docs/PARSERS.md` with the following content:

````markdown
# Writing parsers

> The autotrader pipeline reads each Telegram message you watch through
> a *parser*. The parser turns prose like _"🟢 BUY EUR/USD 1m"_ into a
> structured signal — asset, direction, duration, optional fire-time
> and stake — that the risk gate and broker executor act on.

Four parser types ship today, each tuned to a different channel
posting style.

| Type | One-liner | Use it when |
|------|-----------|-------------|
| **Template** | Click-to-pick layout: `{DIRECTION} {ASSET} {DURATION}` | Channels post one tidy line per signal. |
| **Regex** | Power-user: full Python regex with named groups | The channel's layout drifts; templates can't capture it. |
| **Prep + Trigger** | Two messages: a prep sets up params, a trigger fires | "PAIR / TIME" line followed by a 👍 / 👎 sticker. |
| **Batch** | One message → many scheduled signals | A daily roster: `01:51 USDBDT-OTC PUT`, etc. |

Multiple parsers can live on the same chat — they're independent
subscribers, not alternatives. Lower **priority** values run first;
the editor's priority field defaults to 100.

---

## 1. Template parser

A template is a string with bracketed placeholders. Whitespace in the
template matches any run of whitespace; everything else is regex-
escaped.

```
{DIRECTION} {ASSET} {DURATION}
```

Will match `BUY EURUSD 1m`, `SELL EUR/USD 5 min`, `🟢 EUR/USD 60s`.

### Placeholders

| Token | What it captures | Example values |
|-------|------------------|----------------|
| `{ASSET}` | Pair / asset code | `EURUSD`, `EUR/USD`, `USD NGN OTC`, `XAUUSD` |
| `{DIRECTION}` | Buy/sell side | `BUY`, `SELL`, `UP`, `DOWN`, 🟢, 🔴, 👍, 👎 |
| `{DURATION}` | Expiry window | `1m`, `60s`, `M5`, `5 minutes`, bare number `60` |
| `{TIME}` | Scheduled fire time (HH:MM in channel TZ) | `14:30`, `09:05:30` |
| `{STAKE}` | Numeric stake override | `25`, `100.5` |

### Built-in templates

The editor offers click-to-pick presets — pick the closest one and
adjust:

- `{DIRECTION} {ASSET} {DURATION}` → `BUY EURUSD 1m`
- `{ASSET} {DIRECTION} {DURATION}` → `EURUSD BUY 1m`
- `{DIRECTION} {ASSET} expiry {DURATION}` → `🟢 EUR/USD expiry 1m`
- `{DIRECTION} {ASSET} {DURATION} at {TIME}` → `BUY EURUSD 1m at 14:30`
- `{DIRECTION} {ASSET} {DURATION} ${STAKE}` → `SELL GBPUSD 5m $25`

---

## 2. Regex parser

Provide a Python regex with **named groups**. Required:

- `direction`
- `asset`

Optional:

- `duration` — normalised to seconds (default unit: minutes).
- `fire_at` (or `time` — synonym) — for scheduled trades.
- `stake` — numeric override of the parser's default stake.

Anything outside those groups is ignored, so emoji / prose / noise in
your pattern doesn't leak into the structured signal.

```python
^(?P<direction>BUY|SELL)\s+(?P<asset>[A-Z/]+)\s+(?P<duration>\d+m)$
```

The full message text (after newline-joining when multi-message
buffering is on) is searched; the first match wins.

---

## 3. Prep + Trigger parser

For channels that post a *prep* message followed by a *trigger*:

```
Message 1 (prep)    "🌐 PAIR: USD-NGN OTC   ⏱ TIME: 01 Minute"
Message 2 (trigger) [👍 sticker]   ← direction only
```

The parser:

- runs the **prep** template/regex on every incoming message; when it
  matches, the parser stores asset / duration / stake / fire_at
  per-chat;
- runs the **trigger** template/regex on every incoming message; when
  it matches, the parser combines the stored prep with the trigger's
  direction and emits a `ParsedSignal` immediately. No window wait.

If no trigger arrives within `aggregate_window_seconds` (default
120s, edited as "Prep-to-trigger gap" in the editor), the stored
prep is dropped silently.

### Required groups

| Phase | Required | Optional |
|-------|----------|----------|
| Prep | `asset` | `duration`, `fire_at`, `time`, `stake` |
| Trigger | `direction` | — |

### Sticker tip

Telegram stickers carry an emoji; we surface that emoji as the
message text. So `(?P<direction>👍|👎)` is enough for a "thumbs"
trigger:

```
prep:    PAIR: {ASSET} TIME: {DURATION} Minute
trigger: {DIRECTION}            ← the placeholder matches 👍 / 👎
```

### Restart caveat

In-memory pending preps don't survive an API restart — half-built
signals straddling a bounce are dropped. The cache also evicts
expired preps automatically.

---

## 4. Batch parser

For one message containing many scheduled signals:

```
DATE: 07.05.2026
TIMEZONE : UTC/GMT (+06:00)
FUTURE SIGNALS 🕯
📏📏📏📏📏📏📏📏📏📏

01:51 USDBDT-OTC PUT
01:53 USDBDT-OTC PUT
01:55 USDBDT-OTC PUT
01:58 USDBDT-OTC CALL

📏📏📏📏📏📏📏📏📏📏
```

### Header (optional)

Captures DATE + tz_offset that apply to every row. Without a header
the parser uses today's date in the editor's timezone offset.

| Group | Notes |
|-------|-------|
| `date` | `DD.MM.YYYY`, `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`, `DD.MM.YY` |
| `tz_offset` | `+06:00`, `-0500`, `+6`, `+06` — sign + HH(:MM) |

### Row (required)

| Group | Required | Notes |
|-------|----------|-------|
| `time` | yes | `HH:MM` or `HH:MM:SS` in the row, channel TZ |
| `asset` | yes | resolved against the broker catalogue |
| `direction` | yes | matched against the direction normaliser |
| `duration` | optional | per-row override |
| `stake` | optional | per-row override |

The row regex is run with `finditer` over the joined text, so each
match becomes a pending order at the resolved UTC time.

---

## Direction tokens (full table)

The direction normaliser is generous about how channels write the
side. All these resolve to `call`:

```
buy  up    call    long    bull   bullish   green
high  higher  rise   rising   above
🟢 🟩 📈 ⬆ ⬆️ ↑ 🔼 🔝   👍 👍🏻 👍🏼 👍🏽 👍🏾 👍🏿 ✅ 💚
```

And these to `put`:

```
sell  down  put  short  bear  bearish  red
low  lower  fall  falling  below
🔴 🟥 📉 ⬇ ⬇️ ↓ 🔽   👎 👎🏻 👎🏼 👎🏽 👎🏾 👎🏿 ❌ ❤
```

If a channel uses a token not on this list, you can pre-translate it
via the asset-aliases box (yes, it works for direction too — the
left side is the raw token, the right side is `BUY` or `SELL`).

---

## Duration units

Parser durations accept any of these:

| Form | Examples | Resolved |
|------|----------|----------|
| Numeric + unit | `1m`, `60s`, `5 minutes`, `2h` | direct |
| `M`-prefix | `M1`, `M5`, `M15` | minutes (TradingView shorthand) |
| Bare number | `60`, `5` | uses **Default duration unit** (minutes by default) |

Configure the **Default duration (seconds)** field on the editor for
the fallback when nothing is captured at all. Many channels also put
the expiry in a header line — the parser does a best-effort scan
for `<N> minute(s)` / `<N> seconds` / etc. when the row regex doesn't
capture a duration directly.

---

## Asset resolution

Channels write asset names a thousand different ways. The resolver
looks at each raw asset in this order:

1. **Manual alias** (case-insensitive) from the editor's asset-aliases
   box — explicit override.
2. **Trailing OTC token detection** — if the raw asset's last token
   is `OTC` (`USD NGN OTC`, `GOLD OTC`), the canonical form is
   `<base>_otc`.
3. **Exact match** against the broker's known asset catalogue.
4. **`_otc` cross-probe** — try the OTC and non-OTC variants of the
   base when the channel didn't say but the broker has only one
   variant available.
5. **Fallback** — preserve the channel's intent (OTC-marked names
   become `<base>_otc`; bare names stay `<base>`).

The live tester shows which path resolved — look for the `asset:
exact / alias / otc / fallback` badge on a successful match.

---

## Trade-mode pin

| Mode | Behaviour |
|------|-----------|
| `live` | Always fires immediately; any `fire_at` extracted from the signal is stripped. |
| `scheduled` | Requires a parsed `fire_at`; rejects live-only signals. |
| `auto` | Default. Uses `fire_at` when present, otherwise live. |

Mix and match per parser when one channel posts both styles.

---

## Martingale recovery

| Field | Notes |
|-------|-------|
| **Enable** | Required to do anything. |
| **Multiplier** | Stake = `base × multiplier^streak`. 2.0 doubles each loss. |
| **Max streak level** | Cap the recovery ladder; 0 = uncapped. |
| **Reset on win** | A win resets `current_streak` to 0. |
| **Auto-recovery** | When ON, a *losing* trade fires an immediate same-asset / same-direction recovery trade with the multiplied stake — without waiting for the channel to send another signal. Mirrors how channels phrase their gale rules ("IF LOSS TAKE 1 STEP MTG (Same Direction Double Amount)"). |

The runtime streak counter lives on a separate row per parser — the
risk module's "Reset streak" button in the dashboard zeroes it.

---

## Why isn't my parser firing? — checklist

If signals from a channel aren't reaching trades, walk this list
top-to-bottom:

1. **Is the chat watched?** `/dashboard/telegram` → confirm the chat
   is in the watched list and toggled on.
2. **Did Pyrogram subscribe the chat?** `/dashboard/pipeline` →
   "Channels subscribed" gauge should equal "Channels watched".
   If they differ, log out + re-login (forces `_prime_peer_cache`)
   or DELETE+POST the watch row (the watch endpoint subscribes on
   each enabled POST).
3. **Is at least one parser enabled on the chat?** `/dashboard/parsers/<chat>`
   → "Enabled" toggle on the parser row.
4. **Master switch on?** `/dashboard/pipeline` → "Pipeline active"
   toggle should be green. Kill switch should NOT be engaged.
5. **What does `/dashboard/decisions` show?** Every dispatch lands
   here — matched, no-match, build-failed, no-configs, or
   pipeline-inactive. The reason column tells you which step the
   message stopped at.
6. **Live tester** — paste a real channel message into
   `/dashboard/parsers/<chat>/<parser>` and click Test. If the
   tester says "no match" but `/decisions` shows the message
   arriving, your regex/template needs work.
7. **Trade-mode mismatch** — `trade_mode=scheduled` rejects
   signals without a `fire_at`; `trade_mode=live` strips any
   `fire_at` extracted. Check the parser's pin matches the
   channel's posting style.
8. **Risk gate blocked it** — `/dashboard/trades` lists rejected
   attempts with the reason. Daily loss / stake caps, max-concurrent,
   broker-not-connected, REAL gate (env flag), kill switch all show
   here.

---

## Live tester

The editor's right-hand pane is a self-contained replay:

- Each block is one Telegram message — blank lines inside a block
  are kept (real prep messages have them); only a *new block* is a
  message boundary.
- "Add to tester" buttons on the recent-messages panel push real
  channel messages into the pane; click twice to push a prep + a
  sticker as two separate blocks.
- The tester applies asset auto-resolution so you see the same
  `raw → broker` mapping the live executor will use at trade time.
````

- [ ] **Step 7.2: Add a link to the docs from the autotrader README**

Edit `autotrader/README.md` — find the "How it works" section near the bottom and insert a one-line link before the project-layout block:

```markdown
**Writing parsers:** see [`docs/PARSERS.md`](docs/PARSERS.md) for the
template / regex / prep+trigger / batch reference, the direction-token
table, and the "why isn't my parser firing?" troubleshooting checklist.
```

- [ ] **Step 7.3: Add the editor header link**

Edit `autotrader/frontend/app/dashboard/parsers/[chat_id]/[config_id]/page.tsx`. In the page header (top `<section>`, around lines 142-162), the existing layout has the title on the left and a "← Channel parsers" link on the right. Add a "📖 Parser guide" link alongside it. Replace lines 155-160 (the `<Link>` element block) with:

```tsx
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <a
              href="https://github.com/imran-ahmedani/pyquotex/blob/master/autotrader/docs/PARSERS.md"
              target="_blank"
              rel="noreferrer"
              className="hover:text-foreground"
              title="Parser writing guide + troubleshooting"
            >
              📖 Parser guide
            </a>
            <Link
              href={`/dashboard/parsers/${chatId}`}
              className="hover:text-foreground"
            >
              ← Channel parsers
            </Link>
          </div>
```

This uses an external GitHub link (works even if the API isn't serving static docs). The link target is reusable; if you self-host docs at a different URL, change the `href`.

- [ ] **Step 7.4: Type-check the frontend**

```bash
cd autotrader/frontend
bun run type-check
```

Expected: 0 errors.

- [ ] **Step 7.5: Build the frontend**

```bash
cd autotrader/frontend
bun run build
```

Expected: build completes; the new editor page still passes static analysis.

- [ ] **Step 7.6: Commit**

```bash
git add autotrader/docs/PARSERS.md autotrader/README.md autotrader/frontend/app/dashboard/parsers/\[chat_id\]/\[config_id\]/page.tsx
git commit -m "docs(autotrader): PARSERS.md + editor guide link

Comprehensive reference for the four parser types (template / regex
/ prep+trigger / batch), the direction-token table, the duration
unit table, asset resolution, trade-mode pin semantics, and a
\"why isn't my parser firing?\" troubleshooting checklist.

The parser editor's header now links to the doc on GitHub so an
operator can jump to the reference without leaving the dashboard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: End-to-end verification

**Files:** No code changes. Manual + automated verification only.

- [ ] **Step 8.1: Run the full backend test suite**

```bash
cd autotrader/backend
uv run pytest
```

Expected: every test passes. Goal is to confirm none of the patches
regressed an existing test.

- [ ] **Step 8.2: Run backend lint**

```bash
cd autotrader/backend
uv run ruff check src tests
```

Expected: no findings. Fix anything ruff complains about (mostly import-order or unused-import patches will surface here).

- [ ] **Step 8.3: Run frontend type-check + build**

```bash
cd autotrader/frontend
bun run type-check
bun run build
```

Expected: clean.

- [ ] **Step 8.4: Run the frontend e2e smoke test**

```bash
cd autotrader/frontend
bun run e2e
```

Expected: existing dashboard smoke test stays green.

- [ ] **Step 8.5: Manual end-to-end check (the regression repro)**

This step needs a real Telegram account + a sandbox channel you
control so you can post test messages. Skip if you only want
automated coverage; the e2e harness covers the watch + subscribe
path with stubs.

1. Start the API + dashboard locally: `docker compose up --build`.
2. Sign in with the dashboard passcode.
3. Connect Telegram (phone → SMS code → 2FA if any).
4. Connect the broker (DEMO mode is enough; do NOT enable
   AUTOTRADER_LIVE_TRADING_ENABLED for this test).
5. Add a watched chat (your sandbox channel). Confirm
   `/pipeline/status` shows `subscribed_chat_count` increment by 1
   within 2 seconds.
6. Add a parser config and enable it.
7. Send a test message in the sandbox channel that the parser
   should match. Confirm `/dashboard/decisions` shows the matched
   dispatch *without* restarting the API.
8. Confirm a trade row appears in `/dashboard/trades` with status
   `pending` (or `rejected` if a risk gate blocked it).
9. Restart the API mid-trade window: `docker compose restart api`.
10. Confirm the in-flight row stays `pending` until its
    `placed_at + duration` passes, then flips to `expired` with
    the new "settle window passed; check broker history" note.
11. Confirm the martingale streak gauge (`/dashboard/risk`) stays
    where it was before the restart — the deferred reconciler must
    NOT have ticked the ladder.

- [ ] **Step 8.6: Stage final review commit (if needed)**

If any of the above surfaced a bug not covered by an earlier task,
add a follow-up patch in its own commit. Otherwise this task ends
with no commit — it's pure verification.

---

## Self-Review

**Spec coverage:**

- Patch A — `subscribe_chat` + watch wiring → Tasks 1, 2.
- Patch B — three-bucket `reconcile_pending` → Task 5.
- Patch C — `PARSERS.md` + editor link → Task 7.
- Patch D — auto-recovery refetch → Task 6.
- Patch E — `invalidate_for_chat` + unwatch wiring → Task 3.
  (Static-file mount in main.py was a "nice-to-have" in the spec;
  dropped from the plan because the GitHub link in Task 7 covers
  the docs-link UX without needing the mount. Re-adding it would be
  ~5 LOC; can ship later if the deployment story demands it.)
- Frontend invalidation — Task 4.
- Verification plan — Task 8.

**Placeholder scan:** every code block has the actual content; no
"implement appropriate handling", no "fill in details". Test
assertions are explicit. Commit messages are written out.

**Type / signature consistency:** `subscribe_chat`, `invalidate_for_chat`,
`_RECONCILE_SLACK_SECONDS`, `_spawn_deferred_reconcile` are referenced
in tests with the exact names defined in the implementation steps.

**Decision log:**

- The static-file mount (`/docs`) was scoped out of the plan — the
  GitHub link in the editor satisfies the docs-link UX without
  introducing a path that 404s in some deploys. Trivial to re-add
  later.
- The `unknown` status (mentioned in early brainstorming) is NOT
  added; the spec settled on "stay as `expired`, change the note +
  add the wait gate." All test assertions read `status == "expired"`.
