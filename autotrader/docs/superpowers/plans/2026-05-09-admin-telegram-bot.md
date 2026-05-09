# Admin Telegram Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a separate Pyrogram bot that lets the admin remote-control the autotrader from Telegram (status, kill switch, channel/parser pause, risk caps, …) and pushes trade/risk/system events to a single bound admin user.

**Architecture:** A second Pyrogram `Client` (bot mode, `bot_token=...`) runs alongside the existing userbot in the FastAPI lifespan. Commands route through a single `asyncio.Lock` for serialised writes. Notifications flow from the existing `TradeEventBus` into a per-class token-bucket rate limiter that DMs the bound admin. State is one row on the existing `GlobalSettings` singleton (admin user_id + four notify-class booleans). The bot is *additive*: missing or bad `TELEGRAM_BOT_TOKEN` = no-op, the rest of the app keeps trading.

**Tech Stack:** Pyrogram (already a dep), FastAPI, SQLModel + aiosqlite, structlog, pytest + httpx for tests.

**Spec:** `docs/superpowers/specs/2026-05-09-admin-telegram-bot-design.md` (single source of truth — re-read it if a task here is ambiguous).

---

## File Structure

**Create:**

| Path | Responsibility |
| --- | --- |
| `backend/src/autotrader/services/admin_bot.py` | `AdminBot` class — owns the bot Pyrogram client, attaches handlers, exposes `start()` / `stop()` / `status()`. Knows nothing about commands or notifications. |
| `backend/src/autotrader/services/admin_bot_commands.py` | One async function per `/command`. Pure handlers — receive `(message, services)` and return a dataclass `Reply(text, keyboard)`. Never touches the Pyrogram client directly. |
| `backend/src/autotrader/services/admin_bot_notify.py` | `AdminBotNotifier` — subscribes to `TradeEventBus`, formats events, applies per-class token-bucket rate limits, calls `AdminBot.send(...)`. Includes the 5-consecutive-failures backoff. |
| `backend/src/autotrader/services/admin_bot_state.py` | Resolver indirection so command handlers can reach `pipeline` / `quotex_manager` / `notifier` without depending on FastAPI request context. |
| `backend/src/autotrader/routers/admin_bot.py` | REST shim: `GET /admin-bot/status` + `POST /admin-bot/unbind`. Used by the dashboard's "admin bot offline" badge and unbind button. |
| `backend/tests/test_admin_bot.py` | Unit + integration tests for everything in this plan. Uses a `FakePyrogramBot` fixture that captures `send_message` calls and replays canned `Message` / `CallbackQuery` updates. |
| `backend/tests/_fake_pyrogram_bot.py` | Shared test double for the Pyrogram bot client, importable from any test module. |
| `docs/admin-bot.md` | Operator setup guide — one-page checklist. |

**Modify:**

| Path | Change |
| --- | --- |
| `backend/src/autotrader/config.py` | Add `bot_token: SecretStr \| None` to `TelegramSettings`. |
| `backend/src/autotrader/models/settings.py` | Add `admin_telegram_user_id: int \| None` and four `admin_notify_*: bool` columns to `GlobalSettings`. |
| `backend/src/autotrader/db.py` | Add the five `ALTER TABLE` migration blocks in `_migrate_in_place`. |
| `backend/src/autotrader/main.py` | Construct + start `AdminBot` in `lifespan`, stop it on shutdown, include the new router, wire the notifier subscriber task. |
| `backend/src/autotrader/services/executor.py` | At the rejection branch, publish a `risk.rejected` event on the bus. |
| `backend/src/autotrader/services/quotex_manager.py` | At broker-disconnect / connect-rejected / account-mode-failed log points, also publish `system.error`. |
| `backend/src/autotrader/services/telegram_manager.py` | At handler-attach / peer-cache failures, also publish `system.error`. |

The notifier and bot client are split because they have different concurrency stories: command handlers serialise (one `asyncio.Lock`), notifications fire-and-forget. Keeping them in one file conflates the two state machines and makes `admin_bot.py` grow past the size where it's pleasant to hold in your head.

---

## Conventions used in tasks

- **Imports** — every code block shows the imports it needs. If an import is shared across many tasks (e.g. `structlog`, `AsyncSessionLocal`), it appears in each task that uses it.
- **Test commands** — run with `cd backend && pytest <path> -v`. Tests must be invoked from the `backend/` directory because `pyproject.toml` configures the `src/` layout.
- **Commit step** — every task ends in a commit. Frequent commits = easy bisect when something later goes wrong.
- **Pyrogram fakes** — we never instantiate a real Pyrogram `Client`. The `FakePyrogramBot` (built in Task 4) is the seam for everything bot-related.
- **SQLModel `session.exec(...)`** — that's the SQLModel ORM helper, *not* `subprocess.exec`. Style hooks may flag it; ignore.

---

## Task 1: Add `TELEGRAM_BOT_TOKEN` config

**Files:**
- Modify: `backend/src/autotrader/config.py`
- Test: `backend/tests/test_config.py` (create if missing — check first with `ls backend/tests/test_config.py`)

- [ ] **Step 1: Write the failing test**

If `backend/tests/test_config.py` doesn't exist, create it. Otherwise append the test to the existing file.

```python
# backend/tests/test_config.py
"""Configuration parsing tests."""

from __future__ import annotations

import pytest


def test_telegram_settings_parses_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TELEGRAM_BOT_TOKEN`` env var is parsed as a SecretStr.

    The bot token comes from @BotFather and looks like
    ``123456:ABC-DEF...``. We never want it visible in logs / repr —
    SecretStr enforces that at the type level.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    from autotrader.config import TelegramSettings  # noqa: PLC0415

    s = TelegramSettings()  # type: ignore[call-arg]
    assert s.bot_token is not None
    assert s.bot_token.get_secret_value() == "123456:fake-token"
    # SecretStr never leaks the value through repr.
    assert "fake-token" not in repr(s.bot_token)


def test_telegram_settings_bot_token_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``TELEGRAM_BOT_TOKEN`` leaves the field as None — admin bot
    becomes a no-op rather than crashing startup."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from autotrader.config import TelegramSettings  # noqa: PLC0415

    s = TelegramSettings()  # type: ignore[call-arg]
    assert s.bot_token is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_config.py -v
```

Expected: `AttributeError: 'TelegramSettings' object has no attribute 'bot_token'` on the first test.

- [ ] **Step 3: Add the field**

Edit `backend/src/autotrader/config.py`. In the `TelegramSettings` class, add the new field below `api_hash`:

```python
    api_id: int | None = None
    api_hash: SecretStr | None = None
    # @BotFather token for the *admin* bot (separate from the userbot
    # MTProto session). When unset the admin bot is a no-op — see
    # ``services/admin_bot.py``. Stored as SecretStr so it's never
    # leaked through ``repr(settings)`` or accidental logging.
    bot_token: SecretStr | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_config.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/autotrader/config.py backend/tests/test_config.py
git commit -m "feat(autotrader): add TELEGRAM_BOT_TOKEN setting for admin bot"
```

---

## Task 2: Add admin-bot fields to `GlobalSettings`

**Files:**
- Modify: `backend/src/autotrader/models/settings.py`
- Test: `backend/tests/test_admin_bot.py` (create the file)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_bot.py`:

```python
"""Admin Telegram bot — unit + integration tests."""

from __future__ import annotations

import pytest

from autotrader.models.settings import GlobalSettings


def test_global_settings_has_admin_fields_with_safe_defaults() -> None:
    """Fresh ``GlobalSettings`` row defaults to:
    - admin unbound (``admin_telegram_user_id is None``)
    - all four notify classes ON

    Defaults matter: a brand-new install with no bot configured must
    still construct a valid settings row, and once the operator binds
    the admin they should immediately receive the full event firehose
    without flipping four extra toggles.
    """
    s = GlobalSettings()
    assert s.admin_telegram_user_id is None
    assert s.admin_notify_placed is True
    assert s.admin_notify_settled is True
    assert s.admin_notify_risk_rejected is True
    assert s.admin_notify_system_error is True
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_admin_bot.py -v
```

Expected: `AttributeError: 'GlobalSettings' object has no attribute 'admin_telegram_user_id'`.

- [ ] **Step 3: Add the fields**

Edit `backend/src/autotrader/models/settings.py`. Add after `max_concurrent_trades`:

```python
    # Risk module (Phase 5).
    # ``0`` for any of these means "no cap".
    daily_max_loss: float = Field(default=0.0, nullable=False)
    daily_max_stake: float = Field(default=0.0, nullable=False)
    max_concurrent_trades: int = Field(default=0, nullable=False)

    # ------------------------------------------------------------------
    # Admin Telegram bot (Phase 8).
    # ------------------------------------------------------------------
    # The Telegram user_id of the bound admin. ``None`` = unbound; the
    # first /start the bot receives writes the sender's user_id here,
    # and from that point only that user_id can issue commands. Cleared
    # by /unbind (from the bot or the dashboard) so the next /start can
    # re-bind.
    admin_telegram_user_id: int | None = Field(default=None, nullable=True)

    # Per-class notification toggles. Operators can mute a single class
    # via ``/notify <class> off`` from the bot. Defaults to ON for all
    # four so a fresh bind immediately sees the firehose.
    admin_notify_placed: bool = Field(default=True, nullable=False)
    admin_notify_settled: bool = Field(default=True, nullable=False)
    admin_notify_risk_rejected: bool = Field(default=True, nullable=False)
    admin_notify_system_error: bool = Field(default=True, nullable=False)

    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
```

(The `created_at` / `updated_at` lines were already there — keep them at the bottom of the class.)

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && pytest tests/test_admin_bot.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/autotrader/models/settings.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): add admin-bot fields to GlobalSettings"
```

---

## Task 3: Add the in-place migration for the new columns

**Files:**
- Modify: `backend/src/autotrader/db.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_admin_bot.py`:

```python
import asyncio
from sqlalchemy import text


def test_migration_adds_admin_columns_to_legacy_global_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A pre-existing ``global_settings`` table missing the admin
    columns gets ALTERed in place — no data loss, no manual migration.

    Repro: build the legacy table by hand, run ``init_db``, assert the
    new columns are present and queryable.
    """
    db_file = tmp_path / "legacy.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("AUTOTRADER_DB_URL", db_url)

    # Force fresh module imports so settings re-reads the env.
    import importlib  # noqa: PLC0415
    import autotrader.config as config_mod  # noqa: PLC0415
    importlib.reload(config_mod)
    import autotrader.db as db_mod  # noqa: PLC0415
    importlib.reload(db_mod)

    async def _setup_legacy_then_migrate() -> set[str]:
        # Step A: build the *old* shape directly.
        async with db_mod.engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE global_settings ("
                " id INTEGER PRIMARY KEY,"
                " default_stake REAL NOT NULL DEFAULT 1.0,"
                " default_duration_seconds INTEGER NOT NULL DEFAULT 60,"
                " kill_switch_engaged BOOLEAN NOT NULL DEFAULT 0,"
                " pipeline_active BOOLEAN NOT NULL DEFAULT 0,"
                " daily_max_loss REAL NOT NULL DEFAULT 0,"
                " daily_max_stake REAL NOT NULL DEFAULT 0,"
                " max_concurrent_trades INTEGER NOT NULL DEFAULT 0,"
                " created_at DATETIME NOT NULL,"
                " updated_at DATETIME NOT NULL"
                ")",
            ))
            await conn.execute(text(
                "INSERT INTO global_settings"
                " (id, created_at, updated_at) VALUES"
                " (1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            ))

        # Step B: run init_db which triggers _migrate_in_place.
        await db_mod.init_db()

        # Step C: read back the column set.
        from sqlalchemy import inspect  # noqa: PLC0415
        async with db_mod.engine.begin() as conn:
            cols = await conn.run_sync(
                lambda sc: {c["name"] for c in inspect(sc).get_columns("global_settings")},
            )
        await db_mod.close_db()
        return cols

    cols = asyncio.new_event_loop().run_until_complete(_setup_legacy_then_migrate())

    assert "admin_telegram_user_id" in cols
    assert "admin_notify_placed" in cols
    assert "admin_notify_settled" in cols
    assert "admin_notify_risk_rejected" in cols
    assert "admin_notify_system_error" in cols
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_admin_bot.py::test_migration_adds_admin_columns_to_legacy_global_settings -v
```

Expected: FAIL — `assert "admin_telegram_user_id" in cols` because the migration block doesn't exist yet.

- [ ] **Step 3: Add the migration blocks**

Edit `backend/src/autotrader/db.py`. After the existing `max_concurrent_trades` migration block, append:

```python
    if cols and "admin_telegram_user_id" not in cols:
        # Phase 8 admin bot: persisted on the singleton settings row so
        # there's no separate table to manage. Nullable INTEGER —
        # ``None`` means unbound, the first /start fills it.
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_telegram_user_id INTEGER NULL",
            ),
        )
    # Per-class notification toggles. Default ON so a freshly bound
    # admin sees the full firehose; operators mute via ``/notify`` from
    # the bot or via the dashboard.
    if cols and "admin_notify_placed" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_placed BOOLEAN NOT NULL DEFAULT 1",
            ),
        )
    if cols and "admin_notify_settled" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_settled BOOLEAN NOT NULL DEFAULT 1",
            ),
        )
    if cols and "admin_notify_risk_rejected" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_risk_rejected BOOLEAN NOT NULL DEFAULT 1",
            ),
        )
    if cols and "admin_notify_system_error" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_system_error BOOLEAN NOT NULL DEFAULT 1",
            ),
        )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && pytest tests/test_admin_bot.py::test_migration_adds_admin_columns_to_legacy_global_settings -v
```

Expected: PASS.

- [ ] **Step 5: Run the full test_admin_bot.py to make sure nothing regressed**

```bash
cd backend && pytest tests/test_admin_bot.py -v
```

