# OTP Relay + Broker-Session Persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an admin-bot OTP relay that lets the operator answer broker PIN challenges from Telegram (reply-to-message UX) plus a Fernet-encrypted on-disk store for the broker session — so most container restarts skip the OTP cycle entirely.

**Architecture:** A new `AdminBotOTPRelay` module owns the OTP-message lifecycle (send → edit on re-prompt → terminal on resolve/timeout/exhausted). The relay is wired into the existing `QuotexManager` via a direct callback (`set_otp_relay`) so the Telegram round-trip completes before the manager's 180-second OTP timer parks. A small `SessionStore` persists `client.session_data` to `/data/quotex_session.json` encrypted with the existing `AUTOTRADER_FERNET_KEY`; on restart the manager loads it before calling `client.connect()` so pyquotex skips the HTTP login when the SSID is still valid. The existing admin-bot message hook gains a single forward-on-match branch for replies targeting the active OTP message; a new `/reconnect` command handles the timeout/exhausted recovery path.

**Tech Stack:** Pyrogram (already a dep), FastAPI lifespan, cryptography.Fernet (already a dep), SQLModel + aiosqlite (unchanged), structlog, pytest + pytest-asyncio for tests.

**Spec:** `autotrader/docs/superpowers/specs/2026-05-12-otp-relay-and-session-persistence-design.md` — single source of truth. Re-read it if a task here is ambiguous.

---

## File Structure

**Create:**

| Path | Responsibility |
| --- | --- |
| `backend/src/autotrader/services/session_store.py` | `SessionStore` — atomic encrypted dict I/O. Three public methods (`load`, `save`, `clear`); pure file I/O, no business logic. |
| `backend/src/autotrader/services/admin_bot_otp_relay.py` | `AdminBotOTPRelay` — owns the OTP-message lifecycle: receives direct calls from the manager (`on_otp_required`, `on_otp_resolved`, `on_otp_timeout`), sends/edits Telegram messages, exposes `handle_reply` for the existing message hook to forward to. |
| `backend/tests/test_session_store.py` | Unit tests for `SessionStore` — roundtrip, missing file, corrupt file, wrong key, atomic write, log hygiene. |
| `backend/tests/test_admin_bot_otp_relay.py` | Unit tests for `AdminBotOTPRelay` — send/edit lifecycle, reply extraction, timeout, attempts cap, env-var override. Uses a hand-rolled `FakeAdminBot` (no Pyrogram). |

**Modify:**

| Path | Change |
| --- | --- |
| `backend/src/autotrader/config.py` | Add `otp_max_attempts: int = Field(default=3, ge=1, le=10)` to `Settings`, env `AUTOTRADER_OTP_MAX_ATTEMPTS`. |
| `backend/src/autotrader/services/quotex_manager.py` | Add `_session_store` + `_otp_relay` + `_otp_attempt` attributes; load/save session in `_do_connect`; call relay callbacks at `_on_otp_callback` start, timeout, and resolved branches. ~60 LoC. |
| `backend/src/autotrader/services/admin_bot_state.py` | Add `_otp_relay` slot + `get_otp_relay()` accessor; update `attach()` signature. |
| `backend/src/autotrader/services/admin_bot_commands.py` | At the top of `_hook` in `build_message_hook`: detect reply-to-message, forward to `relay.handle_reply` if the relay claims it. Add `/reconnect` handler + register in `COMMANDS`. |
| `backend/src/autotrader/main.py` | In `lifespan` startup: construct `SessionStore` + `AdminBotOTPRelay`, attach via `manager.set_otp_relay(relay)` and `admin_bot_state.attach(..., otp_relay=relay)`. |
| `backend/tests/test_broker.py` | Update `FakeQuotex.__init__` to accept (already does via `**_`) the new manager hooks; add `_FakeSessionStore` for the persistence tests; add 2 manager-side tests for the new behaviour. |
| `.env.example` | Document `AUTOTRADER_OTP_MAX_ATTEMPTS=3`. |

The session_store and the OTP relay are split because they solve unrelated concerns: one is an atomic I/O wrapper around a single dict; the other is a Telegram message-state machine. Their tests have no overlap. Combining them would force tests of either concern to drag in the other's machinery.

---

## Conventions used in tasks

- **CWD for commands** — all `pytest` and `git` commands assume CWD is `autotrader/` (the existing plan in this repo uses that convention). When running `pytest`, prefix with `cd backend && pytest <path>` so it picks up the project's `pyproject.toml`.
- **Imports** — every code block shows the imports it needs. Repeats across tasks are intentional (engineer may read tasks out of order).
- **Test commands** — `pytest tests/<file>.py -v` from `backend/`. The repo uses `uv run` in CI, but the venv is activated locally, so bare `pytest` works.
- **Commit step** — every task ends in a commit. Frequent commits = easy bisect.
- **Fakes over mocks** — the existing `test_broker.py` uses a hand-rolled `FakeQuotex` rather than `unittest.mock`. Match that style; only fall back to `MagicMock` for incidental object surfaces.
- **No new Pyrogram** — relay tests use a hand-rolled `FakeAdminBot` that just records `send`/`edit` calls. We never instantiate a real Pyrogram client in tests.

---

## Task 1: Add `AUTOTRADER_OTP_MAX_ATTEMPTS` config setting

**Files:**
- Modify: `backend/src/autotrader/config.py`
- Test: `backend/tests/test_config.py` (append to existing)

- [ ] **Step 1: Read the existing config + tests to find the right insertion points**

```bash
grep -n "class Settings\|otp_\|live_trading_enabled" backend/src/autotrader/config.py backend/tests/test_config.py
```

Note the existing `live_trading_enabled` field — your new field goes right next to it. Note the existing test file structure — your new test goes at the bottom.

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/test_config.py`:

```python
def test_otp_max_attempts_defaults_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default cap on OTP retry attempts per cycle. The relay edits
    the same Telegram message up to this count, then bails with the
    terminal '/reconnect to retry' state.
    """
    monkeypatch.delenv("AUTOTRADER_OTP_MAX_ATTEMPTS", raising=False)
    from autotrader.config import Settings  # noqa: PLC0415

    s = Settings()  # type: ignore[call-arg]
    assert s.otp_max_attempts == 3


def test_otp_max_attempts_env_var_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOTRADER_OTP_MAX_ATTEMPTS", "5")
    from autotrader.config import Settings  # noqa: PLC0415

    s = Settings()  # type: ignore[call-arg]
    assert s.otp_max_attempts == 5


def test_otp_max_attempts_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """Field-level validator rejects nonsense values at parse time."""
    monkeypatch.setenv("AUTOTRADER_OTP_MAX_ATTEMPTS", "0")
    from pydantic import ValidationError  # noqa: PLC0415
    from autotrader.config import Settings  # noqa: PLC0415

    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_config.py -v -k otp
```

Expected: 3 failures, all with `AttributeError: 'Settings' object has no attribute 'otp_max_attempts'`.

- [ ] **Step 4: Add the field to `Settings`**

In `backend/src/autotrader/config.py`, find the `Settings` class (it's the one with `live_trading_enabled`). Add the new field directly under `live_trading_enabled`:

```python
    # Maximum OTP attempts per cycle before the relay gives up and
    # edits the message to '/reconnect to retry'. Trades off
    # alert-fatigue (low) against finger-fumble forgiveness (high).
    # 3 is the sweet spot per the spec; tune via env if you find
    # yourself routinely needing more.
    otp_max_attempts: int = Field(default=3, ge=1, le=10)
```

If `Field` is not already imported, add it: `from pydantic import Field`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_config.py -v -k otp
```

Expected: 3 passes.

- [ ] **Step 6: Document the env var in `.env.example`**

Append to `.env.example` (find the section near other `AUTOTRADER_*` settings):

```
# Maximum OTP attempts per cycle. The admin-bot relay edits the same
# Telegram message up to this many times on bad codes; after the cap,
# it shows '/reconnect to retry'. Range: 1..10.
AUTOTRADER_OTP_MAX_ATTEMPTS=3
```

- [ ] **Step 7: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/config.py autotrader/backend/tests/test_config.py autotrader/.env.example
git commit -m "feat(autotrader/config): add AUTOTRADER_OTP_MAX_ATTEMPTS setting"
```

---

## Task 2: `SessionStore` — atomic encrypted dict I/O

**Files:**
- Create: `backend/src/autotrader/services/session_store.py`
- Create: `backend/tests/test_session_store.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_session_store.py`:

```python
"""SessionStore — atomic encrypted dict I/O.