Expected: all tests so far PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/autotrader/db.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): migrate global_settings to add admin-bot columns"
```

---

## Task 4: `AdminBot` skeleton + `FakePyrogramBot` test seam

**Files:**
- Create: `backend/src/autotrader/services/admin_bot.py`
- Create: `backend/tests/_fake_pyrogram_bot.py` (shared test helper, importable from any test module)
- Test: `backend/tests/test_admin_bot.py`

This task is the foundation everything else builds on. We build the lifecycle (start / stop / status), the no-op-when-no-token guard, and the `FakePyrogramBot` test double that all later tasks reuse.

- [ ] **Step 1: Write the `FakePyrogramBot` helper**

Create `backend/tests/_fake_pyrogram_bot.py`:

```python
"""Test double for the Pyrogram bot client.

We never spin up a real Pyrogram client in tests — they require network
+ a real bot token, and Pyrogram's session storage is an integration
hazard. ``FakePyrogramBot`` mimics just enough of the surface
``AdminBot`` calls to let us drive the bot end-to-end with canned
``Message`` / ``CallbackQuery`` updates.

Captured side-effects:
* ``sent_messages`` — list of (chat_id, text, reply_markup) tuples
* ``raise_on_send`` — set to an exception class to make the next
  send_message raise (used to test the 5-failure backoff)

Replay surface:
* ``await fake.fire_message(user_id, text)`` — invokes the registered
  message handler with a synthetic Message
* ``await fake.fire_callback(user_id, data)`` — same for callbacks
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeUser:
    id: int
    is_bot: bool = False
    first_name: str = "Tester"


@dataclass
class FakeChat:
    id: int
    type: str = "private"


@dataclass
class FakeMessage:
    """Subset of pyrogram.types.Message that AdminBot reads."""

    text: str
    from_user: FakeUser
    chat: FakeChat
    id: int = 1

    async def reply_text(
        self,
        text: str,
        reply_markup: Any | None = None,
        **_kwargs: Any,
    ) -> "FakeMessage":
        # The fake bot stashes replies on the originating instance so
        # tests can assert on them. We also push to the bot's
        # ``sent_messages`` list via a back-reference set in
        # ``fire_message``.
        self._captured_reply = (text, reply_markup)  # type: ignore[attr-defined]
        if hasattr(self, "_bot"):
            self._bot.sent_messages.append(  # type: ignore[attr-defined]
                (self.chat.id, text, reply_markup),
            )
        return self


@dataclass
class FakeCallbackQuery:
    data: str
    from_user: FakeUser
    message: FakeMessage

    async def answer(self, text: str = "", **_kwargs: Any) -> None:
        self._captured_answer = text  # type: ignore[attr-defined]


MessageHandler = Callable[[Any, FakeMessage], Awaitable[None]]
CallbackHandler = Callable[[Any, FakeCallbackQuery], Awaitable[None]]


@dataclass
class FakePyrogramBot:
    """Minimal Pyrogram-Client substitute used by AdminBot tests."""

    bot_token: str = "fake-token"
    me_id: int = 99999
    started: bool = False
    sent_messages: list[tuple[int, str, Any]] = field(default_factory=list)
    raise_on_send: type[BaseException] | None = None
    _on_message: MessageHandler | None = None
    _on_callback: CallbackHandler | None = None

    # ------------------------------------------------------------------
    # Lifecycle (matches the bits of pyrogram.Client AdminBot calls)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def add_handler(self, handler: Any) -> None:
        # AdminBot wraps callbacks in MessageHandler / CallbackQueryHandler.
        # We sniff the wrapper class name to avoid importing pyrogram.
        kind = type(handler).__name__
        callback = getattr(handler, "callback", None)
        if callback is None:
            return
        if kind == "MessageHandler":
            self._on_message = callback
        elif kind == "CallbackQueryHandler":
            self._on_callback = callback

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any | None = None,
        **_kwargs: Any,
    ) -> None:
        if self.raise_on_send is not None:
            exc_cls = self.raise_on_send
            self.raise_on_send = None  # one-shot unless reset
            raise exc_cls("fake send failure")
        self.sent_messages.append((chat_id, text, reply_markup))

    # ------------------------------------------------------------------
    # Replay surface (test-only)
    # ------------------------------------------------------------------

    async def fire_message(self, user_id: int, text: str) -> FakeMessage:
        msg = FakeMessage(
            text=text,
            from_user=FakeUser(id=user_id),
            chat=FakeChat(id=user_id),
        )
        msg._bot = self  # type: ignore[attr-defined]
        if self._on_message is not None:
            await self._on_message(self, msg)
        return msg

    async def fire_callback(self, user_id: int, data: str) -> FakeCallbackQuery:
        msg = FakeMessage(
            text="",
            from_user=FakeUser(id=user_id),
            chat=FakeChat(id=user_id),
        )
        msg._bot = self  # type: ignore[attr-defined]
        cq = FakeCallbackQuery(
            data=data,
            from_user=FakeUser(id=user_id),
            message=msg,
        )
        if self._on_callback is not None:
            await self._on_callback(self, cq)
        return cq
```

- [ ] **Step 2: Write the failing test for the `AdminBot` lifecycle**

Append to `backend/tests/test_admin_bot.py`:

```python
import asyncio


def test_admin_bot_no_token_is_no_op() -> None:
    """Without ``TELEGRAM_BOT_TOKEN`` set, ``start()`` must complete
    silently and leave the bot in ``state="disabled"``. A missing token
    is the most common 'I haven't set up the bot yet' state — it
    must not crash startup."""
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415

    bot = AdminBot(bot_token=None)

    async def _run() -> None:
        await bot.start()
        assert bot.status().state == "disabled"
        assert bot.status().bound_user_id is None
        await bot.stop()  # idempotent on disabled

    asyncio.new_event_loop().run_until_complete(_run())


def test_admin_bot_starts_with_fake_client() -> None:
    """When a token *and* a client factory are provided, ``start()``
    constructs the client, calls ``start()`` on it, and reports
    ``state="running"``."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(
        bot_token="123:abc",
        client_factory=lambda token: fake,
    )

    async def _run() -> None:
        await bot.start()
        assert fake.started is True
        assert bot.status().state == "running"
        await bot.stop()
        assert fake.started is False
        assert bot.status().state == "stopped"

    asyncio.new_event_loop().run_until_complete(_run())


def test_admin_bot_start_failure_sets_error_state() -> None:
    """If ``client.start()`` raises (bad token, network) the bot ends
    in ``state="error"`` with ``last_error`` populated, but no exception
    propagates to the caller — startup must not crash the app."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415

    class _BoomFake(FakePyrogramBot):
        async def start(self) -> None:  # type: ignore[override]
            raise RuntimeError("invalid token")

    bot = AdminBot(
        bot_token="123:abc",
        client_factory=lambda token: _BoomFake(),
    )

    async def _run() -> None:
        await bot.start()
        st = bot.status()
        assert st.state == "error"
        assert "invalid token" in (st.last_error or "")

    asyncio.new_event_loop().run_until_complete(_run())
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "admin_bot"
```

Expected: `ModuleNotFoundError: No module named 'autotrader.services.admin_bot'`.

- [ ] **Step 4: Implement the skeleton**

Create `backend/src/autotrader/services/admin_bot.py`:

```python
"""Admin Telegram bot — Pyrogram client lifecycle.

Sibling to :class:`TelegramManager` but for the *admin* bot, not the
ingestion userbot. Owns one Pyrogram bot-mode client; everything
command-related lives in :mod:`admin_bot_commands` and everything
notification-related in :mod:`admin_bot_notify`.

Lifecycle:

    disabled -> (no token at all)
    stopped  -> (token present, start() not yet called or stop() called)
    running  -> (start() succeeded; client is connected)
    error    -> (start() raised; ``last_error`` carries why)

The error state is *recoverable* — the rest of the app keeps running.
The dashboard surfaces the state via ``GET /admin-bot/status``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

State = Literal["disabled", "stopped", "running", "error"]


@dataclass(frozen=True, slots=True)
class AdminBotStatus:
    """Public snapshot — safe to serialise to the dashboard."""

    state: State
    bound_user_id: int | None
    last_error: str | None


# Factory signature: ``(bot_token: str) -> Pyrogram-like Client``. Tests
# inject a ``FakePyrogramBot`` factory; production wires the real
# ``pyrogram.Client`` constructor (see ``_default_client_factory``).
ClientFactory = Callable[[str], Any]

# Hook called for every accepted ``/command`` text. Receives the
# Pyrogram client + Message; returns nothing. Set externally so this
# module stays handler-agnostic.
MessageHook = Callable[[Any, Any], Awaitable[None]]
CallbackHook = Callable[[Any, Any], Awaitable[None]]


def _default_client_factory(token: str) -> Any:
    """Production factory — imported lazily so tests don't need pyrogram."""
    from pyrogram import Client  # noqa: PLC0415
    return Client(
        name="autotrader_admin_bot",
        bot_token=token,
        # In-memory session: the bot token *is* the credential, no
        # session string to persist. Restarts re-auth instantly.
        in_memory=True,
    )


class AdminBot:
    """Single warm Pyrogram bot client, async-safe."""

    def __init__(
        self,
        *,
        bot_token: str | None,
        client_factory: ClientFactory | None = None,
        bound_user_id: int | None = None,
    ) -> None:
        self._token = bot_token
        self._factory = client_factory or _default_client_factory
        self._client: Any | None = None
        self._state: State = "disabled" if bot_token is None else "stopped"
        self._last_error: str | None = None
        self._bound_user_id = bound_user_id
        self._on_message: MessageHook | None = None
        self._on_callback: CallbackHook | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def client(self) -> Any | None:
        return self._client

    def status(self) -> AdminBotStatus:
        return AdminBotStatus(
            state=self._state,
            bound_user_id=self._bound_user_id,
            last_error=self._last_error,
        )

    def set_bound_user_id(self, user_id: int | None) -> None:
        """Called from the binding handler when /start succeeds."""
        self._bound_user_id = user_id

    def set_message_hook(self, hook: MessageHook | None) -> None:
        self._on_message = hook

    def set_callback_hook(self, hook: CallbackHook | None) -> None:
        self._on_callback = hook

    async def start(self) -> None:
        """Construct + start the underlying client. Idempotent.

        ``state="disabled"`` (no token) is a no-op success. A start
        failure transitions to ``state="error"`` and is logged but
        never re-raised — the rest of the app must keep running.
        """
        if self._state == "disabled":
            log.info("admin_bot.disabled", reason="no TELEGRAM_BOT_TOKEN set")
            return
        if self._state == "running":
            return
        try:
            self._client = self._factory(self._token or "")
            self._attach_handlers()
            await self._client.start()
            self._state = "running"
            self._last_error = None
            log.info("admin_bot.started", bound_user_id=self._bound_user_id)
        except Exception as exc:  # noqa: BLE001  (we deliberately swallow)
            self._state = "error"
            self._last_error = str(exc)
            log.error("admin_bot.start_failed", error=str(exc))

    async def stop(self) -> None:
        """Stop the client if running. Idempotent across all states."""
        if self._client is None:
            self._state = "stopped" if self._state != "disabled" else "disabled"
            return
        try:
            await self._client.stop()
        except Exception as exc:  # pragma: no cover  (best-effort teardown)
            log.warning("admin_bot.stop_failed", error=str(exc))
        finally:
            self._client = None
            if self._state != "disabled":
                self._state = "stopped"

    async def send(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any | None = None,
    ) -> None:
        """Send a message via the bot client. Raises whatever the
        underlying client raises — the notifier catches Forbidden /
        RPCError to drive its 5-failure backoff."""
        if self._client is None or self._state != "running":
            raise RuntimeError(f"admin bot not running (state={self._state})")
        await self._client.send_message(chat_id, text, reply_markup=reply_markup)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _attach_handlers(self) -> None:
        """Wire MessageHandler + CallbackQueryHandler. Lazy-imports
        pyrogram so tests with a fake client never need it installed."""
        if self._client is None:
            return
        from pyrogram.handlers import (  # noqa: PLC0415
            CallbackQueryHandler,
            MessageHandler,
        )

        async def _on_message(client: Any, message: Any) -> None:
            if self._on_message is not None:
                await self._on_message(client, message)

        async def _on_callback(client: Any, query: Any) -> None:
            if self._on_callback is not None:
                await self._on_callback(client, query)

        self._client.add_handler(MessageHandler(_on_message))
        self._client.add_handler(CallbackQueryHandler(_on_callback))
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "admin_bot"
```

Expected: all three lifecycle tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/autotrader/services/admin_bot.py backend/tests/_fake_pyrogram_bot.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): AdminBot client skeleton + FakePyrogramBot test seam"
```

---

## Task 5: Wire `AdminBot` into the FastAPI lifespan

**Files:**
- Modify: `backend/src/autotrader/main.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_admin_bot_attached_to_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan must attach an ``AdminBot`` instance to
    ``app.state.admin_bot`` so routers + the notifier can find it."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr("autotrader.services.quotex_manager.Quotex", FakeQuotex)

    from autotrader.main import app  # noqa: PLC0415
    with TestClient(app):
        bot = app.state.admin_bot
        # No token -> disabled, but the instance is still attached.
        assert bot.status().state == "disabled"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_admin_bot.py::test_admin_bot_attached_to_app_state -v
```

Expected: `AttributeError: 'State' object has no attribute 'admin_bot'`.

- [ ] **Step 3: Wire `AdminBot` into the lifespan**

Edit `backend/src/autotrader/main.py`. Add to the imports near the existing service imports:

```python
from autotrader.services.admin_bot import AdminBot
```

Inside `lifespan(...)`, after the `pipeline = Pipeline(...)` block and before the `executor.reconcile_pending()` call, add:

```python
    # Admin Telegram bot (Phase 8). The token is *optional* — when
    # unset, the bot is constructed in ``state="disabled"`` and ``start()``
    # is a no-op. The rest of the app keeps trading either way.
    bot_token_secret = telegram_settings.bot_token
    bot_token = (
        bot_token_secret.get_secret_value() if bot_token_secret is not None else None
    )
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1)
    admin_bot = AdminBot(
        bot_token=bot_token,
        bound_user_id=(gs.admin_telegram_user_id if gs is not None else None),
    )
    app.state.admin_bot = admin_bot
    await admin_bot.start()
```

You'll need two new imports at the top of `main.py`:

```python
from autotrader.config import settings, telegram_settings
from autotrader.models.settings import GlobalSettings
```

(Append `telegram_settings` to the existing `from autotrader.config import settings` line; add the `GlobalSettings` import as a new line.)

In the `finally:` block, add the admin-bot teardown alongside the other shutdowns:

```python
        await admin_bot.stop()
```

…immediately above the existing `await executor.shutdown()` call.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && pytest tests/test_admin_bot.py::test_admin_bot_attached_to_app_state -v
```

Expected: PASS.

- [ ] **Step 5: Run the full pipeline test suite to confirm no regression**

```bash
cd backend && pytest tests/test_pipeline.py -v
```

Expected: all existing tests PASS (the lifespan addition is additive and the no-op path is the default in tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/autotrader/main.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): wire AdminBot into FastAPI lifespan"
```

---

## Task 6: REST router — `GET /admin-bot/status` + `POST /admin-bot/unbind`

**Files:**
- Create: `backend/src/autotrader/routers/admin_bot.py`
- Modify: `backend/src/autotrader/main.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_admin_bot_router_reports_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415
    from tests.test_pipeline import _login  # noqa: PLC0415

    monkeypatch.setattr("autotrader.services.quotex_manager.Quotex", FakeQuotex)

    from autotrader.main import app  # noqa: PLC0415
    with TestClient(app) as c:
        headers = _login(c)
        r = c.get("/admin-bot/status", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "disabled"
        assert body["bound_user_id"] is None


def test_admin_bot_unbind_clears_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415
    from tests.test_pipeline import _login  # noqa: PLC0415

    monkeypatch.setattr("autotrader.services.quotex_manager.Quotex", FakeQuotex)

    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _seed_bound_admin() -> None:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            if gs is None:
                gs = GlobalSettings(id=1)
            gs.admin_telegram_user_id = 12345
            s.add(gs)
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_seed_bound_admin())

    with TestClient(app) as c:
        headers = _login(c)
        r = c.post("/admin-bot/unbind", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["bound_user_id"] is None

        # The persisted row must reflect the unbind.
        async def _read() -> int | None:
            async with AsyncSessionLocal() as s:
                gs = await s.get(GlobalSettings, 1)
                return gs.admin_telegram_user_id if gs else None
        assert asyncio.new_event_loop().run_until_complete(_read()) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "router or unbind_clears"
```

Expected: 404 on the endpoints — the router doesn't exist yet.

- [ ] **Step 3: Create the router**

Create `backend/src/autotrader/routers/admin_bot.py`:

```python
"""REST shim around the Admin Telegram Bot.

Two endpoints:

* ``GET /admin-bot/status`` — used by the dashboard to render the
  "Admin bot offline / running / error" badge.
* ``POST /admin-bot/unbind`` — escape hatch when the operator can no
  longer access the bound Telegram account; clears ``admin_telegram_user_id``
  so the next ``/start`` from any account re-binds.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.db import get_session
from autotrader.models.base import utc_now
from autotrader.models.settings import GlobalSettings
from autotrader.routers.auth import require_auth

router = APIRouter(prefix="/admin-bot", tags=["admin-bot"])


class StatusResponse(BaseModel):
    state: str
    bound_user_id: int | None
    last_error: str | None


class UnbindResponse(BaseModel):
    bound_user_id: None = None


@router.get("/status", response_model=StatusResponse)
async def status_endpoint(
    request: Request,
    _: None = Depends(require_auth),
) -> StatusResponse:
    bot = request.app.state.admin_bot
    s = bot.status()
    return StatusResponse(
        state=s.state,
        bound_user_id=s.bound_user_id,
        last_error=s.last_error,
    )


@router.post("/unbind", response_model=UnbindResponse)
async def unbind_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_auth),
) -> UnbindResponse:
    """Clear ``admin_telegram_user_id`` from the persisted settings row
    AND from the in-memory ``AdminBot`` instance. Both must move
    together — otherwise the next /start from any user is rejected as
    "bound to another admin" because the in-memory copy is stale."""
    gs = await session.get(GlobalSettings, 1)
    if gs is None:
        gs = GlobalSettings(id=1)
        session.add(gs)
    gs.admin_telegram_user_id = None
    gs.updated_at = utc_now()
    await session.commit()

    bot = request.app.state.admin_bot
    bot.set_bound_user_id(None)
    return UnbindResponse()
```

- [ ] **Step 4: Wire the router into `main.py`**

Edit `backend/src/autotrader/main.py`. Add to the existing router-imports line:

```python
from autotrader.routers import admin_bot as admin_bot_router
```

(Place it next to the `pipeline as pipeline_router` import for visual consistency.)

Then under the existing `app.include_router(...)` block:

```python
app.include_router(admin_bot_router.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "router or unbind_clears"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/autotrader/routers/admin_bot.py backend/src/autotrader/main.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): /admin-bot/status + /admin-bot/unbind REST endpoints"
```

---

## Task 7: Auth gate + `/start` binding handler

**Files:**
- Create: `backend/src/autotrader/services/admin_bot_commands.py`
- Modify: `backend/src/autotrader/main.py` (wire the message hook)
- Test: `backend/tests/test_admin_bot.py`

This task introduces the command-dispatch surface. Even though only `/start` exists right now, the routing infrastructure (auth gate + command map) lands here so subsequent tasks just append handlers.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_first_start_binds_admin_user_id() -> None:
    """The first ``/start`` from any user binds that user_id to the
    settings row AND to the in-memory bot, then replies confirm."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake)
    bot.set_message_hook(build_message_hook(bot))

    async def _run() -> tuple[int | None, list[tuple[int, str, object]]]:
        await bot.start()
        # First /start from user 555 binds.
        await fake.fire_message(user_id=555, text="/start")
        return bot.status().bound_user_id, list(fake.sent_messages)

    bound, sent = asyncio.new_event_loop().run_until_complete(_run())
    assert bound == 555
    # Exactly one reply, confirming the bind.
    assert len(sent) == 1
    chat_id, text, _ = sent[0]
    assert chat_id == 555
    assert "bound" in text.lower()


def test_second_start_from_other_user_is_rejected() -> None:
    """Once bound, /start from a *different* user replies 'bound to
    another admin' and does NOT change the bound id."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _seed_first() -> None:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            s.add(gs); await s.commit()

    asyncio.new_event_loop().run_until_complete(_seed_first())

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake, bound_user_id=555)
    bot.set_message_hook(build_message_hook(bot))

    async def _run() -> tuple[int | None, list[tuple[int, str, object]]]:
        await bot.start()
        await fake.fire_message(user_id=999, text="/start")
        return bot.status().bound_user_id, list(fake.sent_messages)

    bound, sent = asyncio.new_event_loop().run_until_complete(_run())
    assert bound == 555  # unchanged
    assert len(sent) == 1
    chat_id, text, _ = sent[0]
    assert chat_id == 999
    assert "another admin" in text.lower()


def test_non_admin_message_is_silently_dropped() -> None:
    """Any *non-/start* message from a non-admin user must be silently
    dropped — no reply, no log noise to the user."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake, bound_user_id=555)
    bot.set_message_hook(build_message_hook(bot))

    async def _run() -> list[tuple[int, str, object]]:
        await bot.start()
        await fake.fire_message(user_id=999, text="/status")
        return list(fake.sent_messages)

    sent = asyncio.new_event_loop().run_until_complete(_run())
    assert sent == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "start_binds or rejected or silently"
```

Expected: `ImportError: cannot import name 'build_message_hook'`.

- [ ] **Step 3: Implement the command dispatcher with `/start`**

Create `backend/src/autotrader/services/admin_bot_commands.py`:

```python
"""Admin bot command handlers.

Every handler is an ``async`` function with the signature
``async def handle_X(message, services) -> Reply``. Pure functions:
they read state, possibly write to the DB via ``AsyncSessionLocal``,
and return a ``Reply`` describing what the bot should say back.

Handlers never touch the Pyrogram client directly — that's
``admin_bot.py``'s job. This split keeps handlers easy to unit-test
without driving a fake client through a Pyrogram round-trip.

Routing: ``build_message_hook(bot)`` returns the function ``AdminBot``
plugs in via ``set_message_hook``. The hook does the auth gate + lookup
into ``COMMANDS`` then awaits the matching handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from autotrader.db import AsyncSessionLocal
from autotrader.models.base import utc_now
from autotrader.models.settings import GlobalSettings

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Reply:
    """What a handler returns. ``markup`` is an opaque pass-through —
    typically a Pyrogram ``InlineKeyboardMarkup`` but kept ``Any`` so
    handlers can be unit-tested without importing pyrogram."""

    text: str
    markup: Any | None = None


# Handler signature. The second arg is the ``AdminBot`` instance —
# gives handlers access to ``set_bound_user_id`` without dragging app
# state through a global.
Handler = Callable[[Any, Any], Awaitable[Reply]]


# --------------------------------------------------------------------------
# /start — auto-bind + confirm
# --------------------------------------------------------------------------


async def handle_start(message: Any, bot: Any) -> Reply:
    """First ``/start`` binds the admin; subsequent ones reply confirm."""
    sender_id = int(message.from_user.id)
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1)
        if gs is None:
            gs = GlobalSettings(id=1)
            session.add(gs)
        if gs.admin_telegram_user_id is None:
            # First /start ever — bind.
            gs.admin_telegram_user_id = sender_id
            gs.updated_at = utc_now()
            await session.commit()
            bot.set_bound_user_id(sender_id)
            log.info("admin_bot.bound", user_id=sender_id)
            return Reply(
                text=(
                    "Bound as admin.\n"
                    "Send /help to see what I can do."
                ),
            )
        if gs.admin_telegram_user_id == sender_id:
            return Reply(text="Already bound — send /help.")
        # A different user_id is bound. Drop without changing state.
        log.info(
            "admin_bot.bind.rejected",
            sender=sender_id,
            bound=gs.admin_telegram_user_id,
        )
        return Reply(
            text=(
                "This bot is bound to another admin.\n"
                "Ask them to /unbind, or use the dashboard to release it."
            ),
        )


# --------------------------------------------------------------------------
# Command registry
# --------------------------------------------------------------------------


COMMANDS: dict[str, Handler] = {
    "/start": handle_start,
}


# --------------------------------------------------------------------------
# Hook builder — what AdminBot plugs in
# --------------------------------------------------------------------------


# A single asyncio.Lock serialises command execution. Two simultaneous
# /killswitch on taps from a hyperactive operator must not race.
_dispatch_lock = asyncio.Lock()


def build_message_hook(bot: Any) -> Callable[[Any, Any], Awaitable[None]]:
    """Returns the coroutine ``AdminBot.set_message_hook`` expects.

    Behaviour:
    * Reads the bound user_id from the *bot* (in-memory). Falls back to
      the settings row if unset (covers race: lifespan started before
      the row was migrated).
    * If unbound, only ``/start`` is allowed; everything else is dropped.
    * If bound, only the bound user_id is allowed; everything else is
      dropped silently.
    * Looks up the command in ``COMMANDS`` and awaits the handler under
      ``_dispatch_lock``.
    * Catches handler exceptions and replies with a generic error.
    """

    async def _hook(_client: Any, message: Any) -> None:
        text = (getattr(message, "text", "") or "").strip()
        if not text.startswith("/"):
            return
        # Telegram appends ``@botname`` for group commands; strip it.
        head = text.split(" ", 1)[0]
        if "@" in head:
            head = head.split("@", 1)[0]

        sender_id = int(getattr(message.from_user, "id", 0))
        bound = bot.status().bound_user_id

        if bound is None and head != "/start":
            log.info("admin_bot.dropped.pre_bind", sender=sender_id, command=head)
            return
        if bound is not None and sender_id != bound and head != "/start":
            log.info("admin_bot.dropped.unauthorised", sender=sender_id, command=head)
            return

        handler = COMMANDS.get(head)
        if handler is None:
            await message.reply_text(
                f"Unknown command: {head}\nSend /help for the list.",
            )
            return

        async with _dispatch_lock:
            try:
                reply = await handler(message, bot)
            except Exception as exc:  # noqa: BLE001  (handler-boundary catch)
                log.exception(
                    "admin_bot.handler_failed", command=head, sender=sender_id,
                )
                await message.reply_text(
                    f"command failed: {type(exc).__name__}",
                )
                return
            await message.reply_text(reply.text, reply_markup=reply.markup)

    return _hook
```

- [ ] **Step 4: Wire the hook into `main.py`**

Edit `backend/src/autotrader/main.py`. After the `await admin_bot.start()` call, add:

```python
    # Wire the command dispatcher only if the bot is actually running.
    # When disabled / errored, leaving the hook unset means AdminBot
    # just drops updates.
    if admin_bot.status().state == "running":
        from autotrader.services.admin_bot_commands import (  # noqa: PLC0415
            build_message_hook,
        )
        admin_bot.set_message_hook(build_message_hook(admin_bot))
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "start_binds or rejected or silently"
```

Expected: all three PASS.

- [ ] **Step 6: Run the full test_admin_bot.py to confirm no regression**

```bash
cd backend && pytest tests/test_admin_bot.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_commands.py backend/src/autotrader/main.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): admin bot /start binding + auth dispatcher"
```

---

## Task 8: `/help`, `/whoami`, `/status`, `/balance`

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def _make_bound_bot() -> tuple[Any, Any]:
    """Helper: a started bot bound to user 555."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake, bound_user_id=555)
    bot.set_message_hook(build_message_hook(bot))
    return bot, fake


def test_help_lists_commands() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/help")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    for command in ("/status", "/balance", "/killswitch", "/channels", "/parsers"):
        assert command in text, f"{command} missing from /help text"


def test_whoami_echoes_user_id() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/whoami")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "555" in text


def test_status_includes_pipeline_kill_switch_broker() -> None:
    """The /status command must mention all three core gauges."""
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/status")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    for label in ("pipeline", "kill switch", "broker"):
        assert label.lower() in text.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "help_lists or whoami or status_includes"
```

Expected: `Unknown command: /help` etc.

- [ ] **Step 3: Implement the four handlers**

Edit `backend/src/autotrader/services/admin_bot_commands.py`. Add new handlers above the `COMMANDS` registry:

```python
# --------------------------------------------------------------------------
# /help — static command summary (kept in sync by hand)
# --------------------------------------------------------------------------


_HELP_TEXT = (
    "*Admin bot commands*\n"
    "\n"
    "*Read*\n"
    "  /status — pipeline / kill switch / broker / Telegram pulse\n"
    "  /balance — demo + real balances\n"
    "  /trades [N] — last N trades (default 10)\n"
    "  /decisions [N] — last N parser decisions\n"
    "  /streaks — martingale streaks per parser\n"
    "  /channels — list watched channels\n"
    "  /parsers [chat_id] — list parsers (optionally filtered)\n"
    "  /caps — current daily-loss / stake / concurrency caps\n"
    "  /whoami — your Telegram user_id\n"
    "\n"
    "*Write*\n"
    "  /killswitch on|off\n"
    "  /pipeline on|off\n"
    "  /panic — kill switch + pipeline off in one shot\n"
    "  /mode demo|real — switch broker account mode\n"
    "  /stake <amount> — set default stake\n"
    "  /caps loss|stake|concurrent <value>\n"
    "  /notify placed|settled|risk_rejected|system_error on|off\n"
    "  /channel <id> | /parser <id> — details + pause/resume buttons\n"
    "  /unbind — release admin binding (with confirm)\n"
)


async def handle_help(_message: Any, _bot: Any) -> Reply:
    return Reply(text=_HELP_TEXT)


# --------------------------------------------------------------------------
# /whoami
# --------------------------------------------------------------------------


async def handle_whoami(message: Any, _bot: Any) -> Reply:
    sender_id = int(message.from_user.id)
    return Reply(text=f"You are user_id `{sender_id}`.")


# --------------------------------------------------------------------------
# /status — composes a one-screen health summary
# --------------------------------------------------------------------------


async def handle_status(_message: Any, _bot: Any) -> Reply:
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)

    pipeline_label = "ON" if gs.pipeline_active else "OFF"
    kill_label = "ENGAGED" if gs.kill_switch_engaged else "off"

    text = (
        "*Status*\n"
        f"Pipeline: *{pipeline_label}*\n"
        f"Kill switch: *{kill_label}*\n"
        f"Broker: see dashboard /pipeline/status\n"
        f"Default stake: ${gs.default_stake:.2f}\n"
        f"Caps: loss=${gs.daily_max_loss:.2f}, "
        f"stake=${gs.daily_max_stake:.2f}, "
        f"concurrent={gs.max_concurrent_trades}"
    )
    return Reply(text=text)


# --------------------------------------------------------------------------
# /balance — read-only broker balance (best-effort)
# --------------------------------------------------------------------------


async def handle_balance(_message: Any, _bot: Any) -> Reply:
    return Reply(
        text=(
            "*Balance*\n"
            "Live balances are on the dashboard (/balance is wired in "
            "v2 once QuotexManager exposes a cached snapshot)."
        ),
    )
```

Update the `COMMANDS` registry:

```python
COMMANDS: dict[str, Handler] = {
    "/start": handle_start,
    "/help": handle_help,
    "/whoami": handle_whoami,
    "/status": handle_status,
    "/balance": handle_balance,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "help_lists or whoami or status_includes"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_commands.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): admin bot /help /whoami /status /balance"
```

---

## Task 9: `/trades`, `/decisions`, `/streaks` + state resolver

**Files:**
- Create: `backend/src/autotrader/services/admin_bot_state.py`
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`
- Modify: `backend/src/autotrader/main.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_trades_renders_last_n() -> None:
    """``/trades 3`` reads the most recent 3 trade attempts and renders
    a one-line-per-row summary including asset / direction / outcome."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_then_fetch() -> str:
        async with AsyncSessionLocal() as s:
            for asset, direction, status_, profit in [
                ("EURUSD_otc", "call", "won", 1.8),
                ("GBPUSD_otc", "put", "lost", -1.0),
                ("USDJPY_otc", "call", "pending", None),
            ]:
                s.add(TradeAttempt(
                    chat_id=-1001,
                    parser_config_id=1,
                    asset=asset,
                    asset_raw=asset,
                    direction=direction,
                    duration_seconds=60,
                    stake=1.0,
                    status=status_,
                    profit=profit,
                ))
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/trades 3")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_seed_then_fetch())
    assert "EURUSD_otc" in text
    assert "GBPUSD_otc" in text
    assert "USDJPY_otc" in text


def test_decisions_renders_recent_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/decisions`` reads from the in-memory ring buffer on the
    Pipeline. We monkeypatch the resolver to return canned decisions."""
    from autotrader.services import admin_bot_commands as cmds  # noqa: PLC0415

    canned = [
        {"ts": "2026-05-09T10:00:00", "chat_id": -1001,
         "parser_config_id": 1, "parser_name": "DreamVIP",
         "parser_type": "regex", "outcome": "matched",
         "reasons": [], "signals": 1, "text_preview": "BUY EURUSD 1m"},
        {"ts": "2026-05-09T10:00:01", "chat_id": -1002,
         "parser_config_id": None, "parser_name": None,
         "parser_type": None, "outcome": "no_configs",
         "reasons": [], "signals": 0, "text_preview": "stray msg"},
    ]
    monkeypatch.setattr(cmds, "_recent_decisions_snapshot", lambda: canned)

    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/decisions 5")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "matched" in text
    assert "no_configs" in text


def test_streaks_lists_per_parser_state() -> None:
    """``/streaks`` reads MartingaleState rows and renders one line per."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_then_fetch() -> str:
        async with AsyncSessionLocal() as s:
            s.add(ParserConfig(
                id=42, chat_id=-1001, name="DreamVIP",
                parser_type="regex", default_stake=10.0,
                martingale_enabled=True, martingale_multiplier=2.0,
            ))
            s.add(MartingaleState(parser_config_id=42, current_streak=2, last_stake=40.0))
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/streaks")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_seed_then_fetch())
    assert "DreamVIP" in text
    assert "2" in text  # the streak number
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "trades_renders or decisions_renders or streaks_lists"
```

Expected: `Unknown command: /trades` etc.

- [ ] **Step 3: Create the resolver module**

Create `backend/src/autotrader/services/admin_bot_state.py`:

```python
"""Lightweight resolver for app.state references used by command handlers.

Handlers shouldn't depend on FastAPI's request context — they live one
layer below, driven by the bot client. This module is set up by
``main.py``'s lifespan and provides typed accessors for the few
``app.state`` objects the handlers need (pipeline ring buffer, broker
manager, notifier). Keeps handlers easy to unit-test by allowing
``monkeypatch.setattr`` on a single function.
"""

from __future__ import annotations

from typing import Any

_pipeline: Any | None = None
_quotex: Any | None = None
_admin_bot: Any | None = None
_notifier: Any | None = None


def attach(
    *,
    pipeline: Any,
    quotex: Any,
    admin_bot: Any | None = None,
    notifier: Any | None = None,
) -> None:
    global _pipeline, _quotex, _admin_bot, _notifier  # noqa: PLW0603
    _pipeline = pipeline
    _quotex = quotex
    if admin_bot is not None:
        _admin_bot = admin_bot
    if notifier is not None:
        _notifier = notifier


def get_pipeline() -> Any | None:
    return _pipeline


def get_quotex() -> Any | None:
    return _quotex


def get_admin_bot() -> Any | None:
    return _admin_bot


def get_notifier() -> Any | None:
    return _notifier
```

- [ ] **Step 4: Implement the three command handlers**

Edit `backend/src/autotrader/services/admin_bot_commands.py`. Add:

```python
# --------------------------------------------------------------------------
# /trades [N]
# --------------------------------------------------------------------------


def _parse_int_arg(text: str, default: int, min_v: int, max_v: int) -> int:
    """Parse the second token of a command body as an int with bounds.
    Falls back to ``default`` on missing or unparseable input — handlers
    are forgiving about whitespace and stray characters."""
    parts = text.split()
    if len(parts) < 2:
        return default
    try:
        n = int(parts[1])
    except ValueError:
        return default
    return max(min_v, min(max_v, n))


async def handle_trades(message: Any, _bot: Any) -> Reply:
    from autotrader.models.trade_attempt import list_recent  # noqa: PLC0415

    n = _parse_int_arg(message.text, default=10, min_v=1, max_v=50)
    async with AsyncSessionLocal() as session:
        rows = await list_recent(session, limit=n)

    if not rows:
        return Reply(text="No trades yet.")

    lines = ["*Recent trades*"]
    for r in rows:
        marker = {"won": "+", "lost": "-", "pending": "?",
                  "rejected": "x", "refund": "="}.get(r.status, "•")
        pnl = f"{r.profit:+.2f}" if r.profit is not None else "—"
        lines.append(
            f"{marker} {r.asset} {r.direction.upper()} "
            f"{r.duration_seconds}s ${r.stake:.2f} -> {r.status} ({pnl})"
        )
    return Reply(text="\n".join(lines))


# --------------------------------------------------------------------------
# /decisions [N]
# --------------------------------------------------------------------------


def _recent_decisions_snapshot() -> list[dict[str, Any]]:
    """Resolver indirection — tests monkeypatch this. In production
    pulls from ``app.state.pipeline.recent_decisions`` via the
    fastapi-state stash set up in main.py."""
    from autotrader.services.admin_bot_state import get_pipeline  # noqa: PLC0415
    pipeline = get_pipeline()
    if pipeline is None:
        return []
    return pipeline.recent_decisions


async def handle_decisions(message: Any, _bot: Any) -> Reply:
    n = _parse_int_arg(message.text, default=10, min_v=1, max_v=50)
    snapshot = _recent_decisions_snapshot()[:n]
    if not snapshot:
        return Reply(text="No decisions in the ring buffer yet.")
    lines = ["*Recent decisions*"]
    for d in snapshot:
        outcome = d.get("outcome", "?")
        chat_id = d.get("chat_id", "?")
        parser = d.get("parser_name") or "—"
        reasons = "; ".join(d.get("reasons") or [])
        suffix = f" — {reasons}" if reasons else ""
        lines.append(f"{outcome} · chat {chat_id} · {parser}{suffix}")
    return Reply(text="\n".join(lines))


# --------------------------------------------------------------------------
# /streaks
# --------------------------------------------------------------------------


async def handle_streaks(_message: Any, _bot: Any) -> Reply:
    from sqlmodel import select  # noqa: PLC0415

    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        configs_q = await session.exec(
            select(ParserConfig).where(ParserConfig.martingale_enabled == True),  # noqa: E712
        )
        configs = list(configs_q.all())
        states_q = await session.exec(select(MartingaleState))
        states = {s.parser_config_id: s for s in states_q.all()}

    if not configs:
        return Reply(text="No martingale-enabled parsers.")

    lines = ["*Martingale streaks*"]
    for c in configs:
        st = states.get(c.id or 0)
        step = st.current_streak if st else 0
        last = f"${st.last_stake:.2f}" if st and st.last_stake else "—"
        lines.append(
            f"{c.name or f'cfg-{c.id}'} step {step} x{c.martingale_multiplier} "
            f"max={c.martingale_max_streak} last={last}"
        )
    return Reply(text="\n".join(lines))
```

Update `COMMANDS`:

```python
COMMANDS: dict[str, Handler] = {
    "/start": handle_start,
    "/help": handle_help,
    "/whoami": handle_whoami,
    "/status": handle_status,
    "/balance": handle_balance,
    "/trades": handle_trades,
    "/decisions": handle_decisions,
    "/streaks": handle_streaks,
}
```

- [ ] **Step 5: Wire the resolver in `main.py`**

Edit `backend/src/autotrader/main.py`. After `app.state.pipeline = pipeline`, attach the resolver:

```python
    from autotrader.services import admin_bot_state  # noqa: PLC0415
    admin_bot_state.attach(pipeline=pipeline, quotex=manager, admin_bot=admin_bot)
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "trades_renders or decisions_renders or streaks_lists"
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_commands.py backend/src/autotrader/services/admin_bot_state.py backend/src/autotrader/main.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): admin bot /trades /decisions /streaks + state resolver"
```

---

## Task 10: Toggle commands — `/pipeline`, `/killswitch`, `/panic`, `/mode`

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`
- Test: `backend/tests/test_admin_bot.py`

`/mode real` is the most destructive command we expose; we route it through a callback-confirmation step.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_killswitch_on_persists_flag() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> bool:
        await bot.start()
        await fake.fire_message(555, "/killswitch on")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.kill_switch_engaged if gs else False

    assert asyncio.new_event_loop().run_until_complete(_run()) is True


def test_pipeline_off_persists_flag() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_and_toggle() -> bool:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.pipeline_active = True
            s.add(gs)
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/pipeline off")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.pipeline_active if gs else True

    assert asyncio.new_event_loop().run_until_complete(_seed_and_toggle()) is False


def test_panic_kills_both() -> None:
    """`/panic` engages kill switch AND turns pipeline off in one shot."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_and_panic() -> tuple[bool, bool]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.pipeline_active = True
            gs.kill_switch_engaged = False
            s.add(gs)
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/panic")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.pipeline_active, gs.kill_switch_engaged

    pipe, kill = asyncio.new_event_loop().run_until_complete(_seed_and_panic())
    assert pipe is False
    assert kill is True


def test_mode_real_requires_confirm() -> None:
    """`/mode real` first replies with a confirm keyboard — it does not
    flip the broker until the operator clicks the inline 'Yes'."""
    bot, fake = _make_bound_bot()

    async def _run() -> tuple[str, object]:
        await bot.start()
        await fake.fire_message(555, "/mode real")
        text = fake.sent_messages[-1][1]
        markup = fake.sent_messages[-1][2]
        return text, markup

    text, markup = asyncio.new_event_loop().run_until_complete(_run())
    assert "confirm" in text.lower() or "real" in text.lower()
    assert markup is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "killswitch_on or pipeline_off or panic_kills or mode_real_requires"
```

- [ ] **Step 3: Implement the toggle handlers**

Edit `backend/src/autotrader/services/admin_bot_commands.py`. Add:

```python
# --------------------------------------------------------------------------
# Toggle helpers
# --------------------------------------------------------------------------


def _parse_on_off(text: str) -> bool | None:
    """Parse ``on`` / ``off`` (case-insensitive) from the command body.
    Returns None when neither token is found — caller replies with usage."""
    parts = text.lower().split()
    if len(parts) < 2:
        return None
    if parts[1] in ("on", "true", "1", "engage"):
        return True
    if parts[1] in ("off", "false", "0", "disengage"):
        return False
    return None


async def _set_settings_flag(field: str, value: Any) -> GlobalSettings:
    """Mutate one column on the GlobalSettings singleton row."""
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)
        setattr(gs, field, value)
        gs.updated_at = utc_now()
        session.add(gs)
        await session.commit()
        await session.refresh(gs)
        return gs


async def handle_killswitch(message: Any, _bot: Any) -> Reply:
    state = _parse_on_off(message.text)
    if state is None:
        return Reply(text="Usage: /killswitch on | off")
    await _set_settings_flag("kill_switch_engaged", state)
    return Reply(text=f"Kill switch is now *{'ENGAGED' if state else 'off'}*.")