The store persists pyquotex's session_data dict to disk so most
container restarts can skip the HTTP login (and therefore the OTP
challenge). Encrypted at rest with the same Fernet key the rest of
the app uses for secrets.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import structlog
from cryptography.fernet import Fernet


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(Fernet.generate_key())


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "session.json"


def test_save_load_roundtrip(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    store = SessionStore(path=store_path, fernet=fernet)
    payload = {
        "token": "ssid-abc123",
        "cookies": "laravel_session=foo; _cfuvid=bar",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/144.0",
    }
    store.save(payload)
    loaded = store.load()
    assert loaded == payload


def test_load_missing_file_returns_none(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    store = SessionStore(path=store_path, fernet=fernet)
    assert store.load() is None


def test_load_corrupt_file_returns_none(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    # Write garbage that Fernet can't decrypt.
    store_path.write_bytes(b"not-fernet-ciphertext")
    store = SessionStore(path=store_path, fernet=fernet)
    assert store.load() is None


def test_load_wrong_fernet_key_returns_none(store_path: Path) -> None:
    """Key rotation: an old file under the previous key decrypts to
    None rather than raising. Forces a fresh login on the next start
    rather than crashing the app."""
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    key_a = Fernet(Fernet.generate_key())
    key_b = Fernet(Fernet.generate_key())
    SessionStore(path=store_path, fernet=key_a).save({"token": "x", "cookies": "", "user_agent": ""})
    assert SessionStore(path=store_path, fernet=key_b).load() is None


def test_save_is_atomic_on_os_replace_failure(
    fernet: Fernet, store_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If os.replace raises, the original file (if any) must be
    untouched and no half-written temp file should leak through to
    the final path."""
    from autotrader.services import session_store as ss_mod  # noqa: PLC0415

    # Seed an existing valid file so we can verify it survives.
    ss_mod.SessionStore(path=store_path, fernet=fernet).save(
        {"token": "original", "cookies": "", "user_agent": ""},
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated mid-replace crash")

    monkeypatch.setattr(ss_mod.os, "replace", boom)
    store = ss_mod.SessionStore(path=store_path, fernet=fernet)
    with pytest.raises(OSError, match="simulated"):
        store.save({"token": "new", "cookies": "", "user_agent": ""})

    # Original file is intact.
    loaded = ss_mod.SessionStore(path=store_path, fernet=fernet).load()
    assert loaded == {"token": "original", "cookies": "", "user_agent": ""}


def test_clear_removes_file_idempotently(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    store = SessionStore(path=store_path, fernet=fernet)
    store.save({"token": "x", "cookies": "", "user_agent": ""})
    assert store_path.exists()
    store.clear()
    assert not store_path.exists()
    # Second clear on a missing file must not raise.
    store.clear()


def test_save_does_not_log_token_value(
    fernet: Fernet, store_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The token is the credential — never let it appear in
    structured logs or stderr output."""
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    # structlog routes via stdlib logging — caplog catches both.
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    store = SessionStore(path=store_path, fernet=fernet)
    with caplog.at_level(logging.INFO):
        store.save({"token": "extremely-secret-ssid", "cookies": "", "user_agent": ""})
    joined = " ".join(rec.message for rec in caplog.records)
    assert "extremely-secret-ssid" not in joined
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_session_store.py -v
```

Expected: all 7 fail with `ModuleNotFoundError: No module named 'autotrader.services.session_store'`.

- [ ] **Step 3: Create the module**

Create `backend/src/autotrader/services/session_store.py`:

```python
"""Atomic encrypted persistence for pyquotex's ``session_data`` dict.

The broker's session — SSID token + cookies + user-agent — lives in
process memory by default in pyquotex. We persist it to disk so most
container restarts can skip the HTTP login (and therefore the OTP
challenge) when the SSID is still valid.

Encryption at rest uses the same ``AUTOTRADER_FERNET_KEY`` that
protects ``broker_credentials``; reusing the key keeps the deployment
story unchanged (no new secret to provision, rotate, back up).

Atomic write: ``save()`` writes ``${path}.tmp`` then ``os.replace()``
to ``${path}``. Without this, a crash mid-write would corrupt the
file and the next ``load()`` would return None — safe-by-default but
wasteful. With the rename, the file is either the old contents or
the new, never partial.

Schema (pyquotex-native):
    {"token": str, "cookies": str, "user_agent": str}

No schema version field — we control both ends and would coordinate a
rewrite if the shape ever changed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog
from cryptography.fernet import Fernet, InvalidToken

log = structlog.get_logger(__name__)


class SessionStore:
    """Persists a session dict, encrypted at rest."""

    def __init__(self, *, path: Path, fernet: Fernet) -> None:
        self._path = path
        self._fernet = fernet

    def load(self) -> dict[str, Any] | None:
        """Return the decrypted dict, or None on any failure.

        Never raises — a corrupt file, wrong key, or missing file all
        fall back to None so the caller treats this as 'no cached
        session' and runs a fresh login.
        """
        try:
            ciphertext = self._path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken:
            log.warning("broker.session.decrypt_failed", path=str(self._path))
            return None
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("broker.session.parse_failed", path=str(self._path))
            return None
        if not isinstance(payload, dict):
            log.warning(
                "broker.session.bad_shape", path=str(self._path),
                got_type=type(payload).__name__,
            )
            return None
        return payload

    def save(self, session_data: dict[str, Any]) -> None:
        """Atomically replace the on-disk file with the encrypted dict.

        Never logs the token value — only its presence.
        """
        # Best-effort directory create — covers first-startup on a
        # fresh /data volume.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(session_data).encode("utf-8")
        ciphertext = self._fernet.encrypt(plaintext)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_bytes(ciphertext)
        os.replace(tmp_path, self._path)
        log.info(
            "broker.session.persisted",
            token_present=bool(session_data.get("token")),
            path=str(self._path),
        )

    def clear(self) -> None:
        """Remove the on-disk file; idempotent."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            return
        log.info("broker.session.cleared", path=str(self._path))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_session_store.py -v
```

Expected: 7 passes.

- [ ] **Step 5: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/session_store.py autotrader/backend/tests/test_session_store.py
git commit -m "feat(autotrader/session): atomic encrypted SessionStore for broker session"
```

---

## Task 3: `QuotexManager` — load/save the session

**Files:**
- Modify: `backend/src/autotrader/services/quotex_manager.py`
- Modify: `backend/tests/test_broker.py`

- [ ] **Step 1: Skim what the manager currently does in `_do_connect`**

```bash
grep -n "_do_connect\|client.connect\|client.session_data\|self._client = client" backend/src/autotrader/services/quotex_manager.py
```

Note where `client = Quotex(...)` is created and where `await client.connect()` is awaited. The new "load session before connect" code goes immediately after the `Quotex(...)` construction. The "save session after connect" code goes inside the `if ok:` branch, next to `self._start_status_watcher()`.

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_broker.py` (find the bottom; if there's a `TODO: more tests` marker drop it above):

```python
# ---------------------------------------------------------------------------
# Session persistence (Task 3 of OTP relay plan)
# ---------------------------------------------------------------------------


class _FakeSessionStore:
    """In-memory drop-in for SessionStore — tests assert on
    ``saved_payloads`` / ``primed_payload`` rather than real disk I/O."""

    def __init__(self, primed: dict | None = None) -> None:
        self.primed_payload = primed
        self.saved_payloads: list[dict] = []
        self.cleared_count = 0

    def load(self) -> dict | None:
        return self.primed_payload

    def save(self, session_data: dict) -> None:
        self.saved_payloads.append(dict(session_data))

    def clear(self) -> None:
        self.cleared_count += 1


def test_manager_loads_session_before_connect_when_attached(
    client: TestClient,
) -> None:
    """When a SessionStore is attached and has a cached payload, the
    manager hydrates client.session_data BEFORE awaiting connect."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    primed = {
        "token": "cached-ssid",
        "cookies": "laravel_session=foo",
        "user_agent": "Firefox/144",
    }
    store = _FakeSessionStore(primed=primed)
    manager.set_session_store(store)

    headers = _login(client)
    _put_credentials(client, headers)
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200, r.text

    # The fake Quotex.connect() doesn't observe session_data directly,
    # so we assert that the manager forwarded the payload onto the
    # client instance constructed by the FakeQuotex factory.
    fq = FakeQuotex.last_instance
    assert fq is not None
    # On real Quotex, session_data lives on self (the fake mirrors via
    # ``session_data`` attr set by the manager).
    assert getattr(fq, "session_data", None) == primed


def test_manager_saves_session_after_successful_connect(
    client: TestClient,
) -> None:
    """On a successful connect, manager pushes the (now-warm)
    client.session_data through SessionStore.save."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    store = _FakeSessionStore()
    manager.set_session_store(store)

    headers = _login(client)
    _put_credentials(client, headers)

    # FakeQuotex.connect populates a fresh session_data on its client.
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200, r.text

    assert len(store.saved_payloads) >= 1
    last = store.saved_payloads[-1]
    assert last.get("token")  # truthy
```

You'll also need to update `FakeQuotex` so it (a) exposes a `session_data` attribute the manager can read/write, and (b) `connect()` writes a fresh session_data on success. Edit the `FakeQuotex` class in the same file:

Find `FakeQuotex.__init__` and add after the existing `self.api = MagicMock()` line:

```python
        # Manager mirrors session_data onto the client before
        # connect() and reads it back after. Real pyquotex stores
        # this on ``Quotex.session_data``.
        self.session_data: dict = {}
```

Find `FakeQuotex._flip_connected` and add a line that populates session_data so the manager has something to save:

```python
    def _flip_connected(self) -> None:
        self.api.state.status = WebsocketStatus.CONNECTED
        self.api.state.auth_status = AuthStatus.AUTHENTICATED
        # Mirror pyquotex's behaviour — a successful connect leaves a
        # populated session_data on the client.
        if not self.session_data.get("token"):
            self.session_data = {
                "token": "fake-ssid-from-login",
                "cookies": "fake-cookies",
                "user_agent": "Firefox/144 (test)",
            }
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_broker.py::test_manager_loads_session_before_connect_when_attached tests/test_broker.py::test_manager_saves_session_after_successful_connect -v
```

Expected: both fail with `AttributeError: 'QuotexManager' object has no attribute 'set_session_store'`.

- [ ] **Step 4: Add `set_session_store` + load + save hooks to `QuotexManager`**

In `backend/src/autotrader/services/quotex_manager.py`, find the `__init__` method. Add right after `self._consecutive_failed_reconnects: int = 0`:

```python
        # Session persistence. When attached (typically by lifespan),
        # we hydrate ``client.session_data`` from this before calling
        # ``client.connect()`` and push the (now-warm) session back
        # into this after a successful connect. Until attached, both
        # operations are no-ops — the manager works fine without
        # persistence, just at the cost of an extra HTTP login per
        # restart.
        self._session_store: Any | None = None
```

Add a public setter method (place it next to `set_credentials`):

```python
    def set_session_store(self, store: Any | None) -> None:
        """Attach a SessionStore-like object. ``None`` clears it.

        Duck-typed on three methods: ``load() -> dict | None``,
        ``save(dict) -> None``, ``clear() -> None``. Tests inject a
        fake; production wires the real ``SessionStore``.
        """
        self._session_store = store
```

Inside `_do_connect`, immediately after `client = Quotex(...)` returns (still inside the `try`), add:

```python
                # Hydrate session_data from disk if we have a store and
                # a cached payload. Pyquotex's ``_connect_unlocked``
                # checks ``self.session_data.get("token")`` and skips
                # the HTTP login when present — so this is the path
                # that lets a container restart skip OTP.
                if self._session_store is not None:
                    cached = self._session_store.load()
                    if cached:
                        client.session_data = cached
                        log.info(
                            "broker.session.loaded",
                            token_present=bool(cached.get("token")),
                        )
```

In the `if ok:` branch of `_do_connect`, immediately after `self._start_status_watcher()`, add:

```python
                # Persist the freshly-warm session so the NEXT
                # container restart can skip OTP. Best-effort: log on
                # failure but don't fail the connect.
                if self._session_store is not None:
                    try:
                        self._session_store.save(client.session_data)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("broker.session.save_failed", error=str(exc))
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_broker.py::test_manager_loads_session_before_connect_when_attached tests/test_broker.py::test_manager_saves_session_after_successful_connect -v
```

Expected: 2 passes.

- [ ] **Step 6: Run the FULL broker test suite to confirm no regression**

```bash
cd backend && pytest tests/test_broker.py tests/test_broker_resilience.py -v
```

Expected: all pass (was 24 before, now 26).

- [ ] **Step 7: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/quotex_manager.py autotrader/backend/tests/test_broker.py
git commit -m "feat(autotrader/broker): wire SessionStore into QuotexManager"
```

---

## Task 4: `AdminBotOTPRelay` — state + `on_otp_required` (send + edit)

**Files:**
- Create: `backend/src/autotrader/services/admin_bot_otp_relay.py`
- Create: `backend/tests/test_admin_bot_otp_relay.py`

- [ ] **Step 1: Write the failing tests (the first three behaviours)**

Create `backend/tests/test_admin_bot_otp_relay.py`:

```python
"""AdminBotOTPRelay — OTP-message lifecycle in Telegram.

We never instantiate a real Pyrogram client here. ``FakeAdminBot``
captures every ``send`` / ``edit`` call so tests assert on the
resulting message sequence directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _SentMessage:
    chat_id: int
    text: str
    message_id: int


@dataclass
class _EditedMessage:
    chat_id: int
    message_id: int
    text: str


class FakeAdminBot:
    """Captures send/edit_message_text. State == 'running' by default."""

    def __init__(self, state: str = "running") -> None:
        self._state = state
        self._next_message_id = 1000
        self.sent: list[_SentMessage] = []
        self.edits: list[_EditedMessage] = []

    def status(self) -> Any:
        return type("S", (), {"state": self._state})()

    async def send(self, chat_id: int, text: str, **_kwargs: Any) -> Any:
        msg_id = self._next_message_id
        self._next_message_id += 1
        self.sent.append(_SentMessage(chat_id=chat_id, text=text, message_id=msg_id))
        # Return a Pyrogram-shaped object with id attribute.
        return type("M", (), {"id": msg_id})()

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str, **_kwargs: Any,
    ) -> None:
        self.edits.append(
            _EditedMessage(chat_id=chat_id, message_id=message_id, text=text),
        )


@dataclass
class FakeManager:
    submitted: list[str] = field(default_factory=list)

    async def submit_otp(self, code: str) -> None:
        self.submitted.append(code)


@pytest.fixture
def fake_bot() -> FakeAdminBot:
    return FakeAdminBot()


@pytest.fixture
def fake_manager() -> FakeManager:
    return FakeManager()


def _relay(fake_bot: FakeAdminBot, fake_manager: FakeManager, bound_user_id: int = 42):
    from autotrader.services.admin_bot_otp_relay import AdminBotOTPRelay  # noqa: PLC0415

    return AdminBotOTPRelay(
        manager=fake_manager,
        admin_bot=fake_bot,
        bound_user_id=bound_user_id,
        max_attempts=3,
    )


# ---------------------------------------------------------------------------
# on_otp_required (attempt 1) — sends a fresh message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_otp_required_attempt_1_sends_message(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("Enter PIN from email", attempt=1)

    assert len(fake_bot.sent) == 1
    msg = fake_bot.sent[0]
    assert msg.chat_id == 42
    assert "OTP" in msg.text or "PIN" in msg.text
    assert "reply" in msg.text.lower()
    assert fake_bot.edits == []  # no edits on attempt 1


@pytest.mark.asyncio
async def test_on_otp_required_attempt_2_edits_existing_message(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """Re-prompt (attempt > 1) edits the SAME message — the same
    message_id stays valid as the operator's reply target."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("first prompt", attempt=1)
    sent_id = fake_bot.sent[0].message_id

    await relay.on_otp_required("second prompt", attempt=2)

    assert len(fake_bot.sent) == 1  # still only ONE send
    assert len(fake_bot.edits) == 1
    edit = fake_bot.edits[0]
    assert edit.message_id == sent_id
    assert "2/3" in edit.text  # attempt counter shows up


@pytest.mark.asyncio
async def test_disabled_bot_short_circuits(
    fake_manager: FakeManager,
) -> None:
    """When admin_bot.status().state != 'running', the relay no-ops
    silently — the dashboard's awaiting_otp surface is the fallback."""
    bot = FakeAdminBot(state="disabled")
    relay = _relay(bot, fake_manager)
    await relay.on_otp_required("prompt", attempt=1)

    assert bot.sent == []
    assert bot.edits == []
```

Add `pytest-asyncio` config check: open `backend/pyproject.toml` and confirm `asyncio_mode = "auto"` exists under `[tool.pytest.ini_options]`. If not, the `@pytest.mark.asyncio` markers may not run; the existing `test_broker_resilience.py` uses them already so it should be configured.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot_otp_relay.py -v
```

Expected: 3 failures with `ModuleNotFoundError: No module named 'autotrader.services.admin_bot_otp_relay'`.

- [ ] **Step 3: Create the relay module (minimal — just enough for these tests)**

Create `backend/src/autotrader/services/admin_bot_otp_relay.py`:

```python
"""Admin-bot OTP relay — handles the broker-PIN challenge from Telegram.

Lifecycle (managed by direct calls from :class:`QuotexManager`):

1. ``on_otp_required(prompt, attempt=1)`` — broker just challenged.
   Relay sends a fresh Telegram message to the bound admin user, asks
   them to reply with the code.
2. ``on_otp_required(prompt, attempt=N>1)`` — broker re-challenged
   (wrong code). Relay EDITS the existing message in place so the
   operator's reply-target stays valid; the message text updates to
   show the new attempt count.
3. ``handle_reply(message)`` — operator sent a Telegram reply
   targeting the active OTP message. Relay extracts digits and
   forwards via ``manager.submit_otp``.
4. ``on_otp_resolved()`` — connect completed successfully. Relay
   edits the message to a terminal "✅ Connected." and clears state.
5. ``on_otp_timeout()`` — 180s window elapsed without resolution.
   Relay edits to "⏰ OTP expired. Reply /reconnect to retry."

The relay owns no durable state — a container restart loses the
in-flight cycle, which is correct (an OTP code in flight isn't
durable data).

Telegram formatting: all messages are plain text. The broker's
prompt string is never interpolated raw into a markdown context, so
we don't need an escaper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from autotrader.models.base import utc_now

log = structlog.get_logger(__name__)

# Total relay-side budget. The manager's _on_otp_callback already
# parks on ``asyncio.wait_for(_otp_future, timeout=180)``; we use the
# same value here only to format the user-visible message.
_OTP_WINDOW_SECONDS = 180

# Operator's reply payload — anywhere from 4 to 8 digits, depending
# on broker. Quotex emits 6 today.
_OTP_DIGIT_PATTERN = re.compile(r"\b(\d{4,8})\b")


@dataclass
class _ActiveCycle:
    """One in-flight OTP cycle. Replaced on every fresh
    ``on_otp_required(attempt=1)``."""

    message_id: int
    chat_id: int
    attempt: int
    expires_at: datetime
    broker_prompt: str


class AdminBotOTPRelay:
    """Translates broker OTP challenges into a Telegram reply UX."""

    def __init__(
        self,
        *,
        manager: Any,
        admin_bot: Any,
        bound_user_id: int | None,
        max_attempts: int = 3,
    ) -> None:
        self._manager = manager
        self._admin_bot = admin_bot
        self._bound_user_id = bound_user_id
        self._max_attempts = max_attempts
        self._active: _ActiveCycle | None = None

    # ------------------------------------------------------------------
    # Entry points called by QuotexManager
    # ------------------------------------------------------------------

    async def on_otp_required(self, prompt: str, attempt: int) -> None:
        """Broker just challenged. Send (attempt=1) or edit (attempt>1)."""
        if not self._can_relay():
            log.info("otp_relay.skipped.bot_unavailable", attempt=attempt)
            return
        if self._bound_user_id is None:
            log.info("otp_relay.skipped.no_bound_user", attempt=attempt)
            return

        if attempt <= 1 or self._active is None:
            await self._start_new_cycle(prompt=prompt)
        else:
            await self._bump_existing_cycle(prompt=prompt, attempt=attempt)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _can_relay(self) -> bool:
        try:
            return self._admin_bot.status().state == "running"
        except Exception:  # noqa: BLE001
            return False

    async def _start_new_cycle(self, *, prompt: str) -> None:
        text = self._format_initial_prompt()
        try:
            msg = await self._admin_bot.send(self._bound_user_id, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.send_failed", error=str(exc))
            self._active = None
            return
        self._active = _ActiveCycle(
            message_id=int(getattr(msg, "id", 0)),
            chat_id=int(self._bound_user_id or 0),
            attempt=1,
            expires_at=utc_now() + timedelta(seconds=_OTP_WINDOW_SECONDS),
            broker_prompt=prompt,
        )
        log.info(
            "otp_relay.prompt_sent",
            message_id=self._active.message_id,
            attempt=1,
        )

    async def _bump_existing_cycle(self, *, prompt: str, attempt: int) -> None:
        assert self._active is not None
        self._active.attempt = attempt
        self._active.broker_prompt = prompt
        text = self._format_retry_prompt(attempt=attempt)
        try:
            await self._admin_bot.edit_message_text(
                self._active.chat_id,
                self._active.message_id,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.edit_failed", error=str(exc))
            return
        log.info(
            "otp_relay.prompt_edited",
            message_id=self._active.message_id,
            attempt=attempt,
        )

    # ------------------------------------------------------------------
    # Message formatting (plain text — no parse_mode)
    # ------------------------------------------------------------------

    def _format_initial_prompt(self) -> str:
        return (
            f"🔐 Broker needs OTP — reply to this message with the "
            f"code we just emailed you ({_OTP_WINDOW_SECONDS}s)."
        )

    def _format_retry_prompt(self, *, attempt: int) -> str:
        return (
            f"❌ Wrong code — reply with the new code we just "
            f"emailed you (attempt {attempt}/{self._max_attempts})."
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot_otp_relay.py -v
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/admin_bot_otp_relay.py autotrader/backend/tests/test_admin_bot_otp_relay.py
git commit -m "feat(autotrader/admin-bot): AdminBotOTPRelay skeleton + on_otp_required"
```

---

## Task 5: `AdminBotOTPRelay` — `handle_reply` (extract digits + submit)

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_otp_relay.py`
- Modify: `backend/tests/test_admin_bot_otp_relay.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot_otp_relay.py`:

```python
# ---------------------------------------------------------------------------
# handle_reply — extract digits + submit
# ---------------------------------------------------------------------------


@dataclass
class _FakeReplyTo:
    id: int


@dataclass
class _FakeFromUser:
    id: int


@dataclass
class _FakeMessage:
    text: str
    reply_to_message: _FakeReplyTo | None
    from_user: _FakeFromUser


def _reply(text: str, target_id: int, user_id: int = 42) -> _FakeMessage:
    return _FakeMessage(
        text=text,
        reply_to_message=_FakeReplyTo(id=target_id),
        from_user=_FakeFromUser(id=user_id),
    )


@pytest.mark.asyncio
async def test_owns_reply_returns_true_only_for_active_message_id(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """The admin_bot_commands hook asks the relay 'is this yours?'
    before handing the message over. Yes only when there's an active
    cycle and the reply_to_message.id matches."""
    relay = _relay(fake_bot, fake_manager)
    # No active cycle → never owns.
    assert relay.owns_reply(_reply("123456", target_id=9999)) is False

    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    assert relay.owns_reply(_reply("123456", target_id=active_id)) is True
    assert relay.owns_reply(_reply("123456", target_id=active_id + 1)) is False
    # Message with no reply_to_message is not ours.
    assert relay.owns_reply(
        _FakeMessage(text="123456", reply_to_message=None, from_user=_FakeFromUser(id=42)),
    ) is False


@pytest.mark.asyncio
async def test_handle_reply_extracts_digits_and_submits(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.handle_reply(_reply("code: 123456 (got it)", target_id=active_id))

    assert fake_manager.submitted == ["123456"]


@pytest.mark.asyncio
async def test_handle_reply_with_no_digits_edits_helper_message(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """A reply without 4–8 contiguous digits is a fat-finger. We edit
    the message to nudge, without burning an attempt slot."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.handle_reply(_reply("hello what", target_id=active_id))

    assert fake_manager.submitted == []
    assert len(fake_bot.edits) == 1
    assert "no digits" in fake_bot.edits[0].text.lower()


@pytest.mark.asyncio
async def test_handle_reply_with_wrong_target_is_ignored(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """Defensive: even if the commands hook somehow forwards a reply
    that doesn't target our message, we drop it silently."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.handle_reply(_reply("123456", target_id=active_id + 99))

    assert fake_manager.submitted == []
    assert fake_bot.edits == []


@pytest.mark.asyncio
async def test_handle_reply_when_idle_is_ignored(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    # Never called on_otp_required → cycle is idle.

    await relay.handle_reply(_reply("123456", target_id=12345))

    assert fake_manager.submitted == []
    assert fake_bot.edits == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot_otp_relay.py -v
```

Expected: 5 new failures (the 3 from Task 4 still pass).

- [ ] **Step 3: Add `owns_reply` + `handle_reply` to the relay**

Add to `AdminBotOTPRelay` (in `backend/src/autotrader/services/admin_bot_otp_relay.py`), inside the class, after `on_otp_required`:

```python
    def owns_reply(self, message: Any) -> bool:
        """Returns True iff this message is a reply targeting the
        relay's active OTP message. Cheap-and-side-effect-free so the
        commands hook can call it on every inbound message.
        """
        if self._active is None:
            return False
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is None:
            return False
        reply_to_id = getattr(reply_to, "id", None)
        return reply_to_id == self._active.message_id

    async def handle_reply(self, message: Any) -> None:
        """Operator replied to the active OTP message. Extract digits
        and submit. Idempotent for stale/no-digit replies — only the
        terminal call into ``manager.submit_otp`` advances the state."""
        if self._active is None:
            return
        if not self.owns_reply(message):
            log.info(
                "otp_relay.reply.stale_target",
                got=getattr(getattr(message, "reply_to_message", None), "id", None),
                active=self._active.message_id,
            )
            return
        text = getattr(message, "text", "") or ""
        match = _OTP_DIGIT_PATTERN.search(text)
        if not match:
            await self._edit_with(
                "❌ No digits found in your reply — reply with just "
                "the code (4–8 digits).",
            )
            return
        code = match.group(1)
        log.info(
            "otp_relay.reply.submitting",
            attempt=self._active.attempt,
            digits=len(code),
        )
        try:
            await self._manager.submit_otp(code)
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.submit_failed", error=str(exc))
            await self._edit_with(
                f"❌ Internal error submitting OTP ({type(exc).__name__}). "
                "Reply /reconnect to retry.",
            )

    async def _edit_with(self, text: str) -> None:
        if self._active is None:
            return
        try:
            await self._admin_bot.edit_message_text(
                self._active.chat_id,
                self._active.message_id,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.edit_failed", error=str(exc))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_admin_bot_otp_relay.py -v
```

Expected: 8 passes (3 from Task 4 + 5 new).

- [ ] **Step 5: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/admin_bot_otp_relay.py autotrader/backend/tests/test_admin_bot_otp_relay.py
git commit -m "feat(autotrader/admin-bot): AdminBotOTPRelay.handle_reply + digit extraction"
```

---

## Task 6: `AdminBotOTPRelay` — resolved + timeout + max-attempts

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_otp_relay.py`
- Modify: `backend/tests/test_admin_bot_otp_relay.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_admin_bot_otp_relay.py`:

```python
# ---------------------------------------------------------------------------
# on_otp_resolved + on_otp_timeout + max-attempts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_otp_resolved_edits_to_connected_and_clears_cycle(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.on_otp_resolved()

    # The terminal edit shows up.
    assert any(
        edit.message_id == active_id and ("connected" in edit.text.lower())
        for edit in fake_bot.edits
    )
    # And the cycle is cleared — a stale reply now is ignored.
    fake_manager.submitted.clear()
    await relay.handle_reply(_reply("123456", target_id=active_id))
    assert fake_manager.submitted == []


@pytest.mark.asyncio
async def test_on_otp_timeout_edits_to_expired_and_clears_cycle(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.on_otp_timeout()

    assert any(
        edit.message_id == active_id and ("expired" in edit.text.lower())
        for edit in fake_bot.edits
    )
    assert relay.owns_reply(_reply("123456", target_id=active_id)) is False


@pytest.mark.asyncio
async def test_attempts_cap_exhausted_edits_terminal_and_stops_accepting(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """After ``max_attempts`` re-prompts in a row, the relay edits to
    a terminal '/reconnect to retry' message and refuses further
    replies until a fresh cycle (attempt=1) starts."""
    relay = _relay(fake_bot, fake_manager)  # default max_attempts=3
    await relay.on_otp_required("p1", attempt=1)
    active_id = fake_bot.sent[0].message_id
    await relay.on_otp_required("p2", attempt=2)
    await relay.on_otp_required("p3", attempt=3)
    # The 4th prompt — beyond the cap — must lock down the cycle.
    await relay.on_otp_required("p4", attempt=4)

    # Last edit is the terminal message.
    last_edit = fake_bot.edits[-1]
    assert last_edit.message_id == active_id
    assert "/reconnect" in last_edit.text.lower()
    # And replies are now dropped.
    fake_manager.submitted.clear()
    await relay.handle_reply(_reply("123456", target_id=active_id))
    assert fake_manager.submitted == []


@pytest.mark.asyncio
async def test_max_attempts_env_var_changes_cap(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """With max_attempts=5, attempts 4 and 5 are still soft retries —
    only attempt=6 triggers the terminal edit."""
    relay = _relay(fake_bot, fake_manager)
    # Replace with a higher cap.
    from autotrader.services.admin_bot_otp_relay import AdminBotOTPRelay  # noqa: PLC0415
    relay = AdminBotOTPRelay(
        manager=fake_manager,
        admin_bot=fake_bot,
        bound_user_id=42,
        max_attempts=5,
    )

    await relay.on_otp_required("p", attempt=1)
    for n in range(2, 6):
        await relay.on_otp_required("p", attempt=n)

    # Attempts 1..5 are within the cap → no '/reconnect' edit yet.
    edits_so_far = " | ".join(e.text for e in fake_bot.edits)
    assert "/reconnect" not in edits_so_far

    await relay.on_otp_required("p", attempt=6)
    assert "/reconnect" in fake_bot.edits[-1].text.lower()


@pytest.mark.asyncio
async def test_fresh_attempt_1_after_terminal_replaces_cycle(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """After the operator hits /reconnect and the manager triggers a
    fresh begin_connect → fresh on_otp_required(attempt=1) — the relay
    sends a NEW message rather than editing the dead one."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    # Force the terminal edit via exhausted attempts.
    for n in range(2, 5):
        await relay.on_otp_required("p", attempt=n)
    assert "/reconnect" in fake_bot.edits[-1].text.lower()

    first_sent = fake_bot.sent[0].message_id

    # Fresh cycle.
    await relay.on_otp_required("fresh prompt", attempt=1)
    assert len(fake_bot.sent) == 2
    assert fake_bot.sent[1].message_id != first_sent
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_admin_bot_otp_relay.py -v
```

Expected: 5 new failures with `AttributeError: 'AdminBotOTPRelay' object has no attribute 'on_otp_resolved'`.

- [ ] **Step 3: Add the resolved/timeout/cap handling**

Add to the `AdminBotOTPRelay` class after `_edit_with`:

```python
    async def on_otp_resolved(self) -> None:
        """Connect completed successfully. Edit to terminal '✅' and
        clear the cycle."""
        if self._active is None:
            return
        await self._edit_with("✅ Connected.")
        self._active = None

    async def on_otp_timeout(self) -> None:
        """The manager's 180s timer fired. Edit to '⏰ expired' and
        clear. No auto-retry — operator decides via /reconnect."""
        if self._active is None:
            return
        await self._edit_with(
            "⏰ OTP expired. Reply /reconnect to retry.",
        )
        self._active = None
```

Now update `_bump_existing_cycle` to enforce the cap. Replace the existing method body with:

```python
    async def _bump_existing_cycle(self, *, prompt: str, attempt: int) -> None:
        assert self._active is not None
        self._active.attempt = attempt
        self._active.broker_prompt = prompt
        if attempt > self._max_attempts:
            await self._edit_with(
                f"❌ OTP failed after {self._max_attempts} attempts. "
                f"Reply /reconnect to retry from scratch.",
            )
            # Cycle becomes inert — refuse further replies until a
            # fresh attempt=1 fires.
            self._active = None
            log.info("otp_relay.exhausted", attempts=attempt)
            return
        text = self._format_retry_prompt(attempt=attempt)
        try:
            await self._admin_bot.edit_message_text(
                self._active.chat_id,
                self._active.message_id,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.edit_failed", error=str(exc))
            return
        log.info(
            "otp_relay.prompt_edited",
            message_id=self._active.message_id,
            attempt=attempt,
        )
```

- [ ] **Step 4: Run all relay tests**

```bash
cd backend && pytest tests/test_admin_bot_otp_relay.py -v
```

Expected: 13 passes (3 from Task 4 + 5 from Task 5 + 5 new).

- [ ] **Step 5: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/admin_bot_otp_relay.py autotrader/backend/tests/test_admin_bot_otp_relay.py
git commit -m "feat(autotrader/admin-bot): OTP relay resolved/timeout + attempts cap"
```

---

## Task 7: `QuotexManager` — wire OTP relay callbacks

**Files:**
- Modify: `backend/src/autotrader/services/quotex_manager.py`
- Modify: `backend/tests/test_broker.py`

- [ ] **Step 1: Re-read the existing `_on_otp_callback`**

```bash
grep -n "_on_otp_callback\|_otp_future\|submit_otp\|_reset_otp" backend/src/autotrader/services/quotex_manager.py
```

You'll see `_on_otp_callback` parks on `asyncio.wait_for(self._otp_future, _OTP_TIMEOUT_SECONDS)` and the `submit_otp` setter resolves that future. Your job is to (a) call `relay.on_otp_required` before parking, (b) call `relay.on_otp_timeout` if `wait_for` raises `TimeoutError`, and (c) call `relay.on_otp_resolved` from `_do_connect`'s `if ok:` branch.

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_broker.py`:

```python
# ---------------------------------------------------------------------------
# OTP relay integration (Task 7 of OTP relay plan)
# ---------------------------------------------------------------------------


class _FakeOTPRelay:
    """Captures every relay-side call so manager tests can assert
    the wiring."""

    def __init__(self) -> None:
        self.required_calls: list[tuple[str, int]] = []
        self.resolved_count = 0
        self.timeout_count = 0

    async def on_otp_required(self, prompt: str, attempt: int) -> None:
        self.required_calls.append((prompt, attempt))

    async def on_otp_resolved(self) -> None:
        self.resolved_count += 1

    async def on_otp_timeout(self) -> None:
        self.timeout_count += 1


def test_manager_calls_relay_on_otp_required(client: TestClient) -> None:
    """When the broker challenges with OTP, the manager invokes
    relay.on_otp_required(prompt, attempt=1) BEFORE parking on the
    180s timer."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    relay = _FakeOTPRelay()
    manager.set_otp_relay(relay)
    FakeQuotex.behavior = "needs_otp"

    headers = _login(client)
    _put_credentials(client, headers)
    # Fire connect; FakeQuotex.connect parks awaiting OTP via the
    # registered callback. The relay must have been called before
    # the response comes back as 202 awaiting_otp.
    client.post("/broker/connect", headers=headers)

    # Submit so the test doesn't leak a parked task.
    client.post("/broker/otp", headers=headers, json={"code": "654321"})

    assert len(relay.required_calls) >= 1
    prompt, attempt = relay.required_calls[0]
    assert attempt == 1
    assert prompt  # non-empty


def test_manager_calls_relay_on_otp_resolved(client: TestClient) -> None:
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    relay = _FakeOTPRelay()
    manager.set_otp_relay(relay)
    FakeQuotex.behavior = "needs_otp"

    headers = _login(client)
    _put_credentials(client, headers)
    client.post("/broker/connect", headers=headers)
    client.post("/broker/otp", headers=headers, json={"code": "654321"})

    # Allow the background connect task to settle.
    import time  # noqa: PLC0415
    for _ in range(20):
        if manager.status().state == "connected":
            break
        time.sleep(0.05)

    assert relay.resolved_count == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_broker.py::test_manager_calls_relay_on_otp_required tests/test_broker.py::test_manager_calls_relay_on_otp_resolved -v
```

Expected: both fail with `AttributeError: 'QuotexManager' object has no attribute 'set_otp_relay'`.

- [ ] **Step 4: Add the relay wiring to `QuotexManager`**

In `backend/src/autotrader/services/quotex_manager.py`:

(a) Add to `__init__`, right after `self._session_store: Any | None = None`:

```python
        # OTP relay. When attached (by lifespan), receives direct
        # calls at the start, timeout, and resolved branches of the
        # OTP cycle. Until attached, the manager's existing UI-side
        # ``awaiting_otp`` state is the sole surface (the dashboard
        # works without the relay).
        self._otp_relay: Any | None = None
        # Per-cycle attempt counter. Reset to 0 on every successful
        # connect; incremented on each ``_on_otp_callback`` entry.
        self._otp_attempt: int = 0
```

(b) Add a setter next to `set_session_store`:

```python
    def set_otp_relay(self, relay: Any | None) -> None:
        """Attach an AdminBotOTPRelay-like object. Duck-typed on
        three async methods: ``on_otp_required(prompt, attempt)``,
        ``on_otp_resolved()``, ``on_otp_timeout()``.
        """
        self._otp_relay = relay
```

(c) Rewrite `_on_otp_callback` to drive the relay. Replace the existing method with:

```python
    async def _on_otp_callback(self, prompt: str) -> str:
        """Hook pyquotex calls when the broker challenges for a code.

        Parks the connect coroutine on a future that ``submit_otp``
        resolves. pyquotex passes the resulting string straight to the
        login form; if the broker rejects it, ``connect()`` returns
        ``(False, ...)`` and the manager state moves to ``error``.

        Re-prompts (broker re-challenges after a wrong code) come in
        as additional invocations of this same callback within one
        ``client.connect()`` await; we track that via
        ``self._otp_attempt`` so the relay can edit (vs. send) the
        Telegram message.
        """
        loop = asyncio.get_running_loop()
        self._otp_future = loop.create_future()
        self._otp_prompt = prompt.strip() or "Enter the code sent to your email."
        self._state = "awaiting_otp"
        self._otp_attempt += 1
        log.info(
            "broker.otp.prompted",
            prompt=self._otp_prompt[:80],
            attempt=self._otp_attempt,
        )
        # Bus-side broadcast for observers (notifier silently no-ops on
        # this event type today; future dashboards / digest tooling
        # may subscribe). Off the critical path — fire-and-forget.
        if self._event_bus is not None:
            try:
                self._event_bus.publish("broker.otp_required", {
                    "prompt": self._otp_prompt,
                    "attempt": self._otp_attempt,
                })
            except Exception as exc:  # noqa: BLE001
                log.warning("broker.otp.bus_publish_failed", error=str(exc))
        if self._otp_relay is not None:
            try:
                await self._otp_relay.on_otp_required(
                    prompt=self._otp_prompt,
                    attempt=self._otp_attempt,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("broker.otp.relay_required_failed", error=str(exc))
        try:
            return await asyncio.wait_for(self._otp_future, timeout=_OTP_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            self._last_error = "OTP timed out"
            if self._otp_relay is not None:
                try:
                    await self._otp_relay.on_otp_timeout()
                except Exception as exc:  # noqa: BLE001
                    log.warning("broker.otp.relay_timeout_failed", error=str(exc))
            raise
        finally:
            self._reset_otp(keep_state=True)
```

(d) In `_do_connect`'s `if ok:` branch, AFTER `self._session_store.save(...)` but inside `if ok:`, add:

```python
                # Notify the relay so the Telegram OTP message edits
                # to '✅ Connected.' Best-effort: failure here is a
                # cosmetic glitch, not a connect failure.
                if self._otp_relay is not None and self._otp_attempt > 0:
                    try:
                        await self._otp_relay.on_otp_resolved()
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "broker.otp.relay_resolved_failed",
                            error=str(exc),
                        )
                self._otp_attempt = 0
```

Also: in the `else:` branch (connect rejected) right after `self._reset_otp()`, reset the attempt counter so a future connect starts at 1:

```python
                self._otp_attempt = 0
```

- [ ] **Step 5: Run the new tests**

```bash
cd backend && pytest tests/test_broker.py::test_manager_calls_relay_on_otp_required tests/test_broker.py::test_manager_calls_relay_on_otp_resolved -v
```

Expected: 2 passes.

- [ ] **Step 6: Run the full broker test suite**

```bash
cd backend && pytest tests/test_broker.py tests/test_broker_resilience.py tests/test_admin_bot_otp_relay.py tests/test_session_store.py -v
```

Expected: all pass (currently 26 + 13 + 7 = 46 tests).

- [ ] **Step 7: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/quotex_manager.py autotrader/backend/tests/test_broker.py
git commit -m "feat(autotrader/broker): wire OTP relay callbacks into QuotexManager"
```

---

## Task 8: `admin_bot_state` — add `get_otp_relay` accessor

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_state.py`

This is a tiny file (50 lines). Mirror the existing `get_pipeline` / `get_quotex` pattern.

- [ ] **Step 1: Edit the file**

Replace the contents of `backend/src/autotrader/services/admin_bot_state.py` with:

```python
"""Lightweight resolver for app.state references used by command handlers.

Handlers shouldn't depend on FastAPI's request context — they live one
layer below, driven by the bot client. This module is set up by
``main.py``'s lifespan and provides typed accessors for the few
``app.state`` objects the handlers need (pipeline ring buffer, broker
manager, notifier, OTP relay). Keeps handlers easy to unit-test by
allowing ``monkeypatch.setattr`` on a single function.
"""

from __future__ import annotations

from typing import Any

_pipeline: Any | None = None
_quotex: Any | None = None
_admin_bot: Any | None = None
_notifier: Any | None = None
_otp_relay: Any | None = None


def attach(
    *,
    pipeline: Any,
    quotex: Any,
    admin_bot: Any | None = None,
    notifier: Any | None = None,
    otp_relay: Any | None = None,
) -> None:
    global _pipeline, _quotex, _admin_bot, _notifier, _otp_relay  # noqa: PLW0603
    _pipeline = pipeline
    _quotex = quotex
    if admin_bot is not None:
        _admin_bot = admin_bot
    if notifier is not None:
        _notifier = notifier
    if otp_relay is not None:
        _otp_relay = otp_relay


def get_pipeline() -> Any | None:
    return _pipeline


def get_quotex() -> Any | None:
    return _quotex


def get_admin_bot() -> Any | None:
    return _admin_bot


def get_notifier() -> Any | None:
    return _notifier


def get_otp_relay() -> Any | None:
    return _otp_relay
```

- [ ] **Step 2: Run existing tests to confirm no regression**

```bash
cd backend && pytest tests/ -k "admin_bot" -v --tb=line
```

Expected: same pass/fail count as before (the 26 pre-existing pyrofork-stub failures are unrelated to this change — confirm none of them regress further; broker + resilience tests stay green).

- [ ] **Step 3: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/admin_bot_state.py
git commit -m "feat(autotrader/admin-bot): admin_bot_state.get_otp_relay accessor"
```

---

## Task 9: `admin_bot_commands` — reply-to-message forwarding

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`

- [ ] **Step 1: Find the exact spot to edit**

```bash
grep -n "build_message_hook\|async def _hook\|if not text.startswith" backend/src/autotrader/services/admin_bot_commands.py
```

You're looking for the body of `_hook` inside `build_message_hook(bot)`. The current first lines are:

```python
async def _hook(_client: Any, message: Any) -> None:
    text = (getattr(message, "text", "") or "").strip()
    if not text.startswith("/"):
        return
```

You need to insert the relay-forward branch **before** the `if not text.startswith` line — so a reply to the OTP message bypasses the command parser entirely.

- [ ] **Step 2: Edit `_hook`**

Replace the opening of `_hook` (from `async def _hook` through the `if not text.startswith` line) with:

```python
    async def _hook(_client: Any, message: Any) -> None:
        # Reply-to-message forwarding for the OTP relay. Must run
        # BEFORE the slash-command check so a digit-only reply
        # (which doesn't start with '/') still reaches the relay.
        from autotrader.services.admin_bot_state import get_otp_relay  # noqa: PLC0415
        relay = get_otp_relay()
        if relay is not None and relay.owns_reply(message):
            # Same auth model as commands: only the bound admin.
            sender_id = int(getattr(message.from_user, "id", 0))
            bound = bot.status().bound_user_id
            if bound is None or sender_id != bound:
                log.info(
                    "admin_bot.otp_reply.unauthorised", sender=sender_id,
                )
                return
            await relay.handle_reply(message)
            return

        text = (getattr(message, "text", "") or "").strip()
        if not text.startswith("/"):
            return
```

- [ ] **Step 3: Run the broker + relay suites to confirm no incidental breakage**

```bash
cd backend && pytest tests/test_broker.py tests/test_broker_resilience.py tests/test_admin_bot_otp_relay.py tests/test_session_store.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/admin_bot_commands.py
git commit -m "feat(autotrader/admin-bot): forward reply-to messages to OTP relay"
```

---

## Task 10: `admin_bot_commands` — `/reconnect` command

**Files:**
- Modify: `backend/src/autotrader/services/admin_bot_commands.py`

- [ ] **Step 1: Find the `COMMANDS` registry and the existing handler shape**

```bash
grep -n "^COMMANDS\|async def handle_unbind\|/unbind" backend/src/autotrader/services/admin_bot_commands.py
```

You'll see a dict literal `COMMANDS: dict[str, Handler] = { ... }` that maps `"/unbind": handle_unbind` etc. Your new handler follows the same `Reply` return pattern.

- [ ] **Step 2: Add the handler**

In `backend/src/autotrader/services/admin_bot_commands.py`, near the other one-liner handlers (right above the `COMMANDS = {...}` block is a good spot), add:

```python
# --------------------------------------------------------------------------
# /reconnect — trigger a fresh broker connect attempt
# --------------------------------------------------------------------------


async def handle_reconnect(_message: Any, _bot: Any) -> Reply:
    """Kick the broker manager into a fresh connect cycle.

    Used by the OTP-relay recovery path: after an OTP timeout or an
    attempts-exhausted terminal state, the relay's message tells the
    operator to '/reconnect'. The new cycle starts with attempt=1 and
    sends a fresh OTP message (no edit of the dead one)."""
    from autotrader.services.admin_bot_state import get_quotex  # noqa: PLC0415
    qx = get_quotex()
    if qx is None:
        return Reply(text="Broker manager not attached.")
    if qx.connected:
        return Reply(text="Broker is already connected — no action taken.")
    if not qx.configured:
        return Reply(
            text="No broker credentials stored. Set them via the dashboard first.",
        )
    try:
        qx.begin_connect()
    except Exception as exc:  # noqa: BLE001
        log.exception("admin_bot.reconnect_failed")
        return Reply(text=f"Reconnect failed to start: {type(exc).__name__}: {exc}")
    return Reply(text="Reconnect triggered — watch for an OTP message in a few seconds.")
```

- [ ] **Step 3: Register it in `COMMANDS`**

Find the `COMMANDS` dict literal and add the new entry. Pick a location alphabetical-ish; right under `"/parser"` or similar is fine. The final block should include:

```python
    "/reconnect": handle_reconnect,
```

- [ ] **Step 4: Also add it to `_HELP_TEXT` so /help shows it**

Find the `_HELP_TEXT` constant (it's a multi-line string near the top of the file). Under the `*Write*` section, after the `/mode demo|real` line, add:

```python
    "  /reconnect — trigger a fresh broker connect (use after OTP timeout)\n"
```

- [ ] **Step 5: Smoke test — make sure the file still parses and tests still pass**

```bash
cd backend && pytest tests/test_broker.py tests/test_broker_resilience.py tests/test_admin_bot_otp_relay.py tests/test_session_store.py -v --tb=line
```

Expected: same pass count as before (46).

- [ ] **Step 6: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/services/admin_bot_commands.py
git commit -m "feat(autotrader/admin-bot): /reconnect command for OTP-recovery path"
```

---

## Task 11: `main.py` — lifespan wiring

**Files:**
- Modify: `backend/src/autotrader/main.py`

- [ ] **Step 1: Find the existing wiring**

```bash
grep -n "admin_bot_state.attach\|QuotexManager(\|AdminBotNotifier\|lifespan\|admin_bot.start" backend/src/autotrader/main.py
```

You'll see (near the top of `lifespan`):
1. `manager = QuotexManager(...)` is constructed.
2. `admin_bot_state.attach(pipeline=..., quotex=manager, ...)` is called.
3. The admin bot is started.

Your job is to construct `SessionStore` + `AdminBotOTPRelay` between (1) and (3), then attach them via `manager.set_session_store(...)`, `manager.set_otp_relay(...)`, and pass `otp_relay=...` to `admin_bot_state.attach`.

- [ ] **Step 2: Edit `lifespan`**

Add the imports near the top of `main.py` (group with other `autotrader.services.*` imports):

```python
from autotrader.services.admin_bot_otp_relay import AdminBotOTPRelay
from autotrader.services.session_store import SessionStore
```

Also add a Fernet import (the file may already have `cryptography` but check):

```python
from cryptography.fernet import Fernet
```

Inside `lifespan`, immediately after `manager = QuotexManager(...)` (look for the existing call — it's the only one), add:

```python
    # --- Broker session persistence ---------------------------------------
    # Encrypted on /data so most container restarts skip OTP. Uses the
    # same Fernet key that already protects broker_credentials.
    session_store_path = Path(settings.data_dir) / "quotex_session.json"
    session_store_fernet = Fernet(settings.fernet_key.get_secret_value().encode())
    session_store = SessionStore(
        path=session_store_path,
        fernet=session_store_fernet,
    )
    manager.set_session_store(session_store)
```

You may need to import `Path` if it isn't already at the top:

```python
from pathlib import Path
```

If `settings.data_dir` doesn't exist as a config field, fall back to a hardcoded `/data` Path (the existing autotrader.db lives there already):

```python
    session_store_path = Path("/data") / "quotex_session.json"
```

(Choose one approach and run `grep -n "data_dir" backend/src/autotrader/config.py` to decide.)

Then, immediately AFTER the admin bot is started (look for `await admin_bot.start()`), add:

```python
    # --- OTP relay (admin bot ↔ broker manager) -------------------------
    # Wires the broker's OTP callback to the admin-bot Telegram client.
    # Bound user is looked up from the persisted GlobalSettings row.
    otp_relay = AdminBotOTPRelay(
        manager=manager,
        admin_bot=admin_bot,
        bound_user_id=admin_bot.status().bound_user_id,
        max_attempts=settings.otp_max_attempts,
    )
    manager.set_otp_relay(otp_relay)
```

Update the existing `admin_bot_state.attach(...)` call to pass `otp_relay=otp_relay`:

```python
    admin_bot_state.attach(
        pipeline=pipeline,
        quotex=manager,
        admin_bot=admin_bot,
        notifier=notifier,
        otp_relay=otp_relay,
    )
```

- [ ] **Step 3: Smoke test — start the app and ensure no import or startup errors**

```bash
cd backend && pytest tests/ -k "test_broker or test_admin_bot_otp_relay or test_session_store or test_health or test_config" -v --tb=short
```

Expected: all pass. If any test fails with `ImportError` or `AttributeError` related to your new code, fix before continuing.

- [ ] **Step 4: Commit**

```bash
cd .. && git add autotrader/backend/src/autotrader/main.py
git commit -m "feat(autotrader/main): wire SessionStore + OTP relay in lifespan"
```

---

## Task 12: Integration test — persisted SSID skips OTP on restart

**Files:**
- Modify: `backend/tests/test_broker.py`

- [ ] **Step 1: Add a new behaviour `"ok_no_otp"` to `FakeQuotex`**

In `backend/tests/test_broker.py`, find `FakeQuotex.connect` and extend the behaviour:

```python
    async def connect(self) -> tuple[bool, str]:
        if FakeQuotex.behavior == "ok":
            self._flip_connected()
            return True, "ok"
        if FakeQuotex.behavior == "ok_no_otp":
            # Caller provided session_data with a token; pyquotex
            # would skip authenticate() in this case. Fake it: succeed
            # WITHOUT invoking on_otp_callback.
            assert self.session_data.get("token"), (
                "ok_no_otp expects pre-warmed session_data — the test "
                "should have wired a SessionStore with a primed payload."
            )
            self._flip_connected()
            return True, "ok"
        if FakeQuotex.behavior == "rejected":
            return False, "auth rejected by broker"
        if FakeQuotex.behavior == "needs_otp":
            assert self.on_otp_callback is not None
            code = await self.on_otp_callback("Enter the code sent to your email:")
            if str(code) == FakeQuotex.valid_otp:
                self._flip_connected()
                return True, "ok"
            return False, "bad otp"
        raise AssertionError(f"unknown behavior: {FakeQuotex.behavior}")
```

- [ ] **Step 2: Write the integration test**

Append to `backend/tests/test_broker.py`:

```python
def test_persisted_ssid_skips_otp_on_second_connect(client: TestClient) -> None:
    """End-to-end: first connect goes through OTP, second connect on
    the same manager reuses the saved session_data and SKIPS the
    on_otp_callback entirely. This is the production restart win the
    spec promises."""
    from autotrader.main import app  # noqa: PLC0415

    manager = app.state.quotex_manager
    store = _FakeSessionStore()
    manager.set_session_store(store)

    headers = _login(client)
    _put_credentials(client, headers)

    # ---------- First connect: OTP-required path ----------------------
    FakeQuotex.behavior = "needs_otp"
    client.post("/broker/connect", headers=headers)
    client.post("/broker/otp", headers=headers, json={"code": "654321"})
    import time  # noqa: PLC0415
    for _ in range(40):
        if manager.status().state == "connected":
            break
        time.sleep(0.05)
    assert manager.status().state == "connected"
    # Session got saved.
    assert len(store.saved_payloads) >= 1

    # Simulate a restart: disconnect, wipe the in-memory manager state
    # but keep the SessionStore (its primed_payload is the last save).
    primed = store.saved_payloads[-1]
    client.post("/broker/disconnect", headers=headers)
    for _ in range(20):
        if manager.status().state == "idle":
            break
        time.sleep(0.05)

    # Build a NEW SessionStore primed with the previous save — this
    # is what a fresh container start sees when reading the on-disk file.
    primed_store = _FakeSessionStore(primed=primed)
    manager.set_session_store(primed_store)

    # ---------- Second connect: SSID-reuse path -----------------------
    FakeQuotex.behavior = "ok_no_otp"
    r = client.post("/broker/connect", headers=headers)
    assert r.status_code == 200, r.text
    for _ in range(40):
        if manager.status().state == "connected":
            break
        time.sleep(0.05)
    assert manager.status().state == "connected"
    # No new OTP cycle this time — but the save still ran (fresh
    # session_data refreshes the on-disk copy).
    assert len(primed_store.saved_payloads) >= 1
```

- [ ] **Step 3: Run the test**

```bash
cd backend && pytest tests/test_broker.py::test_persisted_ssid_skips_otp_on_second_connect -v
```

Expected: 1 pass.

- [ ] **Step 4: Run everything one final time**

```bash
cd backend && pytest tests/ -v --tb=line 2>&1 | tail -20
```

Expected: all OTP-relay-plan tests pass. Pre-existing test_admin_bot.py / test_telegram.py failures from the pyrofork swap are unchanged (not in scope here).

- [ ] **Step 5: Commit**

```bash
cd .. && git add autotrader/backend/tests/test_broker.py
git commit -m "test(autotrader/broker): integration — persisted SSID skips OTP on restart"
```

---

## Plan complete

After Task 12, the implementation is feature-complete and ready for deployment. The final test count should be:

| Suite | Before | After |
|---|---|---|
| `test_broker.py` | 18 | 18 + 2 (session) + 2 (relay) + 1 (integration) = **23** |
| `test_broker_resilience.py` | 6 | 6 |
| `test_admin_bot_otp_relay.py` | 0 | **13** |
| `test_session_store.py` | 0 | **7** |
| `test_config.py` | 4 | 4 + 3 = **7** |
| Pre-existing pyrofork-stub failures in `test_admin_bot.py` / `test_telegram.py` | 26 | 26 (unchanged — not in scope) |

**Deployment (post-implementation):**

1. Rebuild the Docker image: `cd autotrader && docker compose build api`.
2. Restart the container during a quiet window (don't restart while broker is in a fresh-rejection cool-down). The first connect after deploy WILL trigger OTP because no session file exists yet — be ready to type the code.
3. After the first successful connect, verify `/data/quotex_session.json` exists inside the container.
4. Restart the container again to confirm the SSID-reuse path: the second connect should produce `broker.connect.ok` in logs WITHOUT a preceding `broker.otp.prompted`.
5. (Optional) Force an OTP cycle to live-test the relay: `docker exec autotrader-api rm /data/quotex_session.json && docker compose restart api`. Watch for the Telegram message; reply with the code; expect `✅ Connected.` edit within ~5 seconds.