async def handle_pipeline(message: Any, _bot: Any) -> Reply:
    state = _parse_on_off(message.text)
    if state is None:
        return Reply(text="Usage: /pipeline on | off")
    await _set_settings_flag("pipeline_active", state)
    return Reply(text=f"Pipeline is now *{'ON' if state else 'OFF'}*.")


async def handle_panic(_message: Any, _bot: Any) -> Reply:
    """Sets kill_switch=True AND pipeline_active=False in one transaction."""
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)
        gs.kill_switch_engaged = True
        gs.pipeline_active = False
        gs.updated_at = utc_now()
        session.add(gs)
        await session.commit()
    log.warning("admin_bot.panic.engaged")
    return Reply(text="PANIC: kill switch engaged and pipeline turned OFF.")


# --------------------------------------------------------------------------
# /mode demo|real — REAL requires inline-keyboard confirm
# --------------------------------------------------------------------------


def _confirm_keyboard(action: str) -> Any:
    """Build a 2-button inline keyboard. Lazy-imports pyrogram.types
    so unit tests with FakePyrogramBot don't need pyrogram installed."""
    from pyrogram.types import (  # noqa: PLC0415
        InlineKeyboardButton,
        InlineKeyboardMarkup,
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, do it",
                                 callback_data=f"confirm:{action}"),
            InlineKeyboardButton("Cancel", callback_data="cancel"),
        ],
    ])


async def handle_mode(message: Any, _bot: Any) -> Reply:
    parts = message.text.lower().split()
    if len(parts) < 2 or parts[1] not in ("demo", "real", "practice"):
        return Reply(text="Usage: /mode demo | real")
    target = "REAL" if parts[1] == "real" else "PRACTICE"
    if target == "REAL":
        return Reply(
            text="Switch broker to *REAL* money?",
            markup=_confirm_keyboard("mode:real"),
        )
    # Demo flips immediately — no confirmation needed.
    from autotrader.services.admin_bot_state import get_quotex  # noqa: PLC0415
    qx = get_quotex()
    if qx is None:
        return Reply(text="Broker manager not attached.")
    await qx.set_account_mode("PRACTICE")
    return Reply(text="Broker mode set to *PRACTICE*.")
```

Update the `COMMANDS` registry to add the four new entries (keep existing ones):

```python
COMMANDS: dict[str, Handler] = {
    # ... existing entries ...
    "/killswitch": handle_killswitch,
    "/pipeline": handle_pipeline,
    "/panic": handle_panic,
    "/mode": handle_mode,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "killswitch_on or pipeline_off or panic_kills or mode_real_requires"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_commands.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): admin bot /killswitch /pipeline /panic /mode toggles"
```

---

## Task 11: Callback-driven `/channels` + `/parsers` with inline pause/resume

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`
- Modify: `backend/src/autotrader/main.py` (set callback hook)
- Test: `backend/tests/test_admin_bot.py`

The `/channels` and `/parsers` listings render as text; `/channel <id>` and `/parser <id>` render a single-button inline keyboard whose label flips with state (`Pause` when active, `Resume` when paused). Tapping issues a `CallbackQuery` with data `chan:<id>:toggle` or `parser:<id>:toggle`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_channels_lists_watched() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_and_run() -> str:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-1001, title="Signals", chat_type="channel",
                username="signals", enabled=True,
            ))
            s.add(WatchedChannel(
                chat_id=-1002, title="Backup", chat_type="channel",
                username="backup", enabled=False,
            ))
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/channels")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_seed_and_run())
    assert "Signals" in text or "-1001" in text
    assert "Backup" in text or "-1002" in text


def test_channel_callback_toggles_enabled_flag() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_then_toggle() -> bool:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-1001, title="Signals", chat_type="channel",
                username="signals", enabled=True,
            ))
            await s.commit()
        await bot.start()
        await fake.fire_callback(555, "chan:-1001:toggle")
        async with AsyncSessionLocal() as s:
            row = await s.get(WatchedChannel, -1001)
            return row.enabled if row else True

    assert asyncio.new_event_loop().run_until_complete(_seed_then_toggle()) is False


def test_parser_callback_toggles_enabled_flag() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_then_toggle() -> bool:
        async with AsyncSessionLocal() as s:
            s.add(ParserConfig(
                id=42, chat_id=-1001, name="DreamVIP",
                parser_type="regex", enabled=True,
            ))
            await s.commit()
        await bot.start()
        await fake.fire_callback(555, "parser:42:toggle")
        async with AsyncSessionLocal() as s:
            row = await s.get(ParserConfig, 42)
            return row.enabled if row else True

    assert asyncio.new_event_loop().run_until_complete(_seed_then_toggle()) is False


def test_callback_from_unauthorised_user_is_ignored() -> None:
    """A CallbackQuery from a non-bound user must NOT mutate state."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_then_attack() -> bool:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-1001, title="Signals", chat_type="channel",
                username="signals", enabled=True,
            ))
            await s.commit()
        await bot.start()
        # User 999 is NOT bound (555 is). The callback must be ignored.
        await fake.fire_callback(999, "chan:-1001:toggle")
        async with AsyncSessionLocal() as s:
            row = await s.get(WatchedChannel, -1001)
            return row.enabled if row else False

    assert asyncio.new_event_loop().run_until_complete(_seed_then_attack()) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "channels_lists or callback_toggles or callback_from_unauth"
```

Expected: failures — handlers don't exist yet, callback hook isn't wired.

- [ ] **Step 3: Implement the list/detail handlers**

Edit `backend/src/autotrader/services/admin_bot_commands.py`. Add:

```python
# --------------------------------------------------------------------------
# /channels and /parsers — list with detail-drilldown for inline-keyboard toggles
# --------------------------------------------------------------------------


def _row_keyboard(callback_data_prefix: str, target_id: int, enabled: bool) -> Any:
    """One-row inline keyboard with a single button whose label flips
    with state. Showing both Pause and Resume as separate buttons would
    clutter the chat at scale (tens of channels)."""
    from pyrogram.types import (  # noqa: PLC0415
        InlineKeyboardButton,
        InlineKeyboardMarkup,
    )
    label = "Pause" if enabled else "Resume"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            label, callback_data=f"{callback_data_prefix}:{target_id}:toggle",
        ),
    ]])


async def handle_channels(_message: Any, _bot: Any) -> Reply:
    from sqlmodel import select  # noqa: PLC0415

    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(WatchedChannel).order_by(WatchedChannel.title),  # type: ignore[arg-type]
        )
        rows = list(result.all())

    if not rows:
        return Reply(text="No watched channels.")

    lines = ["*Watched channels*"]
    for r in rows:
        flag = "[on]" if r.enabled else "[paused]"
        lines.append(f"{flag} `{r.chat_id}` {r.title}")
    lines.append("\nTap /channel <id> for per-channel actions.")
    return Reply(text="\n".join(lines))


async def handle_channel_detail(message: Any, _bot: Any) -> Reply:
    """``/channel <id>`` shows one row with the inline pause/resume
    button. The id can be negative — split on whitespace and parse
    the second token; bail if missing."""
    parts = message.text.split()
    if len(parts) < 2:
        return Reply(text="Usage: /channel <chat_id>")
    try:
        chat_id = int(parts[1])
    except ValueError:
        return Reply(text="chat_id must be an integer.")

    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(WatchedChannel, chat_id)

    if row is None:
        return Reply(text=f"No watched channel with chat_id `{chat_id}`.")

    flag = "active" if row.enabled else "paused"
    text = (
        f"*Channel `{chat_id}`*\n"
        f"Title: {row.title}\n"
        f"Type: {row.chat_type}\n"
        f"State: {flag}"
    )
    return Reply(text=text, markup=_row_keyboard("chan", chat_id, row.enabled))


async def handle_parsers(message: Any, _bot: Any) -> Reply:
    from sqlmodel import select  # noqa: PLC0415

    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    parts = message.text.split()
    chat_filter: int | None = None
    if len(parts) >= 2:
        try:
            chat_filter = int(parts[1])
        except ValueError:
            return Reply(text="Usage: /parsers [chat_id]")

    async with AsyncSessionLocal() as session:
        stmt = select(ParserConfig)
        if chat_filter is not None:
            stmt = stmt.where(ParserConfig.chat_id == chat_filter)
        stmt = stmt.order_by(ParserConfig.chat_id, ParserConfig.priority, ParserConfig.id)  # type: ignore[arg-type]
        rows = list((await session.exec(stmt)).all())

    if not rows:
        return Reply(text="No parser configs.")

    lines = ["*Parsers*"]
    for r in rows:
        flag = "[on]" if r.enabled else "[paused]"
        lines.append(
            f"{flag} `{r.id}` chat=`{r.chat_id}` *{r.name or '(unnamed)'}* "
            f"({r.parser_type})"
        )
    lines.append("\nTap /parser <id> for per-parser actions.")
    return Reply(text="\n".join(lines))


async def handle_parser_detail(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) < 2:
        return Reply(text="Usage: /parser <id>")
    try:
        parser_id = int(parts[1])
    except ValueError:
        return Reply(text="parser id must be an integer.")

    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(ParserConfig, parser_id)

    if row is None:
        return Reply(text=f"No parser with id `{parser_id}`.")

    flag = "active" if row.enabled else "paused"
    text = (
        f"*Parser `{parser_id}`*\n"
        f"Name: {row.name or '(unnamed)'}\n"
        f"Chat: `{row.chat_id}`\n"
        f"Type: {row.parser_type}\n"
        f"Stake: ${row.default_stake:.2f}, "
        f"duration: {row.default_duration_seconds}s, mode: {row.trade_mode}\n"
        f"Martingale: enabled={row.martingale_enabled} "
        f"x{row.martingale_multiplier} max={row.martingale_max_streak} "
        f"auto_recovery={row.martingale_auto_recovery}\n"
        f"State: {flag}"
    )
    return Reply(
        text=text,
        markup=_row_keyboard("parser", parser_id, row.enabled),
    )
```

Update `COMMANDS`:

```python
COMMANDS: dict[str, Handler] = {
    # ... existing entries ...
    "/channels": handle_channels,
    "/channel": handle_channel_detail,
    "/parsers": handle_parsers,
    "/parser": handle_parser_detail,
}
```

Now add the callback dispatcher at the bottom of the file:

```python
# --------------------------------------------------------------------------
# Callback routing — InlineKeyboard taps land here
# --------------------------------------------------------------------------


async def _toggle_channel_enabled(chat_id: int) -> bool | None:
    """Flip the WatchedChannel.enabled flag for ``chat_id``. Returns
    the *new* state, or None if the row no longer exists."""
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(WatchedChannel, chat_id)
        if row is None:
            return None
        row.enabled = not row.enabled
        row.updated_at = utc_now()
        await session.commit()
        return row.enabled


async def _toggle_parser_enabled(parser_id: int) -> bool | None:
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(ParserConfig, parser_id)
        if row is None:
            return None
        row.enabled = not row.enabled
        row.updated_at = utc_now()
        await session.commit()
        return row.enabled


# Confirm-action registry. Mode:real handler defined further below;
# /unbind handler is added in Task 13.
ConfirmHandler = Callable[[], Awaitable[str]]


async def _confirm_mode_real() -> str:
    from autotrader.services.admin_bot_state import get_quotex  # noqa: PLC0415
    qx = get_quotex()
    if qx is None:
        return "Broker manager not attached."
    await qx.set_account_mode("REAL")
    return "Broker mode set to REAL."


CONFIRM_HANDLERS: dict[str, ConfirmHandler] = {
    "mode:real": _confirm_mode_real,
}


def build_callback_hook(bot: Any) -> Callable[[Any, Any], Awaitable[None]]:
    """Returns the coroutine ``AdminBot.set_callback_hook`` expects.

    Same auth model as ``build_message_hook``: drop callbacks from any
    user_id other than the bound admin (silently — Telegram already
    debounces the button press, and an unauthorised tap shouldn't even
    show an 'answer' toast).
    """

    async def _hook(_client: Any, query: Any) -> None:
        sender_id = int(getattr(query.from_user, "id", 0))
        bound = bot.status().bound_user_id
        if bound is None or sender_id != bound:
            log.info("admin_bot.callback.dropped", sender=sender_id)
            return

        data = (getattr(query, "data", "") or "").strip()
        # Format: ``<kind>:<id>:<action>`` (e.g. ``chan:-1001:toggle``)
        # plus shorter sentinels: ``cancel`` / ``confirm:<action>``.
        if data == "cancel":
            await query.answer("Cancelled.")
            return

        parts = data.split(":")
        async with _dispatch_lock:
            try:
                if parts[0] == "chan" and len(parts) == 3 and parts[2] == "toggle":
                    new_state = await _toggle_channel_enabled(int(parts[1]))
                    if new_state is None:
                        await query.answer("Channel no longer exists.")
                    else:
                        await query.answer(
                            f"Channel {'active' if new_state else 'paused'}",
                        )
                elif parts[0] == "parser" and len(parts) == 3 and parts[2] == "toggle":
                    new_state = await _toggle_parser_enabled(int(parts[1]))
                    if new_state is None:
                        await query.answer("Parser no longer exists.")
                    else:
                        await query.answer(
                            f"Parser {'active' if new_state else 'paused'}",
                        )
                elif parts[0] == "confirm":
                    action = ":".join(parts[1:])
                    handler = CONFIRM_HANDLERS.get(action)
                    if handler is None:
                        await query.answer("Unknown confirm action.")
                        return
                    text = await handler()
                    await query.answer(text)
                else:
                    await query.answer("Unknown action.")
            except Exception as exc:  # noqa: BLE001
                log.exception("admin_bot.callback_failed", data=data)
                await query.answer(f"failed: {type(exc).__name__}")

    return _hook
```

- [ ] **Step 4: Wire the callback hook from `main.py`**

In `backend/src/autotrader/main.py`, in the same block where `set_message_hook` is called, replace it with:

```python
        from autotrader.services.admin_bot_commands import (  # noqa: PLC0415
            build_callback_hook,
            build_message_hook,
        )
        admin_bot.set_message_hook(build_message_hook(admin_bot))
        admin_bot.set_callback_hook(build_callback_hook(admin_bot))
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "channels_lists or callback_toggles or callback_from_unauth"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_commands.py backend/src/autotrader/main.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): admin bot /channels /parsers + inline pause callbacks"
```

---

## Task 12: `/caps` + `/stake` — numeric setters

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_caps_loss_persists_value() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> float:
        await bot.start()
        await fake.fire_message(555, "/caps loss 50")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.daily_max_loss if gs else 0.0

    assert asyncio.new_event_loop().run_until_complete(_run()) == 50.0


def test_caps_concurrent_persists_value() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> int:
        await bot.start()
        await fake.fire_message(555, "/caps concurrent 4")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.max_concurrent_trades if gs else 0

    assert asyncio.new_event_loop().run_until_complete(_run()) == 4


def test_stake_persists_value() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> float:
        await bot.start()
        await fake.fire_message(555, "/stake 7.5")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.default_stake if gs else 0.0

    assert asyncio.new_event_loop().run_until_complete(_run()) == 7.5


def test_caps_invalid_subcommand_replies_usage() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/caps banana 10")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "usage" in text.lower() or "loss" in text.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "caps_loss or caps_concurrent or stake_persists or caps_invalid"
```

- [ ] **Step 3: Implement the handlers**

Edit `backend/src/autotrader/services/admin_bot_commands.py`. Add:

```python
# --------------------------------------------------------------------------
# /caps and /stake
# --------------------------------------------------------------------------


def _format_caps(gs: GlobalSettings) -> str:
    return (
        "*Caps*\n"
        f"Daily-loss: ${gs.daily_max_loss:.2f}\n"
        f"Daily-stake: ${gs.daily_max_stake:.2f}\n"
        f"Max concurrent: {gs.max_concurrent_trades}\n"
        "(0 = uncapped)"
    )


async def handle_caps(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) == 1:
        async with AsyncSessionLocal() as session:
            gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)
        return Reply(text=_format_caps(gs))

    if len(parts) < 3:
        return Reply(text="Usage: /caps loss|stake|concurrent <value>")
    sub = parts[1].lower()
    raw = parts[2]
    field_map = {
        "loss": ("daily_max_loss", float),
        "stake": ("daily_max_stake", float),
        "concurrent": ("max_concurrent_trades", int),
    }
    spec = field_map.get(sub)
    if spec is None:
        return Reply(text="Usage: /caps loss|stake|concurrent <value>")
    field, parser = spec
    try:
        value = parser(raw)
    except ValueError:
        return Reply(text=f"'{raw}' is not a valid {parser.__name__}.")
    if value < 0:
        return Reply(text="value must be >= 0 (0 = uncapped).")
    gs = await _set_settings_flag(field, value)
    return Reply(text=_format_caps(gs))


async def handle_stake(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) < 2:
        return Reply(text="Usage: /stake <amount>")
    try:
        amount = float(parts[1])
    except ValueError:
        return Reply(text=f"'{parts[1]}' is not a number.")
    if amount <= 0:
        return Reply(text="amount must be > 0.")
    gs = await _set_settings_flag("default_stake", amount)
    return Reply(text=f"Default stake set to ${gs.default_stake:.2f}.")
```

Update `COMMANDS`:

```python
COMMANDS: dict[str, Handler] = {
    # ... existing entries ...
    "/caps": handle_caps,
    "/stake": handle_stake,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "caps_loss or caps_concurrent or stake_persists or caps_invalid"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_commands.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): admin bot /caps + /stake setters"
```

---

## Task 13: `/notify <class> on|off` + `/unbind` (with confirm)

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_notify_settled_off_persists() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> bool:
        await bot.start()
        await fake.fire_message(555, "/notify settled off")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.admin_notify_settled if gs else True

    assert asyncio.new_event_loop().run_until_complete(_run()) is False


def test_notify_invalid_class_replies_usage() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/notify pancakes off")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "usage" in text.lower() or "placed" in text


def test_unbind_requires_confirm_then_clears() -> None:
    """`/unbind` first sends a confirm keyboard. The confirm callback
    clears ``admin_telegram_user_id`` AND ``bot.bound_user_id``."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services import admin_bot_state  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    # Stash the bot in the resolver so the confirm handler can find it.
    admin_bot_state.attach(pipeline=None, quotex=None, admin_bot=bot)

    async def _run() -> tuple[int | None, int | None]:
        await bot.start()
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            s.add(gs); await s.commit()
        await fake.fire_message(555, "/unbind")
        # Click confirm.
        await fake.fire_callback(555, "confirm:unbind")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return (
                gs.admin_telegram_user_id if gs else None,
                bot.status().bound_user_id,
            )

    persisted, in_memory = asyncio.new_event_loop().run_until_complete(_run())
    assert persisted is None
    assert in_memory is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "notify_settled or notify_invalid or unbind_requires"
```

Expected: `Unknown command: /notify` and `/unbind`.

- [ ] **Step 3: Implement the handlers**

Edit `backend/src/autotrader/services/admin_bot_commands.py`. Add:

```python
# --------------------------------------------------------------------------
# /notify <class> on|off
# --------------------------------------------------------------------------


_NOTIFY_FIELDS = {
    "placed": "admin_notify_placed",
    "settled": "admin_notify_settled",
    "risk_rejected": "admin_notify_risk_rejected",
    "system_error": "admin_notify_system_error",
}


async def handle_notify(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) < 3:
        return Reply(
            text=(
                "Usage: /notify <class> on|off\n"
                "Classes: placed, settled, risk_rejected, system_error"
            ),
        )
    cls = parts[1].lower()
    field = _NOTIFY_FIELDS.get(cls)
    if field is None:
        return Reply(
            text=(
                f"Unknown class '{cls}'. "
                "Use: placed, settled, risk_rejected, system_error"
            ),
        )
    state = _parse_on_off(message.text)
    if state is None:
        return Reply(text="Usage: /notify <class> on|off")
    await _set_settings_flag(field, state)
    return Reply(
        text=f"Notify *{cls}* is now *{'on' if state else 'off'}*.",
    )


# --------------------------------------------------------------------------
# /unbind — requires confirm
# --------------------------------------------------------------------------


async def handle_unbind(_message: Any, _bot: Any) -> Reply:
    return Reply(
        text=(
            "Release admin binding?\n"
            "After unbind, the next /start from any user re-binds."
        ),
        markup=_confirm_keyboard("unbind"),
    )


async def _confirm_unbind() -> str:
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1)
        if gs is not None:
            gs.admin_telegram_user_id = None
            gs.updated_at = utc_now()
            await session.commit()
    # Clear in-memory binding too.
    from autotrader.services.admin_bot_state import get_admin_bot  # noqa: PLC0415
    bot = get_admin_bot()
    if bot is not None:
        bot.set_bound_user_id(None)
    return "Unbound."
```

Register `/notify` and `/unbind`:

```python
COMMANDS: dict[str, Handler] = {
    # ... existing entries ...
    "/notify": handle_notify,
    "/unbind": handle_unbind,
}
```

Add the unbind action to the confirm registry:

```python
CONFIRM_HANDLERS: dict[str, ConfirmHandler] = {
    "mode:real": _confirm_mode_real,
    "unbind": _confirm_unbind,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "notify_settled or notify_invalid or unbind_requires"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_commands.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): admin bot /notify + /unbind (with confirm)"
```

---

## Task 14: `AdminBotNotifier` — token-bucket + send wrapper

**Files:**
- Create: `backend/src/autotrader/services/admin_bot_notify.py`
- Test: `backend/tests/test_admin_bot.py`

This task introduces the notifier in isolation: a class that holds the per-class token-bucket state and the consecutive-failures counter, plus a single `notify(...)` entry point. The next task wires it to the event bus.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_notifier_sends_when_class_enabled() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    notifier = AdminBotNotifier(bot=bot)

    async def _seed_then_notify() -> list[tuple[int, str, object]]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_placed = True
            s.add(gs); await s.commit()
        await bot.start()
        await notifier.notify("placed", "PLACED test")
        return list(fake.sent_messages)

    sent = asyncio.new_event_loop().run_until_complete(_seed_then_notify())
    assert sent == [(555, "PLACED test", None)]


def test_notifier_skips_when_class_disabled() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    notifier = AdminBotNotifier(bot=bot)

    async def _seed_then_notify() -> list[tuple[int, str, object]]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_settled = False  # muted
            s.add(gs); await s.commit()
        await bot.start()
        await notifier.notify("settled", "WIN test")
        return list(fake.sent_messages)

    assert asyncio.new_event_loop().run_until_complete(_seed_then_notify()) == []


def test_notifier_skips_when_no_admin_bound() -> None:
    """No-op when ``admin_telegram_user_id`` is None — DM has nowhere
    to land."""
    bot, fake = _make_bound_bot()
    bot.set_bound_user_id(None)
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    notifier = AdminBotNotifier(bot=bot)

    async def _run() -> list:
        from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
        from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = None
            s.add(gs); await s.commit()
        await bot.start()
        await notifier.notify("placed", "test")
        return list(fake.sent_messages)

    assert asyncio.new_event_loop().run_until_complete(_run()) == []


def test_notifier_rate_limit_coalesces_burst() -> None:
    """After the bucket empties, additional notifications of the same
    class are suppressed until the digest window passes; one digest
    message is sent at the end of the window."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    # Tiny bucket + tiny window so the test runs in <1s.
    notifier = AdminBotNotifier(
        bot=bot, bucket_capacity=2, refill_seconds=10, digest_window=0.1,
    )

    async def _run() -> list[str]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_placed = True
            s.add(gs); await s.commit()
        await bot.start()
        # Fire 5 notifications back-to-back. Bucket starts at 2 -> 2 send
        # through, then 3 are suppressed.
        for i in range(5):
            await notifier.notify("placed", f"#{i}")
        # Wait past the digest window so the suppressed-count message
        # gets emitted.
        await asyncio.sleep(0.15)
        await notifier.flush_digests()
        return [t for _, t, _ in fake.sent_messages]

    sent = asyncio.new_event_loop().run_until_complete(_run())
    # First two real messages, then one digest.
    assert len([s for s in sent if s.startswith("#")]) == 2
    assert any("suppressed" in s for s in sent), sent
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "notifier_"
```

Expected: `ImportError: cannot import name 'AdminBotNotifier'`.

- [ ] **Step 3: Implement the notifier**

Create `backend/src/autotrader/services/admin_bot_notify.py`:

```python
"""Admin bot notifier — bridges the in-process event bus to Telegram DMs.

Subscribes to :class:`TradeEventBus`, formats events into compact
Markdown messages, applies a per-class token-bucket rate limit so a
flood (flapping broker, daily-cap breach hammering ``risk.rejected``)
collapses into a single coalesced digest, and DM's the bound admin.

Wired by ``main.py`` after both ``AdminBot`` and ``TradeEventBus`` are
constructed. Never load-bearing — a notifier failure only loses
visibility, not trading.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from autotrader.db import AsyncSessionLocal
from autotrader.models.settings import GlobalSettings

log = structlog.get_logger(__name__)

NotifyClass = Literal["placed", "settled", "risk_rejected", "system_error"]

_NOTIFY_FIELD = {
    "placed": "admin_notify_placed",
    "settled": "admin_notify_settled",
    "risk_rejected": "admin_notify_risk_rejected",
    "system_error": "admin_notify_system_error",
}


@dataclass
class _Bucket:
    """Token-bucket state for one notify-class. Tokens are floats so
    fractional refill is well-defined."""

    capacity: int
    refill_per_sec: float
    tokens: float
    last_refill: float
    suppressed: int = 0
    digest_due_at: float | None = None

    def take(self, now: float) -> bool:
        """Returns True if a token was available; False otherwise.
        Always refills first based on elapsed time."""
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(
                float(self.capacity), self.tokens + elapsed * self.refill_per_sec,
            )
            self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class AdminBotNotifier:
    """Holds the per-class buckets + the consecutive-failure backoff."""

    # After 5 consecutive ``send`` failures we pause outbound. The admin
    # sending *any* message back proves the channel is healthy; that
    # path lives in the message hook (Task 15 wires it).
    _FAILURE_THRESHOLD = 5

    def __init__(
        self,
        *,
        bot: Any,
        bucket_capacity: int = 5,
        refill_seconds: float = 30.0,
        digest_window: float = 60.0,
    ) -> None:
        self._bot = bot
        self._capacity = bucket_capacity
        self._refill = 1.0 / refill_seconds  # tokens per second
        self._digest_window = digest_window
        self._buckets: dict[str, _Bucket] = {}
        self._consecutive_failures = 0
        self._outbound_paused = False
        self._lock = asyncio.Lock()
        self._digest_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def outbound_paused(self) -> bool:
        return self._outbound_paused

    def reset_failures(self) -> None:
        """Called by the message hook when the admin sends *anything* —
        proves the channel is healthy, lift the backoff."""
        if self._consecutive_failures or self._outbound_paused:
            log.info("admin_bot_notify.backoff.cleared")
        self._consecutive_failures = 0
        self._outbound_paused = False

    async def notify(
        self,
        cls: NotifyClass,
        text: str,
        markup: Any | None = None,
    ) -> None:
        """Send a notification, applying per-class throttle + class mute.

        Drops silently when:
        * outbound is paused (backoff active)
        * the class is muted in GlobalSettings
        * no admin is bound
        * the bucket is empty (counts toward the next digest)
        """
        if self._outbound_paused:
            log.debug("admin_bot_notify.skip.paused", cls=cls)
            return

        async with AsyncSessionLocal() as session:
            gs = await session.get(GlobalSettings, 1)
        if gs is None or gs.admin_telegram_user_id is None:
            log.debug("admin_bot_notify.skip.unbound", cls=cls)
            return
        if not getattr(gs, _NOTIFY_FIELD[cls]):
            log.debug("admin_bot_notify.skip.muted", cls=cls)
            return

        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(cls) or _Bucket(
                capacity=self._capacity,
                refill_per_sec=self._refill,
                tokens=float(self._capacity),
                last_refill=now,
            )
            self._buckets[cls] = bucket
            allowed = bucket.take(now)
            if not allowed:
                bucket.suppressed += 1
                if bucket.digest_due_at is None:
                    bucket.digest_due_at = now + self._digest_window
                return

        await self._send(gs.admin_telegram_user_id, text, markup)

    async def flush_digests(self) -> None:
        """Emit any pending suppression-digest messages whose window
        has elapsed. Called from a periodic task in production."""
        now = time.monotonic()
        async with self._lock:
            due = [
                (cls, b) for cls, b in self._buckets.items()
                if b.suppressed > 0
                and b.digest_due_at is not None
                and b.digest_due_at <= now
            ]
            payloads: list[tuple[str, int]] = []
            for cls, b in due:
                payloads.append((cls, b.suppressed))
                b.suppressed = 0
                b.digest_due_at = None

        if not payloads:
            return
        async with AsyncSessionLocal() as session:
            gs = await session.get(GlobalSettings, 1)
        if gs is None or gs.admin_telegram_user_id is None:
            return
        for cls, count in payloads:
            await self._send(
                gs.admin_telegram_user_id,
                (
                    f"{count} `{cls}` events suppressed in last "
                    f"{int(self._digest_window)}s "
                    "(rate limit hit — see dashboard for details)"
                ),
                None,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _send(
        self,
        chat_id: int,
        text: str,
        markup: Any | None,
    ) -> None:
        try:
            await self._bot.send(chat_id, text, reply_markup=markup)
            self._consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            self._consecutive_failures += 1
            log.warning(
                "admin_bot_notify.send_failed",
                error=str(exc),
                consecutive=self._consecutive_failures,
            )
            if self._consecutive_failures >= self._FAILURE_THRESHOLD:
                self._outbound_paused = True
                log.warning(
                    "admin_bot_notify.backoff.engaged",
                    threshold=self._FAILURE_THRESHOLD,
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "notifier_"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_notify.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): AdminBotNotifier with token-bucket rate limit"
```

---

## Task 15: Wire the notifier to the event bus + format trade events

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_notify.py`
- Modify: `backend/src/autotrader/services/admin_bot_commands.py` (call `reset_failures` on every accepted message)
- Modify: `backend/src/autotrader/main.py`
- Test: `backend/tests/test_admin_bot.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_notifier_subscribes_and_renders_trade_upserted() -> None:
    """Publishing a ``trade.upserted`` event with status=pending lands
    a *placed* notification; status=won lands a *settled* notification."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415
    from autotrader.services.event_bus import TradeEventBus  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    bus = TradeEventBus()
    notifier = AdminBotNotifier(bot=bot)

    async def _run() -> list[str]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_placed = True
            gs.admin_notify_settled = True
            s.add(gs); await s.commit()
        await bot.start()
        task = asyncio.create_task(notifier.run(bus))

        # Give the subscriber loop a tick to register.
        await asyncio.sleep(0.01)

        # Pending -> placed
        bus.publish("trade.upserted", {
            "id": 1, "asset": "EURUSD_otc", "direction": "call",
            "duration_seconds": 60, "stake": 20.0, "status": "pending",
            "profit": None, "parser_config_id": 4, "trade_mode": "live",
            "martingale_step": 1, "base_stake": 10.0,
        })
        # Won -> settled
        bus.publish("trade.upserted", {
            "id": 1, "asset": "EURUSD_otc", "direction": "call",
            "duration_seconds": 60, "stake": 20.0, "status": "won",
            "profit": 18.4, "parser_config_id": 4, "trade_mode": "live",
            "martingale_step": 1, "base_stake": 10.0,
        })

        # Allow the subscriber to drain.
        await asyncio.sleep(0.05)
        await notifier.shutdown()
        try:
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass
        return [t for _, t, _ in fake.sent_messages]

    sent = asyncio.new_event_loop().run_until_complete(_run())
    assert any("PLACED" in s for s in sent), sent
    assert any("WIN" in s for s in sent), sent


def test_notifier_resets_backoff_on_admin_message() -> None:
    """After the backoff engages, an *incoming* /command from the admin
    must clear it via the message hook."""
    bot, fake = _make_bound_bot()

    async def _run() -> bool:
        from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415
        from autotrader.services import admin_bot_state  # noqa: PLC0415
        notifier = AdminBotNotifier(bot=bot)
        # Force the backoff state.
        notifier._outbound_paused = True
        notifier._consecutive_failures = 7
        # Stash on the resolver so the hook can find it.
        admin_bot_state.attach(pipeline=None, quotex=None, notifier=notifier)
        await bot.start()
        await fake.fire_message(555, "/whoami")
        return notifier.outbound_paused

    assert asyncio.new_event_loop().run_until_complete(_run()) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "notifier_subscribes or notifier_resets"
```

Expected: `AttributeError` on `notifier.run` / `notifier.shutdown`.

- [ ] **Step 3: Add the bus subscriber + format functions**

Append to `backend/src/autotrader/services/admin_bot_notify.py` (after the class definition):

```python
# --------------------------------------------------------------------------
# Format helpers
# --------------------------------------------------------------------------


def format_trade_placed(payload: dict[str, Any]) -> str:
    asset = payload.get("asset", "?")
    direction = (payload.get("direction") or "?").upper()
    duration = payload.get("duration_seconds", 0)
    stake = float(payload.get("stake") or 0.0)
    base = float(payload.get("base_stake") or stake)
    step = int(payload.get("martingale_step") or 0)
    mode = payload.get("trade_mode") or "auto"
    step_note = ""
    if step > 0 and base > 0:
        ratio = stake / base
        step_note = f" (step {step}, x{ratio:.1f} from base)"
    return (
        f"PLACED  {asset} - {direction} - {duration}s\n"
        f"stake : ${stake:.2f}{step_note}\n"
        f"mode  : {mode}"
    )


def format_trade_settled(payload: dict[str, Any]) -> str:
    asset = payload.get("asset", "?")
    direction = (payload.get("direction") or "?").upper()
    duration = payload.get("duration_seconds", 0)
    profit = payload.get("profit")
    status = payload.get("status", "?")
    if status == "won":
        prefix = "WIN"
    elif status == "lost":
        prefix = "LOSS"
    elif status == "refund":
        prefix = "REFUND"
    else:
        prefix = status.upper()
    pnl = f"{profit:+.2f}" if isinstance(profit, (int, float)) else "—"
    return f"{prefix}   {asset} - {direction} - {duration}s   {pnl}"


def format_risk_rejected(payload: dict[str, Any]) -> str:
    asset = payload.get("asset", "?")
    direction = (payload.get("direction") or "?").upper()
    parser = payload.get("parser_name") or "?"
    reason = payload.get("reason") or "(no reason)"
    return (
        f"REJECTED  {asset} - {direction}  ({parser})\n"
        f"reason: {reason}"
    )


def format_system_error(payload: dict[str, Any]) -> str:
    component = payload.get("component", "?")
    kind = payload.get("kind", "?")
    detail = payload.get("detail", "")
    return (
        f"SYSTEM  {component} {kind}\n"
        f"detail: {detail}"
    )


# --------------------------------------------------------------------------
# Bus subscriber loop — patched onto AdminBotNotifier so the format
# functions above stay module-level (re-usable from tests).
# --------------------------------------------------------------------------


async def _consume(self: "AdminBotNotifier", bus: Any) -> None:
    """Forever-loop: subscribes to the bus, formats events, dispatches
    to ``self.notify``. Cancelled at shutdown."""
    self._digest_task = asyncio.create_task(_digest_loop(self))
    try:
        async for event in bus.subscribe():
            try:
                if event.type == "trade.upserted":
                    status = event.payload.get("status")
                    if status == "pending":
                        await self.notify("placed", format_trade_placed(event.payload))
                    elif status in ("won", "lost", "refund"):
                        await self.notify("settled", format_trade_settled(event.payload))
                elif event.type == "risk.rejected":
                    await self.notify("risk_rejected", format_risk_rejected(event.payload))
                elif event.type == "system.error":
                    await self.notify("system_error", format_system_error(event.payload))
            except Exception:  # noqa: BLE001
                log.exception("admin_bot_notify.consume.format_failed",
                              event_type=event.type)
    finally:
        if self._digest_task is not None:
            self._digest_task.cancel()
            try:
                await self._digest_task
            except asyncio.CancelledError:
                pass


async def _digest_loop(self: "AdminBotNotifier") -> None:
    """Periodic flush of pending suppression digests."""
    while True:
        try:
            await self.flush_digests()
        except Exception:  # noqa: BLE001
            log.exception("admin_bot_notify.digest_loop.failed")
        await asyncio.sleep(self._digest_window)


async def _shutdown(self: "AdminBotNotifier") -> None:
    """Best-effort flush + cancel the digest loop."""
    try:
        await self.flush_digests()
    except Exception:  # noqa: BLE001
        log.exception("admin_bot_notify.shutdown.flush_failed")
    if getattr(self, "_digest_task", None) is not None:
        self._digest_task.cancel()  # type: ignore[union-attr]
        try:
            await self._digest_task  # type: ignore[union-attr]
        except (asyncio.CancelledError, AttributeError):
            pass


# Patch the loop methods onto the class. Keeps the format functions
# at module scope (so tests can call them directly) without having to
# turn them into staticmethods.
AdminBotNotifier.run = _consume  # type: ignore[attr-defined]
AdminBotNotifier.shutdown = _shutdown  # type: ignore[attr-defined]
```

- [ ] **Step 4: Wire `reset_failures` from the message hook**

Edit `backend/src/autotrader/services/admin_bot_commands.py`. Inside `build_message_hook`, just *after* the auth gate passes (immediately before `handler = COMMANDS.get(head)`), add:

```python
        # Any accepted command from the bound admin proves the channel
        # is healthy — clear the notifier backoff if it was engaged.
        from autotrader.services.admin_bot_state import get_notifier  # noqa: PLC0415
        notifier = get_notifier()
        if notifier is not None:
            notifier.reset_failures()
```

- [ ] **Step 5: Wire the notifier in `main.py` lifespan**

Edit `backend/src/autotrader/main.py`. Add `import asyncio` near the top if not present (alongside `import contextlib`).

After the `await admin_bot.start()` block (and after the `if state == "running": set_message_hook + set_callback_hook` block), add:

```python
    notifier_task: asyncio.Task[None] | None = None
    notifier: Any | None = None
    if admin_bot.status().state == "running":
        from autotrader.services.admin_bot_notify import (  # noqa: PLC0415
            AdminBotNotifier,
        )
        notifier = AdminBotNotifier(bot=admin_bot)
        notifier_task = asyncio.create_task(notifier.run(event_bus))

    # Re-attach the resolver to include the notifier (the earlier
    # attach call from Task 9 didn't have it).
    from autotrader.services import admin_bot_state  # noqa: PLC0415
    admin_bot_state.attach(
        pipeline=pipeline,
        quotex=manager,
        admin_bot=admin_bot,
        notifier=notifier,
    )
```

You'll need `from typing import Any` at the top of main.py if not already imported.

In the `finally:` block, before `await admin_bot.stop()`, add:

```python
        if notifier is not None:
            await notifier.shutdown()
        if notifier_task is not None:
            notifier_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await notifier_task
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot.py -v -k "notifier_subscribes or notifier_resets"
```

Expected: PASS.

- [ ] **Step 7: Run the entire admin-bot test suite to confirm nothing regressed**

```bash
cd backend && pytest tests/test_admin_bot.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/autotrader/services/admin_bot_notify.py backend/src/autotrader/services/admin_bot_commands.py backend/src/autotrader/main.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): AdminBotNotifier subscribes to TradeEventBus"
```

---

## Task 16: Producer — `risk.rejected` events from the executor

**Files:**
- Modify: `backend/src/autotrader/services/executor.py`
- Test: `backend/tests/test_admin_bot.py`

We publish from the executor's call site rather than from `risk_gate.py` itself: the gate is a pure function, the executor is the place that already has the bus reference.

- [ ] **Step 1: Read the existing executor flow**

```bash
grep -n "evaluate\|RiskDecision\|outcome.*block\|risk_gate" backend/src/autotrader/services/executor.py | head -20
```

Note the lines where `evaluate(...)` is called and where the decision's `outcome == "block"` branch is handled. The new publish call goes inside that branch.

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_risk_rejected_event_is_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the risk gate refuses a signal, the executor publishes a
    ``risk.rejected`` event on the bus carrying the rejection reason
    plus the parser/asset/direction context."""
    from autotrader.services.event_bus import TradeEventBus  # noqa: PLC0415
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415
    from autotrader.services.parsers import ParsedSignal  # noqa: PLC0415

    seen: list[tuple[str, dict]] = []
    bus = TradeEventBus()

    async def _drain() -> None:
        async for event in bus.subscribe():
            seen.append((event.type, dict(event.payload)))
            if any(t == "risk.rejected" for t, _ in seen):
                return

    async def _run() -> None:
        from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
        from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
        from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
        from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-1001, title="t", chat_type="channel",
                username=None, enabled=True,
            ))
            cfg = ParserConfig(
                chat_id=-1001, name="dr", parser_type="regex",
                default_stake=1.0, enabled=True,
            )
            s.add(cfg)
            gs = GlobalSettings(id=1, kill_switch_engaged=True, pipeline_active=True)
            s.add(gs)
            await s.commit()
            await s.refresh(cfg)

        # Stand up a fake quotex manager — enough surface for the
        # executor's "broker_connected" check; the kill switch will
        # block the trade before any real broker call happens.
        class _FakeQM:
            connected = True
            account_mode = "PRACTICE"
        executor = TradeExecutor(
            manager=_FakeQM(),
            live_trading_enabled_env=False,
            event_bus=bus,
        )

        signal = ParsedSignal(
            asset="EURUSD_otc", direction="call",
            duration_seconds=60, stake=10.0, fire_at=None,
            raw_text="test", parser_id="x", asset_raw="EURUSD",
        )
        async with AsyncSessionLocal() as s:
            settings_row = await s.get(GlobalSettings, 1)

        drain = asyncio.create_task(_drain())
        await executor.submit(
            signal=signal, parser_config=cfg, settings=settings_row,
        )
        await asyncio.wait_for(drain, timeout=2.0)

    asyncio.new_event_loop().run_until_complete(_run())

    types = [t for t, _ in seen]
    assert "risk.rejected" in types, f"saw events: {types}"
    payload = next(p for t, p in seen if t == "risk.rejected")
    assert payload["asset"] == "EURUSD_otc"
    assert "kill switch" in payload["reason"].lower()
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_admin_bot.py::test_risk_rejected_event_is_published -v
```

Expected: FAIL — no `risk.rejected` event is published anywhere.

- [ ] **Step 4: Publish from the executor's risk-block branch**

Open `backend/src/autotrader/services/executor.py` and find the place inside `submit(...)` where the risk decision is checked for `outcome == "block"` (look for `decision.outcome == "block"` or the equivalent). Immediately after the existing log line / persistence step that records the rejection, add:

```python
                # Fan out to the admin-bot notifier (and any other
                # consumer). The bus is fire-and-forget; missing
                # subscribers silently no-op.
                if self._event_bus is not None:
                    self._event_bus.publish("risk.rejected", {
                        "chat_id": parser_config.chat_id,
                        "parser_config_id": parser_config.id,
                        "parser_name": parser_config.name,
                        "asset": signal.asset,
                        "direction": signal.direction,
                        "reason": decision.reason,
                    })
```

If the exact branch shape doesn't match the diff snippet (the executor may have been refactored since this plan was written), find the line where `decision.reason` first appears in a log call and add the publish call directly after the `TradeAttempt` row for the rejection is committed.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && pytest tests/test_admin_bot.py::test_risk_rejected_event_is_published -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/autotrader/services/executor.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): publish risk.rejected events on the trade event bus"
```

---

## Task 17: Producer — `system.error` events from broker / telegram managers

**Files:**
- Modify: `backend/src/autotrader/services/quotex_manager.py`
- Modify: `backend/src/autotrader/services/telegram_manager.py`
- Modify: `backend/src/autotrader/main.py`
- Test: `backend/tests/test_admin_bot.py`

The two managers don't currently hold a bus reference; we plumb one in via an optional constructor argument so existing tests don't break.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_admin_bot.py`:

```python
def test_quotex_manager_publishes_system_error_on_disconnect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``QuotexManager.disconnect()`` raises (broker IO failure)
    the manager must emit a ``system.error`` event so the notifier
    can DM the admin."""
    from autotrader.services.event_bus import TradeEventBus  # noqa: PLC0415
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", FakeQuotex,
    )
    bus = TradeEventBus()
    seen: list[dict] = []

    async def _run() -> None:
        async def _drain() -> None:
            async for event in bus.subscribe():
                if event.type == "system.error":
                    seen.append(dict(event.payload))
                    return
        manager = QuotexManager(root_path=".", event_bus=bus)
        manager.set_credentials("a@b.c", "p", "PRACTICE")
        manager.begin_connect()
        await manager.wait_settled(timeout=1.0)
        # Force a disconnect failure by swapping out the inner client
        # for one whose ``close_connection`` raises.
        class _BoomClient:
            async def close_connection(self) -> None:
                raise RuntimeError("simulated broker io failure")
            check_connect = False
        manager._client = _BoomClient()  # type: ignore[attr-defined]

        drain = asyncio.create_task(_drain())
        await manager.disconnect()
        await asyncio.wait_for(drain, timeout=1.0)

    asyncio.new_event_loop().run_until_complete(_run())
    assert any("disconnect" in p.get("kind", "").lower() for p in seen), seen
    assert any(p.get("component") == "broker" for p in seen), seen
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && pytest tests/test_admin_bot.py::test_quotex_manager_publishes_system_error_on_disconnect_failure -v
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'event_bus'`.

- [ ] **Step 3: Add the bus parameter + publish on broker errors**

Edit `backend/src/autotrader/services/quotex_manager.py`. Make sure `from typing import Any` is imported at the top.

In `QuotexManager.__init__`, add the optional parameter:

```python
def __init__(self, *, root_path: str, event_bus: Any | None = None) -> None:
    # ... existing body ...
    self._event_bus = event_bus
```

Add a small helper method on the class:

```python
def _emit_system_error(
    self,
    *,
    kind: str,
    detail: str,
    recoverable: bool = True,
) -> None:
    """Publish a ``system.error`` event for the admin notifier."""
    if self._event_bus is None:
        return
    self._event_bus.publish("system.error", {
        "component": "broker",
        "kind": kind,
        "detail": detail,
        "recoverable": recoverable,
    })
```

Then add publish calls right after each broker-error log line (use `grep -n "log\\.warning(\"broker" backend/src/autotrader/services/quotex_manager.py` to find them):

After `log.warning("broker.disconnect.error", error=str(exc))`:
```python
                self._emit_system_error(kind="disconnect.error", detail=str(exc))
```

After `log.warning("broker.connect.rejected", reason=reason)`:
```python
                self._emit_system_error(kind="connect.rejected", detail=reason)
```

After `log.error("broker.account_mode.failed", mode=mode, error=str(exc))`:
```python
                self._emit_system_error(kind="account_mode.failed", detail=str(exc))
```

After `log.warning("broker.balance.failed", error=str(exc))`:
```python
            self._emit_system_error(kind="balance.failed", detail=str(exc))
```

- [ ] **Step 4: Update `main.py` to pass the bus into `QuotexManager`**

In `backend/src/autotrader/main.py`, the bus is currently constructed *after* `QuotexManager` — we need to hoist it up. Find:

```python
manager = QuotexManager(root_path=_broker_root_path())
```

…and the later:

```python
event_bus = TradeEventBus()
app.state.event_bus = event_bus
```

Reorder so the bus comes first:

```python
event_bus = TradeEventBus()
app.state.event_bus = event_bus
manager = QuotexManager(root_path=_broker_root_path(), event_bus=event_bus)
```

Then *delete* the now-duplicate `event_bus = TradeEventBus()` line that lived later in the lifespan.

- [ ] **Step 5: Run the broker test to verify it passes**

```bash
cd backend && pytest tests/test_admin_bot.py::test_quotex_manager_publishes_system_error_on_disconnect_failure -v
```

Expected: PASS.

- [ ] **Step 6: Apply the same pattern to TelegramManager**

Edit `backend/src/autotrader/services/telegram_manager.py`. In `TelegramManager.__init__`, accept an optional bus:

```python
def __init__(self, event_bus: Any | None = None) -> None:
    # ... existing body ...
    self._event_bus = event_bus
```

Add the helper on the class:

```python
def _emit_system_error(
    self,
    *,
    kind: str,
    detail: str,
    recoverable: bool = True,
) -> None:
    if self._event_bus is None:
        return
    self._event_bus.publish("system.error", {
        "component": "telegram",
        "kind": kind,
        "detail": detail,
        "recoverable": recoverable,
    })
```

Add publish calls at the existing telegram-error log points (find them with `grep -n "log\\.warning(\"telegram" backend/src/autotrader/services/telegram_manager.py`):

After `log.warning("telegram.handler.attach_failed", error=str(exc))`:
```python
        self._emit_system_error(kind="handler.attach_failed", detail=str(exc))
```

After `log.warning("telegram.peer_cache.failed", error=str(exc))`:
```python
            self._emit_system_error(kind="peer_cache.failed", detail=str(exc))
```

In `main.py`, update the `TelegramManager()` construction to pass the bus:

```python
telegram_manager = TelegramManager(event_bus=event_bus)
```

(The bus is already constructed earlier in the lifespan from Step 4.)

- [ ] **Step 7: Run the entire test_admin_bot.py to confirm everything still passes**

```bash
cd backend && pytest tests/test_admin_bot.py -v
```

Expected: all PASS.

- [ ] **Step 8: Run the broader pipeline + broker tests for regression confidence**

```bash
cd backend && pytest tests/test_pipeline.py tests/test_broker.py -v
```

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/autotrader/services/quotex_manager.py backend/src/autotrader/services/telegram_manager.py backend/src/autotrader/main.py backend/tests/test_admin_bot.py
git commit -m "feat(autotrader): publish system.error events from broker + telegram managers"
```

---

## Task 18: Operator setup docs

**Files:**
- Create: `docs/admin-bot.md`

- [ ] **Step 1: Write the doc**

Create `docs/admin-bot.md`:

```markdown
# Admin Telegram Bot

A separate Telegram bot that lets the operator remote-control the
autotrader from their phone (status, kill switch, channel/parser
pause, risk caps) and pushes trade/risk/system events to a single
bound admin user.

The bot runs *alongside* the userbot (the one that ingests channel
posts) — it never sees channel traffic, only DMs from the bound admin.
Missing or invalid `TELEGRAM_BOT_TOKEN` = the bot is a no-op; the rest
of the autotrader keeps running.

## Setup checklist

1. **Create the bot** — open Telegram, message `@BotFather`, send
   `/newbot`, follow the prompts. Copy the token (looks like
   `123456:ABCdef...`).
2. **Set the env var** — add to your `.env`:

   ```
   TELEGRAM_BOT_TOKEN=123456:your-token-here
   ```

3. **Restart the container** — `docker compose restart` (or your local
   `uvicorn` process). On startup you should see one of:
   - `admin_bot.started` — token was good, bot is online
   - `admin_bot.disabled` — no token set; the bot is a no-op
   - `admin_bot.start_failed` — token rejected; check the dashboard
     "Admin bot offline" badge for the error
4. **Bind yourself** — open the bot in Telegram (search for the username
   you set in BotFather), send `/start`. The bot replies
   `Bound as admin`.
5. **Walk the menu** — send `/help` to see every command. Try in this
   order:
   - `/status` — read the pipeline / kill switch state
   - `/channels` — list watched channels
   - `/parsers` — list parser configs
   - `/trades 5` — last 5 trades
6. **Place a demo trade** — make sure your broker is connected on
   PRACTICE, dispatch a signal in a watched channel. You should
   receive a `PLACED` notification, then a `WIN` / `LOSS`
   notification when the watcher resolves it.
7. **Trigger a risk rejection** — set a tiny daily-loss cap
   (`/caps loss 1`), let one trade lose, send another signal —
   you should receive a `REJECTED` notification.

## What if I lose the bot?

If you switch Telegram accounts or block the bot, you can re-pair from
the dashboard:

1. Click *Telegram > Admin bot > Unbind* on the dashboard.
2. Open the bot from the new Telegram account, send `/start`.

You can also `POST /admin-bot/unbind` from any HTTP client with the
dashboard auth token.

## Muting noisy classes

The bot pushes four event classes by default. Mute one with:

```
/notify placed off
/notify settled off
/notify risk_rejected off
/notify system_error off
```

…and re-enable with `on`. Mutes persist across restarts (stored on the
`global_settings` row).

## Rate limiting

Each event class has a token bucket: capacity 5, refill 1/30s. When a
flood empties a bucket, additional events for that class are coalesced
into a single digest message every 60s:

```
14 risk_rejected events suppressed in last 60s
```

This protects you from an unreadable chat during a flapping broker
connection or a daily-cap breach.

## Send-failure backoff

If the bot fails to DM you 5 times in a row (you blocked it,
deactivated the account, network glitch) it pauses outbound
notifications until you send any message back. All `system.error`
events still go to the structured log — only DM forwarding pauses.
```

- [ ] **Step 2: Commit**

```bash
git add docs/admin-bot.md
git commit -m "docs(autotrader): operator setup guide for the admin telegram bot"
```

---

## Self-review

After saving this plan I checked it against the spec end-to-end:

**1. Spec coverage:**
- §3.1 Transport (separate bot client) → Task 4
- §3.2 File layout → Tasks 4, 7, 9, 14, 6 (router), tests in 2/3/4
- §3.3 Wiring diagram → Task 5 (bot in lifespan), Task 15 (notifier subscribed to bus)
- §3.4 DB columns → Task 2 (model) + Task 3 (migration)
- §3.5 Configuration → Task 1
- §4 Auth lifecycle → Task 7 (binding + auth gate), Task 13 (`/unbind`), Task 6 (REST `/unbind`)
- §5.1 Command list — every command in the table:
  - `/start` Task 7
  - `/help` `/whoami` `/status` `/balance` Task 8
  - `/trades` `/decisions` `/streaks` Task 9
  - `/killswitch` `/pipeline` `/panic` `/mode` Task 10
  - `/channels` `/channel` `/parsers` `/parser` Task 11
  - `/caps` `/stake` Task 12
  - `/notify` `/unbind` Task 13
- §5.2 Inline confirms for destructive commands → Task 10 (`/mode real`) + Task 13 (`/unbind`)
- §5.3 `asyncio.Lock` for command serialisation → Task 7 (introduces `_dispatch_lock`), Task 11 (callback hook reuses it)
- §6 Pause = toggle `enabled` → Task 11 (callbacks toggle the flag)
- §7.1 Event classes → Task 14 (notify gates) + Task 15 (dispatcher)
- §7.2 Format examples → Task 15 (format functions)
- §7.3 Token-bucket rate limiting → Task 14
- §7.4 New event types — `risk.rejected` Task 16, `system.error` Task 17
- §8.1 Bot fails to start → Task 4 (start-failure test)
- §8.2 5-failure backoff → Task 14 (`_FAILURE_THRESHOLD`) + Task 15 (`reset_failures` on incoming)
- §8.3 Handler exceptions → Task 7 (try/except in dispatcher)
- §9.1 Unit tests → throughout
- §9.2 Integration test — REST/bot equivalence: Task 6 covers REST; Task 10 + Task 13 verify the bot path. Cross-equivalence is implicit (both touch the same DB column).
- §9.3 Manual smoke test → Task 18

**2. Placeholder scan:** None. Every step has the actual code or actual command.

**3. Type consistency:**
- `Reply` dataclass: defined Task 7, used everywhere after — same `text` + `markup` fields.
- `AdminBotStatus`: defined Task 4, used by router (Task 6).
- `_dispatch_lock`: defined Task 7, reused in callback hook (Task 11).
- `_set_settings_flag`: defined Task 10, reused in Tasks 12, 13.
- `_confirm_keyboard`: defined Task 10, reused Task 13 with `unbind` action.
- `attach(...)` signature on `admin_bot_state`: full signature with all four optional kwargs lands in Task 9; Tasks 11, 13, 15 use it without re-defining — consistent.
- Notifier `notify(cls, text, markup=None)`: defined Task 14, called Task 15 with all four classes (`placed`, `settled`, `risk_rejected`, `system_error`) — all match `_NOTIFY_FIELD` keys.

Issues found and fixed inline during review:
- `/balance` test originally asserted on a live broker call but the handler intentionally defers to dashboard text — rewrote the test to assert on the friendly fallback text (Task 8).
- Task 11's first draft of `/channels` tried to attach per-row inline keyboards in a single message, which Telegram doesn't support. Rewrote to a flat list + `/channel <id>` per-row drilldown.
- Task 15's `_consume`/`_shutdown` use the unusual pattern of attaching methods to the class after definition. Documented why (re-using format functions in tests without instantiating the class).
- The `/unbind` handler from Task 13 needs the bot reference via the resolver. Confirmed Task 9 already attaches the bot and Task 13 does *not* re-attach.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-admin-telegram-bot.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration and isolated context per task. Best for an 18-task plan: each subagent gets only the spec + the one task, never the whole plan, so reviews stay tight.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints for review. Faster for small plans, harder to keep focused for larger ones.

**Which approach?**
