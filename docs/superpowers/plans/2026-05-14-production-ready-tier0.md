# Production-Ready Tier-0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the six work items from `docs/superpowers/specs/2026-05-14-production-ready-tier0-design.md` so `AUTOTRADER_LIVE_TRADING_ENABLED=true` can be flipped safely.

**Architecture:** Modifies the existing `QuotexManager`, `Executor`, `Pipeline`, and FastAPI lifespan to add: a pre-flight broker probe, a rejection-data probe, a hard reconnect ceiling, a pre-trade WS health gate, graceful drain on shutdown, and smoke tests + runbook. No new modules; all changes plug into existing seams (Phase 3a's lock-split SETUP/FINALIZE phases, the pyquotex `ReconnectSupervisor`, the realtime-price deque already populated by pyquotex).

**Tech Stack:** Python 3.13, FastAPI, pydantic-settings, pyquotex (vendored at `.venv/lib/python3.13/site-packages/pyquotex`), `curl_cffi` (already a dependency), structlog, pytest + `structlog.testing.capture_logs`.

**Test discipline (lessons from Phase 0–4):**
- Every test defers autotrader imports inside the test function with `# noqa: PLC0415` — module-level imports reorder Python's import graph and break unrelated tests (see `tests/test_phase0_instrumentation.py` for the canonical pattern).
- Use the `monkeypatch` fixture (not ad-hoc `MonkeyPatch()`) so teardown actually runs.
- DB-touching tests use an autouse fixture to wipe `trade_attempts`, `parser_configs`, `watched_channels`, `global_settings`.

**Branch:** stay on `fix/broker-disconnect-blindness` (audit phase work is here; spec already committed as `2d287e5`).

---

## File Structure

**Modify (5 files):**
- `autotrader/backend/src/autotrader/config.py` — add 3 settings fields
- `autotrader/backend/src/autotrader/services/quotex_manager.py` — preflight, rejection probe, hard ceiling, `assert_live`
- `autotrader/backend/src/autotrader/services/executor.py` — health-gate call in `_place`, `wait_for_pendings` method
- `autotrader/backend/src/autotrader/services/pipeline.py` — `_draining` latch, refuse in `dispatch`
- `autotrader/backend/src/autotrader/main.py` — lifespan drain sequence

**Create (8 files):**
- `tests/test_phase_tier0_preflight.py`
- `tests/test_phase_tier0_rejection_probe.py`
- `tests/test_phase_tier0_reconnect_ceiling.py`
- `tests/test_phase_tier0_health_gate.py`
- `tests/test_phase_tier0_graceful_drain.py`
- `tests/test_real_money_invariants.py`
- `autotrader/docs/RUNBOOK.md`
- (No new source modules.)

---

## Task 1: Settings — add Tier-0 tunables

**Files:**
- Modify: `autotrader/backend/src/autotrader/config.py` (insert new fields in `Settings` class)
- Test: `autotrader/backend/tests/test_phase_tier0_settings.py` (new)

**Why first:** Tasks 2–6 read these settings. Adding them first means every later test can `monkeypatch.setenv` to control behavior without code edits.

- [ ] **Step 1: Write failing tests**

Create `autotrader/backend/tests/test_phase_tier0_settings.py`:

```python
"""Tier-0 settings tests (audit 2026-05-14).

Three new Tier-0 tunables on Settings:

* ``broker_curl_cffi_profile`` — exposes the currently-hardcoded
  ``firefox144`` so a profile rotation no longer needs a redeploy.
* ``broker_stale_feed_max_age_seconds`` — stale-quote threshold
  for the pre-trade health gate (Task 5).
* ``broker_reconnect_hard_ceiling`` — count of consecutive failed
  reconnects after which the manager stops auto-retrying and flips
  to ``awaiting_manual_recovery`` (Task 4).

All three default to safe values; all three are env-overridable
with the ``AUTOTRADER_`` prefix.
"""

from __future__ import annotations

import pytest


def test_defaults_match_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §3.1/§3.3/§3.4 default values must hold without env overrides."""
    for k in (
        "AUTOTRADER_BROKER_CURL_CFFI_PROFILE",
        "AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS",
        "AUTOTRADER_BROKER_RECONNECT_HARD_CEILING",
    ):
        monkeypatch.delenv(k, raising=False)

    from autotrader.config import Settings  # noqa: PLC0415
    s = Settings()  # type: ignore[call-arg]
    assert s.broker_curl_cffi_profile == "firefox144"
    assert s.broker_stale_feed_max_age_seconds == 10
    assert s.broker_reconnect_hard_ceiling == 20


def test_env_overrides_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can override all three via AUTOTRADER_* env vars."""
    monkeypatch.setenv("AUTOTRADER_BROKER_CURL_CFFI_PROFILE", "safari170")
    monkeypatch.setenv("AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS", "5")
    monkeypatch.setenv("AUTOTRADER_BROKER_RECONNECT_HARD_CEILING", "50")

    from autotrader.config import Settings  # noqa: PLC0415
    s = Settings()  # type: ignore[call-arg]
    assert s.broker_curl_cffi_profile == "safari170"
    assert s.broker_stale_feed_max_age_seconds == 5
    assert s.broker_reconnect_hard_ceiling == 50


def test_hard_ceiling_below_soft_downgrade_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting the hard ceiling lower than the soft-downgrade threshold
    (10, see quotex_manager._SOFT_DOWNGRADE_AFTER_ATTEMPTS) is a config
    error: the operator would never see the 'transient → outage'
    notification transition before the auto-halt fires."""
    monkeypatch.setenv("AUTOTRADER_BROKER_RECONNECT_HARD_CEILING", "5")
    from autotrader.config import Settings  # noqa: PLC0415
    with pytest.raises(ValueError, match="hard_ceiling"):
        Settings()  # type: ignore[call-arg]
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `autotrader/backend/`:
```
.venv/bin/pytest tests/test_phase_tier0_settings.py -v
```
Expected: 3 FAILs with `AttributeError: ... has no attribute 'broker_curl_cffi_profile'`.

- [ ] **Step 3: Add the settings fields**

Edit `autotrader/backend/src/autotrader/config.py`. After the existing `debug_broker_wire: bool = False` field (~line 92), add:

```python
    # ── Tier-0 broker reliability (audit 2026-05-14) ─────────────
    #
    # curl_cffi impersonate profile used by pyquotex's HTTP login
    # path. Currently hardcoded at quotex_manager.py:405; exposing
    # it lets the operator rotate profiles without a redeploy when
    # Cloudflare's bot scoring shifts. Sweep candidates with the
    # probe in RUNBOOK §B.
    broker_curl_cffi_profile: str = "firefox144"

    # Pre-trade WS health gate (Task 5 / spec §3.4). If the latest
    # tick for the asset is older than this, the executor refuses
    # to send the order and marks the attempt ``broker_error``. The
    # martingale ladder is NOT advanced on a health-gate block —
    # the trade never reached the broker.
    broker_stale_feed_max_age_seconds: int = Field(default=10, ge=1, le=300)

    # Hard reconnect ceiling (Task 4 / spec §3.3). After this many
    # consecutive failed reconnect attempts, the manager stops the
    # pyquotex supervisor, disconnects cleanly, and flips state to
    # ``awaiting_manual_recovery``. Operator must run /reconnect.
    # Must be > _SOFT_DOWNGRADE_AFTER_ATTEMPTS (= 10) so the operator
    # sees the 'transient → outage' notification before the auto-halt.
    broker_reconnect_hard_ceiling: int = Field(default=20, ge=11, le=200)
```

Also add a `@model_validator` after the `cors_origins_list` cached property to enforce the hard-ceiling > 10 invariant cleanly (the `ge=11` default Field validator handles it for env-set values, but the validator gives a clearer error message). Replace the trailing `cors_origins_list` block with:

```python
    @cached_property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check_hard_ceiling(self) -> "Settings":
        # Field(ge=11) covers most cases, but pydantic emits a
        # cryptic "Input should be greater than or equal to 11"
        # without naming the config knob. Re-check here for the
        # cleaner error.
        if self.broker_reconnect_hard_ceiling <= 10:
            raise ValueError(
                "broker_reconnect_hard_ceiling must exceed the "
                "soft-downgrade threshold (10); set >= 11"
            )
        return self
```

Add the import at the top of the file alongside the existing `from pydantic import Field, SecretStr`:

```python
from pydantic import Field, SecretStr, model_validator
```

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_phase_tier0_settings.py -v
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add autotrader/backend/src/autotrader/config.py autotrader/backend/tests/test_phase_tier0_settings.py
git commit -m "feat(config): add Tier-0 broker reliability settings (audit 2026-05-14)"
```

(Use a HEREDOC for the full body — see Task 2 Step 10 for the canonical pattern.)

---

## Task 2: Pre-startup connect probe

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/quotex_manager.py` (add `BrokerPreflightFailed`, `_preflight_check`, wire into `_do_connect:SETUP`)
- Test: `autotrader/backend/tests/test_phase_tier0_preflight.py` (new)

**Why before Task 3:** Acceptance criterion #1 requires the probe runs before every connect; the rejection probe (Task 3) is downstream.

- [ ] **Step 1: Write the failing tests**

Create `autotrader/backend/tests/test_phase_tier0_preflight.py`:

```python
"""Pre-startup broker probe tests (audit 2026-05-14, Task 2).

Spec §3.1: before pyquotex burns OTP-supervisor retry budget on a
broker-side hard failure, hit ``qxbroker.com/en/sign-in`` once with
``curl_cffi`` and short-circuit on:

* 403 — Cloudflare fingerprint regression (current incident class)
* 5xx — broker upstream down

Network errors (timeout / connection refused) fall through to
pyquotex — the probe isn't conclusive in that case. 200 continues
silently.

The probe runs inside ``_do_connect:SETUP`` — the locked phase
from the Phase 3a lock-split (audit 2026-05-13 H2).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs


class _FakeResp:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self.content = body


@pytest.mark.asyncio
async def test_preflight_403_blocks_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §3.1 first bullet: a 403 from the sign-in page raises
    ``BrokerPreflightFailed``."""
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        QuotexManager,
        BrokerPreflightFailed,
    )

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=403, body=b"<html>cf</html>")),
    )

    with pytest.raises(BrokerPreflightFailed, match="cloudflare 403"):
        await mgr._preflight_check()


@pytest.mark.asyncio
async def test_preflight_5xx_blocks_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §3.1 second bullet: 5xx raises ``BrokerPreflightFailed``."""
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        QuotexManager,
        BrokerPreflightFailed,
    )

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=503)),
    )

    with capture_logs() as logs:
        with pytest.raises(BrokerPreflightFailed, match="503"):
            await mgr._preflight_check()

    assert any(
        r["event"] == "broker.preflight.upstream_5xx" for r in logs
    ), logs


@pytest.mark.asyncio
async def test_preflight_network_timeout_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §3.1 third bullet: a network-level error does NOT raise —
    pyquotex still gets to try. The log line
    ``broker.preflight.network_error`` is the breadcrumb."""
    import curl_cffi.requests as curl_requests  # noqa: PLC0415
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    def _raise(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise curl_requests.RequestsError("connection timed out")

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get", _raise,
    )

    with capture_logs() as logs:
        await mgr._preflight_check()  # MUST NOT RAISE

    assert any(
        r["event"] == "broker.preflight.network_error" for r in logs
    ), logs


@pytest.mark.asyncio
async def test_preflight_200_continues_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: 200 logs ``broker.preflight.ok``."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=200, body=b"<html>ok</html>")),
    )

    with capture_logs() as logs:
        await mgr._preflight_check()

    assert any(r["event"] == "broker.preflight.ok" for r in logs), logs
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_phase_tier0_preflight.py -v
```
Expected: 4 FAILs with `ImportError: cannot import name 'BrokerPreflightFailed'`.

- [ ] **Step 3: Add the BrokerPreflightFailed exception**

Edit `autotrader/backend/src/autotrader/services/quotex_manager.py`. Near the top of the file alongside the existing exception classes (search for `class QuotexManagerError`), add:

```python
class BrokerPreflightFailed(RuntimeError):
    """Raised by :meth:`QuotexManager._preflight_check` when the
    broker sign-in page returns a hard error (Cloudflare 403,
    upstream 5xx) before pyquotex even tries to connect. Caught
    by ``_do_connect:SETUP`` and converted to ``last_error``.
    """
```

- [ ] **Step 4: Add the _curl_get module-level helper**

Near the existing imports in `quotex_manager.py`, add:

```python
import curl_cffi.requests as _curl_requests
from typing import Any as _Any


def _curl_get(url: str, *, impersonate: str, timeout: float) -> _Any:
    """Module-level indirection so tests can monkeypatch the HTTP
    call without mocking ``curl_cffi.requests.get`` globally."""
    return _curl_requests.get(url, impersonate=impersonate, timeout=timeout)
```

- [ ] **Step 5: Add the _preflight_check method**

Inside the `QuotexManager` class, before `_do_connect`, add:

```python
    _PREFLIGHT_URL = "https://qxbroker.com/en/sign-in"
    _PREFLIGHT_TIMEOUT_SECONDS = 5.0

    async def _preflight_check(self) -> None:
        """Spec §3.1. Short HTTP probe to catch broker hard-failures
        before pyquotex burns OTP-supervisor retry budget on them.

        Falls through silently on network-level errors — the probe
        isn't conclusive in that case, so pyquotex still gets a turn.
        Raises :class:`BrokerPreflightFailed` only when the broker
        explicitly answers with 403 or 5xx.
        """
        profile = settings.broker_curl_cffi_profile
        try:
            resp = await asyncio.to_thread(
                _curl_get,
                self._PREFLIGHT_URL,
                impersonate=profile,
                timeout=self._PREFLIGHT_TIMEOUT_SECONDS,
            )
        except _curl_requests.RequestsError as exc:
            log.warning(
                "broker.preflight.network_error",
                detail=str(exc),
                impersonate_profile=profile,
            )
            return
        except TimeoutError as exc:
            log.warning(
                "broker.preflight.network_error",
                detail=f"timeout: {exc}",
                impersonate_profile=profile,
            )
            return

        status = int(resp.status_code)
        if status == 403:
            log.error(
                "broker.preflight.cloudflare_403",
                impersonate_profile=profile,
                body_bytes=len(getattr(resp, "content", b"") or b""),
            )
            raise BrokerPreflightFailed(
                "cloudflare 403 — fingerprint regression suspected; "
                "see RUNBOOK §B (rotate curl_cffi profile)"
            )
        if 500 <= status < 600:
            log.error(
                "broker.preflight.upstream_5xx",
                status=status,
                impersonate_profile=profile,
            )
            raise BrokerPreflightFailed(
                f"broker upstream returned {status} — "
                "check brokerstatus + retry in 5 min"
            )
        log.info("broker.preflight.ok", status=status)
```

Add `from autotrader.config import settings` to the imports if not already present.

- [ ] **Step 6: Run the tests again**

```
.venv/bin/pytest tests/test_phase_tier0_preflight.py -v
```
Expected: 4 PASSED.

- [ ] **Step 7: Wire the probe into _do_connect:SETUP**

Edit `quotex_manager.py:_do_connect`. Find the SETUP phase (`async with self._timed_lock("do_connect:setup"):` at ~line 371). Right after the lock acquire, BEFORE the existing `try:`, add the preflight call:

```python
        # ── Phase A: lock-held setup. Fast, in-memory + one disk read.
        async with self._timed_lock("do_connect:setup"):
            # Spec §3.1: short HTTP probe before pyquotex burns OTP
            # budget on a broker-side hard failure.
            try:
                await self._preflight_check()
            except BrokerPreflightFailed as exc:
                self._state = "error"
                self._last_error = str(exc)
                log.warning("broker.connect.preflight_failed", detail=str(exc))
                return
            try:
                assert self._email is not None
                # ... existing setup body unchanged
```

- [ ] **Step 8: Add an integration-level test for the wired-in case**

Append to `tests/test_phase_tier0_preflight.py`:

```python
@pytest.mark.asyncio
async def test_do_connect_aborts_on_preflight_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: pre-flight 403 inside _do_connect leaves the
    manager in ``error`` state with operator-readable last_error,
    and pyquotex.Quotex() is never even constructed."""
    from unittest.mock import MagicMock  # noqa: PLC0415
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "autotrader.services.quotex_manager._curl_get",
        MagicMock(return_value=_FakeResp(status=403)),
    )
    sentinel_ctor = MagicMock(
        side_effect=AssertionError("Quotex() must not be called when preflight 403"),
    )
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", sentinel_ctor,
    )

    await mgr._do_connect()

    assert mgr._state == "error"
    assert "cloudflare 403" in (mgr._last_error or "").lower()
    sentinel_ctor.assert_not_called()
```

- [ ] **Step 9: Run the full preflight test file**

```
.venv/bin/pytest tests/test_phase_tier0_preflight.py -v
```
Expected: 5 PASSED.

- [ ] **Step 10: Commit (canonical HEREDOC pattern for all tasks)**

```bash
git add autotrader/backend/src/autotrader/services/quotex_manager.py autotrader/backend/tests/test_phase_tier0_preflight.py
git commit -m "$(cat <<'EOF'
feat(broker): add pre-startup connect probe — Task 2 (audit 2026-05-14)

Short curl_cffi probe against qxbroker.com/en/sign-in inside
_do_connect:SETUP. 403 → broker.preflight.cloudflare_403 + raise
BrokerPreflightFailed. 5xx → broker.preflight.upstream_5xx + raise.
Network errors fall through to pyquotex (probe inconclusive).

Saves pyquotex's OTP supervisor from burning retry budget on
broker-side hard failures (current incident class, 2026-05-14).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Rejection probe (forensic capture)

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/quotex_manager.py` (error branches in `_do_connect`)
- Test: `autotrader/backend/tests/test_phase_tier0_rejection_probe.py` (new)

**Why now:** Once preflight is in, every connect that still fails is a "pyquotex layer or beyond" failure — the kind we need data on for future M6 taxonomy.

- [ ] **Step 1: Write the failing tests**

Create `autotrader/backend/tests/test_phase_tier0_rejection_probe.py`:

```python
"""Rejection-probe tests (audit 2026-05-14, Task 3).

Spec §3.2: when pyquotex's ``client.connect()`` returns a rejection
(either ``(False, reason)`` or a raised exception), emit a single
``broker.connect.rejection_probe`` log line capturing pyquotex
client state. No behavior change — pure observation.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs


class _FakePyqApi:
    auth_status = 0
    is_authenticated = False
    ssid = None
    ws_url = "wss://ws2.qxbroker.com/socket.io/?EIO=4&transport=websocket"


class _FakePyqClient:
    api = _FakePyqApi()

    def __init__(self, *_a, **_kw) -> None:
        pass

    async def connect(self):  # type: ignore[no-untyped-def]
        return False, "Websocket connection rejected."

    def set_account_mode(self, *_a, **_kw) -> None:
        pass


async def _async_noop() -> None:
    return None


@pytest.mark.asyncio
async def test_rejection_probe_fires_when_pyquotex_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``client.connect()`` returns ``(False, reason)``, the
    probe fires with raw_error=reason, ssid_loaded=False, and the
    impersonate profile."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]

    monkeypatch.setattr(
        QuotexManager, "_preflight_check",
        lambda self: _async_noop(),
    )
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", _FakePyqClient,
    )

    with capture_logs() as logs:
        await mgr._do_connect()

    probes = [r for r in logs if r["event"] == "broker.connect.rejection_probe"]
    assert len(probes) == 1, logs
    p = probes[0]
    assert "Websocket connection rejected" in str(p["raw_error"])
    assert p["ssid_loaded"] is False
    assert "elapsed_ms" in p
    assert p["impersonate_profile"] == "firefox144"


@pytest.mark.asyncio
async def test_rejection_probe_silent_on_successful_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean connect must NOT emit a probe."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    class _OkClient(_FakePyqClient):
        async def connect(self):  # type: ignore[no-untyped-def, override]
            return True, "ok"

    mgr = QuotexManager()
    mgr.set_credentials("user@example.com", "pw")  # type: ignore[attr-defined]
    monkeypatch.setattr(
        QuotexManager, "_preflight_check",
        lambda self: _async_noop(),
    )
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", _OkClient,
    )

    with capture_logs() as logs:
        await mgr._do_connect()

    assert [
        r for r in logs if r["event"] == "broker.connect.rejection_probe"
    ] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_phase_tier0_rejection_probe.py -v
```
Expected: `test_rejection_probe_fires` FAILs (no log emitted yet).

- [ ] **Step 3: Add elapsed_ms capture**

In `quotex_manager.py:_do_connect`, find Phase B (the lock-free `await client.connect()` block at ~line 440). Replace:

```python
        # ── Phase B: LOCK-FREE. The blast-radius reduction.
        try:
            ok, reason = await client.connect()
```

With:

```python
        # ── Phase B: LOCK-FREE. The blast-radius reduction.
        import time as _time  # noqa: PLC0415 — keep adjacent to use
        _connect_start_monotonic = _time.monotonic()
        try:
            ok, reason = await client.connect()
```

- [ ] **Step 4: Add probe in the rejection-finalize branch**

In `_do_connect`'s Phase C, find the `else` branch of `if ok:` (the rejection-finalize block at ~line 530). At the very top of that `else` block, BEFORE the existing state-setting code, add:

```python
            else:
                # Spec §3.2: forensic capture for future M6 taxonomy.
                _elapsed_ms = int((_time.monotonic() - _connect_start_monotonic) * 1000)
                _api = getattr(client, "api", None)
                log.warning(
                    "broker.connect.rejection_probe",
                    raw_error=str(reason),
                    error_class="connect_returned_false",
                    elapsed_ms=_elapsed_ms,
                    auth_status=getattr(_api, "auth_status", None),
                    ssid_loaded=bool(getattr(_api, "ssid", None)) if _api else False,
                    is_authenticated=getattr(_api, "is_authenticated", None),
                    ws_url=getattr(_api, "ws_url", None),
                    impersonate_profile=settings.broker_curl_cffi_profile,
                    consecutive_otp_failures=self._consecutive_otp_failures,
                )
                self._state = "error"
                # ... existing rejection-finalize body unchanged below
```

- [ ] **Step 5: Add probe in the Phase B exception branch**

In `_do_connect`'s Phase B, find the `except Exception as exc:` branch (~line 453). At the very top of that branch, BEFORE re-acquiring the lock, add:

```python
        except Exception as exc:
            # Spec §3.2: forensic capture for future M6 taxonomy.
            _elapsed_ms = int((_time.monotonic() - _connect_start_monotonic) * 1000)
            _api = getattr(client, "api", None)
            log.warning(
                "broker.connect.rejection_probe",
                raw_error=str(exc),
                error_class=type(exc).__name__,
                elapsed_ms=_elapsed_ms,
                auth_status=getattr(_api, "auth_status", None),
                ssid_loaded=bool(getattr(_api, "ssid", None)) if _api else False,
                is_authenticated=getattr(_api, "is_authenticated", None),
                ws_url=getattr(_api, "ws_url", None),
                impersonate_profile=settings.broker_curl_cffi_profile,
                consecutive_otp_failures=self._consecutive_otp_failures,
            )
            # ... existing exception body unchanged below
```

- [ ] **Step 6: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_phase_tier0_rejection_probe.py -v
```
Expected: 2 PASSED.

- [ ] **Step 7: Commit**

```bash
git add autotrader/backend/src/autotrader/services/quotex_manager.py autotrader/backend/tests/test_phase_tier0_rejection_probe.py
git commit -m "$(cat <<'EOF'
feat(broker): rejection probe for future M6 taxonomy — Task 3 (audit 2026-05-14)

Single structured log line broker.connect.rejection_probe at both
rejection branches of _do_connect (pyquotex returns False vs.
raises). Captures: raw_error, error_class, elapsed_ms, auth_status,
ssid_loaded, is_authenticated, ws_url, impersonate_profile,
consecutive_otp_failures.

No behavior change. Two weeks of these logs gives real data to
build the M6 dictionary (FOLLOWUPS §B3) from.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Hard reconnect ceiling

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/quotex_manager.py`
- Test: `autotrader/backend/tests/test_phase_tier0_reconnect_ceiling.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `autotrader/backend/tests/test_phase_tier0_reconnect_ceiling.py`:

```python
"""Hard-ceiling reconnect tests (audit 2026-05-14, Task 4).

Spec §3.3 splits the existing cosmetic _HARD_OUTAGE_AFTER_ATTEMPTS
into two constants:

* ``_SOFT_DOWNGRADE_AFTER_ATTEMPTS = 10`` — keep the UX downgrade.
* ``broker_reconnect_hard_ceiling`` (env-overridable, default 20)
  — stop the pyquotex supervisor, disconnect, flip state.

A successful reconnect anywhere below the ceiling resets the
internal counter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_soft_downgrade_at_10_keeps_supervisor_running() -> None:
    """At attempt 10, the event flips recoverable=False but the
    supervisor IS NOT stopped — the operator sees an outage warning
    while pyquotex keeps trying."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    mgr = QuotexManager()

    mgr._client = MagicMock()  # type: ignore[assignment]
    mgr._client.api.reconnect_supervisor.stop = AsyncMock()  # type: ignore[union-attr]
    mgr._client.disconnect = AsyncMock()  # type: ignore[union-attr]

    mgr._on_reconnect_attempt_failed(10)

    mgr._client.api.reconnect_supervisor.stop.assert_not_called()  # type: ignore[union-attr]
    mgr._client.disconnect.assert_not_called()  # type: ignore[union-attr]
    assert mgr._state != "awaiting_manual_recovery"


@pytest.mark.asyncio
async def test_hard_ceiling_disconnects_and_flips_state() -> None:
    """At attempt 20 (default), supervisor is stopped, client
    disconnects, and state flips to ``awaiting_manual_recovery``."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    import asyncio  # noqa: PLC0415

    mgr = QuotexManager()
    mgr._state = "reconnecting"  # type: ignore[assignment]
    mgr._client = MagicMock()  # type: ignore[assignment]
    mgr._client.api.reconnect_supervisor.stop = AsyncMock()  # type: ignore[union-attr]
    mgr._client.disconnect = AsyncMock()  # type: ignore[union-attr]

    mgr._on_reconnect_attempt_failed(20)
    # _halt_at_ceiling runs as a task — give it a tick to drain.
    for _ in range(5):
        await asyncio.sleep(0)

    mgr._client.api.reconnect_supervisor.stop.assert_awaited_once()  # type: ignore[union-attr]
    mgr._client.disconnect.assert_awaited_once()  # type: ignore[union-attr]
    assert mgr._state == "awaiting_manual_recovery"
    assert "ceiling reached" in (mgr._last_error or "").lower()


def test_successful_reconnect_resets_counter() -> None:
    """_on_ws_recovered already clears _consecutive_failed_reconnects."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    mgr = QuotexManager()
    mgr._consecutive_failed_reconnects = 15  # type: ignore[assignment]
    mgr._disconnected_at = None  # type: ignore[assignment]

    mgr._on_ws_recovered()

    assert mgr._consecutive_failed_reconnects == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_phase_tier0_reconnect_ceiling.py -v
```
Expected: `test_hard_ceiling_disconnects_and_flips_state` FAILs.

- [ ] **Step 3: Rename the constant**

In `quotex_manager.py`, find `_HARD_OUTAGE_AFTER_ATTEMPTS = 10` (around line 714). Replace with:

```python
    # ── Reconnect escalation ladder (Task 4 / spec §3.3) ─────────
    # Step 1 — at this many failures, downgrade the admin-bot tone
    # from "transient" to "outage". The supervisor KEEPS RUNNING.
    _SOFT_DOWNGRADE_AFTER_ATTEMPTS = 10
    # Step 2 — env-overridable hard ceiling. Default 20.
    # See settings.broker_reconnect_hard_ceiling.
```

Any existing reference to `_HARD_OUTAGE_AFTER_ATTEMPTS` in the file must be updated to `_SOFT_DOWNGRADE_AFTER_ATTEMPTS` — there's one usage in `_on_reconnect_attempt_failed` itself.

- [ ] **Step 4: Add the _ceiling_halt_task attribute**

In `QuotexManager.__init__`, add (near the other `_*_task` attributes):

```python
        self._ceiling_halt_task: asyncio.Task[None] | None = None
```

- [ ] **Step 5: Rewrite _on_reconnect_attempt_failed and add _halt_at_ceiling**

Replace the body of `_on_reconnect_attempt_failed`:

```python
    def _on_reconnect_attempt_failed(self, failed_count: int) -> None:
        """Called each time pyquotex's supervisor counts a failed retry.

        Spec §3.3 / Task 4: split the old cosmetic
        ``_HARD_OUTAGE_AFTER_ATTEMPTS`` into a soft downgrade (tone
        change) and a hard ceiling (auto-halt).
        """
        hard_ceiling = settings.broker_reconnect_hard_ceiling
        is_outage = failed_count >= self._SOFT_DOWNGRADE_AFTER_ATTEMPTS
        at_ceiling = failed_count >= hard_ceiling

        self._emit_system_error(
            kind="broker.recover_stalled",
            detail=f"reconnect attempt {failed_count} failed; still trying",
            recoverable=not is_outage,
        )

        if at_ceiling:
            log.error(
                "broker.reconnect_ceiling_reached",
                failed_attempts=failed_count,
                ceiling=hard_ceiling,
            )
            self._ceiling_halt_task = asyncio.create_task(
                self._halt_at_ceiling(failed_count, hard_ceiling),
            )

    async def _halt_at_ceiling(
        self, failed_count: int, ceiling: int,
    ) -> None:
        """Stop the pyquotex supervisor and flip state machine.
        Idempotent — guarded by the state check."""
        if self._state == "awaiting_manual_recovery":
            return  # already halted (e.g. OTP exhaustion got there first)

        client = self._client
        if client is not None:
            supervisor = getattr(
                getattr(client, "api", None), "reconnect_supervisor", None,
            )
            if supervisor is not None:
                try:
                    await supervisor.stop()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "broker.reconnect_ceiling.stop_failed",
                        error=str(exc),
                    )
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "broker.reconnect_ceiling.disconnect_failed",
                    error=str(exc),
                )

        async with self._timed_lock("ceiling_halt:finalize"):
            self._state = "awaiting_manual_recovery"
            self._last_error = (
                f"auto reconnect ceiling reached after {failed_count} "
                f"attempts (limit {ceiling}); check account + IP, then "
                f"run /reconnect"
            )
            self._emit_system_error(
                kind="broker.reconnect_ceiling_reached",
                detail=self._last_error,
                recoverable=False,
            )
```

- [ ] **Step 6: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_phase_tier0_reconnect_ceiling.py -v
```
Expected: 3 PASSED.

- [ ] **Step 7: Commit**

```bash
git add autotrader/backend/src/autotrader/services/quotex_manager.py autotrader/backend/tests/test_phase_tier0_reconnect_ceiling.py
git commit -m "$(cat <<'EOF'
feat(broker): hard reconnect ceiling — Task 4 (audit 2026-05-14)

Splits the cosmetic _HARD_OUTAGE_AFTER_ATTEMPTS=10 into:
* _SOFT_DOWNGRADE_AFTER_ATTEMPTS = 10 (tone change only)
* broker_reconnect_hard_ceiling (default 20, env override)

At the hard ceiling: stop pyquotex's ReconnectSupervisor, disconnect
cleanly, flip state to awaiting_manual_recovery, fire
broker.reconnect_ceiling_reached event with recoverable=False.

Prevents retry-spam deepening a soft-flagged account during real
money trading.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Pre-trade WS health gate

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/quotex_manager.py`
- Modify: `autotrader/backend/src/autotrader/services/executor.py:_place`
- Test: `autotrader/backend/tests/test_phase_tier0_health_gate.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `autotrader/backend/tests/test_phase_tier0_health_gate.py`:

```python
"""Pre-trade WS health gate tests (audit 2026-05-14, Task 5).

Spec §3.4: ``QuotexManager.assert_live(asset)`` raises
``BrokerNotLive`` when the broker layer can't accept an order
safely. The executor catches this, marks the attempt
``broker_error``, and does NOT advance the martingale ladder.
"""

from __future__ import annotations

from collections import deque

import pytest


class _FakeApi:
    def __init__(self, asset_tick_age_s: float | None) -> None:
        self.is_authenticated = True
        self.realtime_price: dict[str, deque] = {}
        if asset_tick_age_s is not None:
            import time  # noqa: PLC0415
            ts = time.time() - asset_tick_age_s
            self.realtime_price["EURUSD"] = deque([
                {"time": ts, "price": 1.0750},
            ])


class _FakeClient:
    def __init__(self, asset_tick_age_s: float | None) -> None:
        self.api = _FakeApi(asset_tick_age_s)


@pytest.mark.asyncio
async def test_assert_live_raises_when_not_connected() -> None:
    """State != connected → raise with reason='not_connected'."""
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        QuotexManager, BrokerNotLive,
    )
    mgr = QuotexManager()
    mgr._state = "reconnecting"  # type: ignore[assignment]

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD")
    assert exc_info.value.reason == "not_connected"


@pytest.mark.asyncio
async def test_assert_live_raises_when_not_authed() -> None:
    """State=connected but is_authenticated=False → 'ws_not_authed'."""
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        QuotexManager, BrokerNotLive,
    )
    mgr = QuotexManager()
    mgr._state = "connected"  # type: ignore[assignment]
    client = _FakeClient(asset_tick_age_s=0.5)
    client.api.is_authenticated = False
    mgr._client = client  # type: ignore[assignment]

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD")
    assert exc_info.value.reason == "ws_not_authed"


@pytest.mark.asyncio
async def test_assert_live_raises_when_feed_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Last tick > threshold → 'stale_feed'."""
    monkeypatch.setenv("AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS", "10")
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        QuotexManager, BrokerNotLive,
    )
    import autotrader.config as cfg  # noqa: PLC0415
    cfg.settings = cfg.Settings()  # type: ignore[call-arg]
    import autotrader.services.quotex_manager as qm  # noqa: PLC0415
    qm.settings = cfg.settings

    mgr = QuotexManager()
    mgr._state = "connected"  # type: ignore[assignment]
    mgr._client = _FakeClient(asset_tick_age_s=30.0)  # type: ignore[assignment]

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD")
    assert exc_info.value.reason == "stale_feed"
    assert exc_info.value.detail["age_seconds"] >= 30.0


@pytest.mark.asyncio
async def test_assert_live_raises_when_asset_never_subscribed() -> None:
    """Asset has no realtime_price entry → 'no_tick_seen'."""
    from autotrader.services.quotex_manager import (  # noqa: PLC0415
        QuotexManager, BrokerNotLive,
    )
    mgr = QuotexManager()
    mgr._state = "connected"  # type: ignore[assignment]
    mgr._client = _FakeClient(asset_tick_age_s=None)  # type: ignore[assignment]

    with pytest.raises(BrokerNotLive) as exc_info:
        await mgr.assert_live("EURUSD")
    assert exc_info.value.reason == "no_tick_seen"


@pytest.mark.asyncio
async def test_assert_live_passes_when_fresh() -> None:
    """Authed + tick within threshold → no raise."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    mgr = QuotexManager()
    mgr._state = "connected"  # type: ignore[assignment]
    mgr._client = _FakeClient(asset_tick_age_s=1.0)  # type: ignore[assignment]

    await mgr.assert_live("EURUSD")  # MUST NOT RAISE
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_phase_tier0_health_gate.py -v
```
Expected: 5 FAILs with `ImportError: cannot import name 'BrokerNotLive'`.

- [ ] **Step 3: Add BrokerNotLive + assert_live + _last_tick_age_seconds**

In `quotex_manager.py` near `BrokerPreflightFailed`, add:

```python
class BrokerNotLive(RuntimeError):
    """Raised by :meth:`QuotexManager.assert_live` when the executor
    must not send an order. Caller marks the attempt
    ``broker_error`` and does NOT advance the martingale ladder —
    the trade never reached the broker.
    """

    def __init__(self, reason: str, **detail: object) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
```

Inside `QuotexManager`, add:

```python
    def _last_tick_age_seconds(self, asset: str) -> float | None:
        """Read the age of the most-recent tick from pyquotex's
        ``realtime_price[asset]`` deque. Returns ``None`` if no tick
        has ever been received for the asset (treated as
        ``no_tick_seen`` by ``assert_live``).
        """
        client = self._client
        if client is None:
            return None
        api = getattr(client, "api", None)
        if api is None:
            return None
        rt = getattr(api, "realtime_price", None) or {}
        ticks = rt.get(asset)
        if not ticks:
            return None
        try:
            latest = ticks[-1]
        except IndexError:
            return None
        ts = latest.get("time") if isinstance(latest, dict) else None
        if ts is None:
            return None
        return max(0.0, time.time() - float(ts))

    async def assert_live(self, asset: str) -> None:
        """Spec §3.4 health gate. Raises :class:`BrokerNotLive` when
        the executor must not send an order on this asset."""
        if self._state != "connected":
            raise BrokerNotLive("not_connected", state=self._state)
        client = self._client
        if client is None or not getattr(
            getattr(client, "api", None), "is_authenticated", False,
        ):
            raise BrokerNotLive("ws_not_authed")
        age = self._last_tick_age_seconds(asset)
        if age is None:
            raise BrokerNotLive("no_tick_seen", asset=asset)
        threshold = float(settings.broker_stale_feed_max_age_seconds)
        if age > threshold:
            raise BrokerNotLive(
                "stale_feed",
                asset=asset,
                age_seconds=age,
                threshold=threshold,
            )
```

Add `import time` to the top of the file if absent.

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_phase_tier0_health_gate.py -v
```
Expected: 5 PASSED.

- [ ] **Step 5: Modify executor._place to call assert_live**

Edit `autotrader/backend/src/autotrader/services/executor.py:_place`. At the very top of the method, before any broker call, add:

```python
    async def _place(
        self,
        attempt: TradeAttempt,
        signal: ParsedSignal,
        decision: RiskDecision,
    ) -> TradeAttempt:
        # ── Tier-0 Task 5: pre-trade WS health gate (spec §3.4) ──
        from autotrader.services.quotex_manager import BrokerNotLive  # noqa: PLC0415
        try:
            await self.manager.assert_live(signal.asset)
        except BrokerNotLive as exc:
            log.warning(
                "executor.healthgate_blocked",
                attempt_id=attempt.id,
                reason=exc.reason,
                **exc.detail,
            )
            async with AsyncSessionLocal() as session:
                row = await session.get(TradeAttempt, attempt.id)
                if row is not None:
                    row.status = "broker_error"
                    row.broker_error = f"healthgate:{exc.reason}"
                    await session.commit()
            self._publish_decision(
                attempt,
                outcome="broker_error",
                reason=f"healthgate:{exc.reason}",
            )
            return attempt
        # ── End Task 5 ──

        # ... existing _place body unchanged
```

If `_publish_decision` doesn't exist on the Executor, look for the existing decision-publishing pattern (search for `pipeline.decision` or `event_bus.publish` in the file) and reuse that idiom inline.

The load-bearing invariant: `record_outcome` (which advances the martingale ladder) MUST NOT be called on this branch. The `return attempt` after publishing the decision ensures that.

- [ ] **Step 6: Write the integration test for the executor path**

Append to `tests/test_phase_tier0_health_gate.py`:

```python
@pytest.mark.asyncio
async def test_place_blocked_marks_broker_error_and_does_not_tick_martingale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §3.4 load-bearing invariant: a health-gate block leaves
    the attempt ``broker_error`` and does NOT advance the martingale
    ladder. record_outcome must not be called from the block path.

    Wire this against existing tests/test_executor.py fixtures —
    follow the pattern from that file for Executor construction
    and TradeAttempt seeding. The shape:

    1. Build an Executor with mgr.assert_live raising BrokerNotLive.
    2. Seed a pending TradeAttempt row.
    3. Stub record_outcome with a spy.
    4. Call executor._place(attempt, signal, decision).
    5. Assert row.status='broker_error', row.broker_error starts
       with 'healthgate:', and the spy was never called.

    See tests/test_phase2_idempotency.py for the autouse DB-wipe
    fixture pattern to keep rows from leaking.
    """
    # Implementer: this is a STUB. Replace it with a real
    # implementation BEFORE committing Task 5. The skip below is
    # not a placeholder — it's the contract for the work that
    # must complete in this same task before commit.
    pytest.fail(
        "Integration test stub — wire to test_executor.py fixtures "
        "before committing Task 5 (see docstring for the recipe)",
    )
```

The `pytest.fail` ensures the implementer cannot commit this task without writing the real test — a forcing function.

- [ ] **Step 7: Replace the stub with a real test**

Read `tests/test_executor.py` to see how Executor + FakeQuotex + TradeAttempt seeding is wired. Replicate that pattern in `test_place_blocked_marks_broker_error_and_does_not_tick_martingale`. Then run:

```
.venv/bin/pytest tests/test_phase_tier0_health_gate.py -v
```
Expected: 6 PASSED.

- [ ] **Step 8: Commit**

```bash
git add autotrader/backend/src/autotrader/services/quotex_manager.py autotrader/backend/src/autotrader/services/executor.py autotrader/backend/tests/test_phase_tier0_health_gate.py
git commit -m "$(cat <<'EOF'
feat(broker): pre-trade WS health gate — Task 5 (audit 2026-05-14)

QuotexManager.assert_live(asset) raises BrokerNotLive with one of:
  not_connected, ws_not_authed, no_tick_seen, stale_feed.

Reads pyquotex's existing client.api.realtime_price[asset] deque
for tick freshness; stale threshold from
AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS (default 10).

executor._place catches the exception, marks the attempt
broker_error with reason 'healthgate:<reason>', and crucially does
NOT call record_outcome — the trade never reached the broker, so
the martingale ladder stays put.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Graceful drain on shutdown

**Files:**
- Modify: `autotrader/backend/src/autotrader/services/pipeline.py`
- Modify: `autotrader/backend/src/autotrader/services/executor.py`
- Modify: `autotrader/backend/src/autotrader/main.py`
- Test: `autotrader/backend/tests/test_phase_tier0_graceful_drain.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `autotrader/backend/tests/test_phase_tier0_graceful_drain.py`:

```python
"""Graceful drain tests (audit 2026-05-14, Task 6).

Spec §3.5: on lifespan shutdown, refuse new dispatches and wait up
to 300s for in-flight trades to settle before tearing down.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs


def test_pipeline_draining_latch_is_one_way() -> None:
    """``start_draining()`` sets ``_draining = True`` — no resume path."""
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415

    class _StubMgr:
        assets: tuple[str, ...] = ()

    class _StubExec:
        async def submit(self, **_kw: object) -> None: ...

    pipe = Pipeline(manager=_StubMgr(), executor=_StubExec())  # type: ignore[arg-type]
    assert pipe._draining is False
    pipe.start_draining()
    assert pipe._draining is True


@pytest.mark.asyncio
async def test_dispatch_refuses_when_draining() -> None:
    """A dispatch call after ``start_draining`` logs ``pipeline.refused``
    with reason='draining' and returns without calling the executor."""
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415

    class _StubMgr:
        assets: tuple[str, ...] = ("EURUSD",)

    submit_calls: list[object] = []

    class _SpyExec:
        async def submit(self, **kwargs: object) -> None:
            submit_calls.append(kwargs)

    pipe = Pipeline(manager=_StubMgr(), executor=_SpyExec())  # type: ignore[arg-type]
    pipe.start_draining()

    with capture_logs() as logs:
        await pipe.dispatch(
            RawMessage(text="CALL EURUSD 1m", chat_id=-1, sender_id=42),
        )

    refused = [r for r in logs if r["event"] == "pipeline.refused"]
    assert refused, logs
    assert refused[0]["reason"] == "draining"
    assert submit_calls == []


@pytest.mark.asyncio
async def test_wait_for_pendings_returns_when_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll loop returns 0 the moment list_pending() is empty."""
    from autotrader.services.executor import Executor  # noqa: PLC0415
    import autotrader.services.executor as executor_module  # noqa: PLC0415

    calls = {"count": 0}

    async def _list_pending_stub(_session: object) -> list[object]:
        calls["count"] += 1
        return []

    monkeypatch.setattr(executor_module, "list_pending", _list_pending_stub)

    instance = Executor.__new__(Executor)
    remaining = await instance.wait_for_pendings(timeout=5.0)

    assert remaining == 0
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_wait_for_pendings_times_out_with_remaining_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pendings never drain, the helper returns the remaining
    count and logs ``lifespan.drain.timeout``."""
    from autotrader.services.executor import Executor  # noqa: PLC0415
    import autotrader.services.executor as executor_module  # noqa: PLC0415

    class _FakeRow:
        id = 99

    async def _list_pending_stub(_session: object) -> list[object]:
        return [_FakeRow(), _FakeRow()]

    monkeypatch.setattr(executor_module, "list_pending", _list_pending_stub)

    instance = Executor.__new__(Executor)
    with capture_logs() as logs:
        remaining = await instance.wait_for_pendings(timeout=0.5)

    assert remaining == 2
    timeouts = [r for r in logs if r["event"] == "lifespan.drain.timeout"]
    assert timeouts, logs
    assert timeouts[0]["remaining"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/pytest tests/test_phase_tier0_graceful_drain.py -v
```
Expected: 4 FAILs.

- [ ] **Step 3: Add Pipeline._draining latch**

Edit `autotrader/backend/src/autotrader/services/pipeline.py`. In `Pipeline.__init__`, add:

```python
        # Spec §3.5 / Task 6: one-way drain latch. Set by the FastAPI
        # lifespan during shutdown; once True, dispatch() refuses all
        # new signals with reason='draining'. No resume path.
        self._draining: bool = False
```

Add the method (anywhere in the class):

```python
    def start_draining(self) -> None:
        """One-way latch (spec §3.5). After this call, ``dispatch()``
        refuses all new signals. Called once by the FastAPI lifespan
        shutdown sequence; never reset."""
        self._draining = True
        log.info("pipeline.draining")
```

In `dispatch()` (first lines, before any other work):

```python
    async def dispatch(self, message: RawMessage, ...) -> None:
        if self._draining:
            log.info(
                "pipeline.refused",
                reason="draining",
                chat_id=getattr(message, "chat_id", None),
            )
            return
        # ... existing dispatch body
```

(Adjust the signature ellipsis to match the actual signature in the file — keep the early-return shape.)

- [ ] **Step 4: Add Executor.wait_for_pendings**

Edit `autotrader/backend/src/autotrader/services/executor.py`. Add the method near `shutdown()`:

```python
    async def wait_for_pendings(self, timeout: float) -> int:
        """Spec §3.5 / Task 6. Poll ``list_pending()`` every 2s.
        Return 0 once drained, or the remaining count on timeout.

        The lifespan calls this BEFORE :meth:`shutdown` so in-flight
        trades get a chance to settle naturally rather than ending up
        as ``reconcile_pending`` work on the next restart (which
        intentionally doesn't advance the martingale ladder).
        """
        deadline = asyncio.get_running_loop().time() + float(timeout)
        poll_interval = 2.0
        while True:
            async with AsyncSessionLocal() as session:
                remaining = await list_pending(session)
            if not remaining:
                log.info("lifespan.drain.complete")
                return 0
            if asyncio.get_running_loop().time() >= deadline:
                log.warning(
                    "lifespan.drain.timeout",
                    remaining=len(remaining),
                )
                return len(remaining)
            await asyncio.sleep(poll_interval)
```

- [ ] **Step 5: Wire into the FastAPI lifespan**

Edit `autotrader/backend/src/autotrader/main.py`. Find the lifespan shutdown block (the part after `yield`). Right before the existing `await executor.shutdown()` call (line ~315), add:

```python
    # ── Tier-0 Task 6: graceful drain (spec §3.5) ───────────────
    pipeline.start_draining()
    await executor.wait_for_pendings(timeout=300.0)
    # ── End Task 6 ──
    await executor.shutdown()
```

- [ ] **Step 6: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_phase_tier0_graceful_drain.py -v
```
Expected: 4 PASSED.

- [ ] **Step 7: Commit**

```bash
git add autotrader/backend/src/autotrader/services/pipeline.py autotrader/backend/src/autotrader/services/executor.py autotrader/backend/src/autotrader/main.py autotrader/backend/tests/test_phase_tier0_graceful_drain.py
git commit -m "$(cat <<'EOF'
feat(lifespan): graceful drain on shutdown — Task 6 (audit 2026-05-14)

* Pipeline._draining one-way latch + start_draining()
* dispatch() refuses with reason='draining' once latched
* Executor.wait_for_pendings(timeout) — polls list_pending every 2s
* FastAPI lifespan: start_draining → wait 300s → existing shutdown

Prevents 'lost outcome → ladder corruption' on planned deploys
mid-session. reconcile_pending on restart remains as the backstop
for crash recovery.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Real-money invariants smoke harness

**Files:**
- Test: `autotrader/backend/tests/test_real_money_invariants.py` (new)

**These are the load-bearing tests** — the only ones whose failure blocks the env-var flip.

- [ ] **Step 1: Write the three tests**

Create `autotrader/backend/tests/test_real_money_invariants.py`:

```python
"""Real-money invariants (audit 2026-05-14, Task 7).

These three tests assert the invariants the operator must trust
before flipping ``AUTOTRADER_LIVE_TRADING_ENABLED=true``. They run
against the standard ``FakeQuotex`` test seam (no real broker).

If ANY fails, the env-var flip is blocked.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_daily_max_loss_blocks_mid_ladder() -> None:
    """The risk gate must block a martingale recovery trade just as
    eagerly as a base trade once daily_max_loss has been reached —
    even though the ladder is 'mid-recovery'."""
    from datetime import UTC, datetime  # noqa: PLC0415
    from sqlmodel import select  # noqa: PLC0415
    from autotrader.models.martingale_state import (  # noqa: PLC0415
        MartingaleState,
    )
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.parsers.base import ParsedSignal  # noqa: PLC0415
    from autotrader.services.risk_gate import evaluate  # noqa: PLC0415
    from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415

    await init_db()

    async with AsyncSessionLocal() as session:
        gs = GlobalSettings(
            id=1, kill_switch_engaged=False, pipeline_active=True,
            daily_max_loss=30.0, daily_max_stake=0.0,
            max_concurrent_trades=0,
        )
        session.add(gs)
        pc = ParserConfig(
            id=1, name="t", parser_id="generic", enabled=True,
            default_stake=10.0, martingale_enabled=True,
            martingale_multiplier=2.0, max_streak=4,
        )
        session.add(pc)
        loss = TradeAttempt(
            chat_id=-1, parser_config_id=1, asset="EURUSD",
            direction="call", stake=10.0, status="lost",
            profit=-30.0, created_at=datetime.now(UTC),
        )
        session.add(loss)
        st = MartingaleState(
            parser_config_id=1, current_streak=1, last_stake=10.0,
            last_payout=0.0, current_win_streak=0,
        )
        session.add(st)
        await session.commit()

    async with AsyncSessionLocal() as session:
        gs_row = (await session.scalars(select(GlobalSettings))).one()
        pc_row = (await session.scalars(select(ParserConfig))).one()

        decision = await evaluate(
            session=session,
            signal=ParsedSignal(
                asset="EURUSD", direction="call",
                duration_seconds=60, stake=None, fire_at=None,
            ),
            parser_config=pc_row,
            settings=gs_row,
            account_mode="DEMO",
            live_trading_enabled_env=False,
            broker_connected=True,
        )

    assert not decision.allowed, decision
    assert "daily loss limit" in decision.reason


async def test_kill_switch_blocks_all_signals_with_decision_row() -> None:
    """Operator engages kill switch → every parseable signal is
    blocked at the risk gate with reason mentioning 'kill switch'.

    Wire to existing tests/test_pipeline.py fixtures during
    implementation. Recipe:
    1. Seed GlobalSettings with kill_switch_engaged=True.
    2. Build a Pipeline with the standard fake manager+executor.
    3. Dispatch a parseable CALL EURUSD 1m message.
    4. Assert: executor.submit was never called.
    5. Assert: one pipeline.decision row exists with outcome='block'
       and reason containing 'kill switch'.
    """
    pytest.fail(
        "Stub — wire to test_pipeline.py fixtures before committing Task 7",
    )


async def test_disconnect_mid_ladder_does_not_advance_state() -> None:
    """A trade that ends ``broker_error`` (not 'lost') MUST NOT
    advance ``MartingaleState.current_streak``. This is the load-
    bearing invariant — a stale-feed block must never look like a
    loss to the ladder.

    Drives the contract via the Task 5 health-gate path:
    1. Stub mgr.assert_live to raise BrokerNotLive("stale_feed").
    2. Seed MartingaleState(current_streak=1, last_stake=10.0).
    3. Call executor._place(attempt, signal, decision).
    4. Re-read MartingaleState — current_streak MUST still be 1.
    """
    pytest.fail(
        "Stub — wire to Task 5 fixtures + record_outcome spy "
        "before committing Task 7",
    )
```

The `pytest.fail` stubs are forcing functions — the implementer must replace them with real test bodies before commit.

- [ ] **Step 2: Replace the two stubs with real test bodies**

Read `tests/test_pipeline.py` and `tests/test_executor.py` to harvest the existing fixture patterns. Replace each `pytest.fail(...)` block with the real wiring. At commit time, ZERO `pytest.fail` calls may remain in this file.

- [ ] **Step 3: Run all three tests**

```
.venv/bin/pytest tests/test_real_money_invariants.py -v
```
Expected: 3 PASSED.

- [ ] **Step 4: Commit**

```bash
git add autotrader/backend/tests/test_real_money_invariants.py
git commit -m "$(cat <<'EOF'
test(real-money): smoke-test invariants — Task 7 (audit 2026-05-14)

Three load-bearing tests, run on every CI:
* kill_switch_blocks_all_signals_with_decision_row
* daily_max_loss_blocks_mid_ladder
* disconnect_mid_ladder_does_not_advance_state

These are the only tests whose failure blocks flipping
AUTOTRADER_LIVE_TRADING_ENABLED=true.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: RUNBOOK.md

**Files:**
- Create: `autotrader/docs/RUNBOOK.md`

No tests — this is documentation. Quality bar: every section answers "what do I type or click?" with concrete commands.

- [ ] **Step 1: Write the runbook**

Create `autotrader/docs/RUNBOOK.md` with the following content:

```markdown
# Autotrader Production Runbook

> Last revised 2026-05-14 (Tier-0 production-ready cutover).
> Companion docs:
> * docs/superpowers/specs/2026-05-14-production-ready-tier0-design.md
> * AUDIT_2026-05-13.md / FOLLOWUPS_2026-05-13.md

## A. Flipping the env var (going live)

Pre-flight checklist — ALL must be true:

- 7+ days of demo-mode soak with no Sentry critical events.
- Acceptance criteria §7 of the spec verified against logs:
  `docker logs autotrader-api 2>&1 | grep broker.preflight.ok | wc -l`
  must be > 0 and roughly equal the number of connect attempts.
- `tests/test_real_money_invariants.py` is green on master.
- Backup retention is healthy:
  `docker compose run --rm api ls /data/backups/ | wc -l` should be ≥ 24.

Flip the switch:

1. Open `.env` on the VPS: `nano /opt/autotrader/.env`.
2. Set the smallest workable caps:
   ```
   AUTOTRADER_LIVE_TRADING_ENABLED=true
   ```
   And in the dashboard (or via the API):
   ```
   daily_max_stake=5
   daily_max_loss=10
   max_concurrent_trades=1
   ```
3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
4. Watch the admin bot Telegram for the first `broker.connect.ok`.
5. Wait for the first signal — verify the decision row shows
   `outcome=allow` and the trade settles correctly against the
   broker history.

Raise caps only after 7 consecutive days with no daily-loss trigger.

## B. Broker rejection diagnostics

If you see `broker.connect.rejection_probe` or
`broker.preflight.cloudflare_403` in logs:

1. Grab the latest probe line:
   ```
   docker logs autotrader-api 2>&1 \
     | grep broker.connect.rejection_probe | tail -1 | jq .
   ```
2. Decide based on `error_class`:

   | `error_class` | Likely cause | Action |
   |---|---|---|
   | `connect_returned_false` + `raw_error` mentions "Websocket connection rejected" | Soft-flagged IP/account | Try incognito web login at qxbroker.com. If that fails, wait 30 min, retry. If still failing after 2 hours, broker support. |
   | `WebsocketConnectionRejectedException` | Same as above, raised flavour | Same as above. |
   | `broker.preflight.cloudflare_403` (separate log line) | curl_cffi fingerprint regression | Rotate curl_cffi profile (see §B.1) |
   | `broker.preflight.upstream_5xx` (separate log line) | Broker maintenance | Check brokerstatus, retry in 5–15 min |
   | Anything unmapped | Unknown error class | File issue with the probe log dump |

### B.1 Rotating the curl_cffi profile

```bash
# 1. Sweep candidates against the broker's sign-in page.
cd /opt/autotrader
docker compose run --rm api python -c "
import curl_cffi.requests as r
for p in ['firefox144','firefox147','safari170','chrome120','chrome146']:
    try:
        x = r.get('https://qxbroker.com/en/sign-in', impersonate=p, timeout=5)
        print(f'{p:14s} status={x.status_code} bytes={len(x.content)}')
    except Exception as e:
        print(f'{p:14s} ERROR {type(e).__name__}: {e}')
"

# 2. Pick the first that returns 200.
# 3. Set in .env:
echo 'AUTOTRADER_BROKER_CURL_CFFI_PROFILE=<chosen>' >> .env

# 4. Restart:
docker compose restart api
```

## C. Halt the bot (kill switch)

From the admin bot Telegram chat:

1. Send `/kill` — instantly blocks all new signals at the risk gate.
2. Confirm: send `/status` — `kill_switch_engaged: true` should appear.
3. In-flight trades continue to settle naturally (their watchers
   are independent of the gate).

Resume:

1. Send `/resume` from the admin bot.
2. Verify `/status` shows `kill_switch_engaged: false`.

## D. Restore from backup

```bash
# 1. List available backups (hourly, 24 retained).
docker compose run --rm api ls -la /data/backups/

# 2. Pick one (filenames are sortable: autotrader-YYYYMMDDTHHMMSSZ.db).
# 3. Stop the API.
docker compose stop api

# 4. Replace the live DB.
docker compose run --rm api cp /data/backups/<chosen>.db /data/autotrader.db

# 5. Restart.
docker compose start api
```

## E. Reconnect ceiling escalation

If you see `broker.reconnect_ceiling_reached` in logs (or the
admin bot pings with "auto reconnect ceiling reached"):

1. The pyquotex supervisor is STOPPED. No more retries until you act.
2. Check three things in order:
   - Account state: log in via web at qxbroker.com. If denied:
     it's a soft-flag — wait it out (typically 30 min – 2 h) or
     contact broker support.
   - IP state: try the IP-sweep diagnostic in §F.1.
   - Credentials: in the dashboard, re-enter the password via
     `/broker/credentials` (force a fresh login on `/reconnect`).
3. Once verified, send `/reconnect` from the admin bot.

## F. Live-tick / stale-feed diagnostics

If you see `executor.healthgate_blocked reason=stale_feed`:

1. Likely cause: the WS is up but quotex stopped pushing ticks
   for that asset. Common for low-volume pairs during off-hours.
2. Either wait — the next tick lifts the gate — or lower the
   threshold in `.env`:
   `AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS=30` and restart.

## F.1 IP-sweep diagnostic

```bash
# From the VPS:
curl -sI https://qxbroker.com/en/sign-in | head -1
```

Should be HTTP/2 200. If 403, the VPS IP is flagged. Workaround:
route through a residential proxy / VPN until the flag lifts.

## G. Out-of-scope items (see FOLLOWUPS_2026-05-13.md)

These are intentionally NOT in Tier-0; promote when their trigger
fires:

* Decimal money columns (§A1) — trigger: $X.99 / $X.01 drift recurs.
* Alembic adoption (§A2) — trigger: a non-trivial migration.
* Event-bus persistence (§A3) — trigger: P0 incident needs replay.
* M6 broker auth error taxonomy (§B3) — trigger: 2 weeks of
  `broker.connect.rejection_probe` data.
* Frontend retry/backoff (§A4) — trigger: deploy-window flashes.
```

- [ ] **Step 2: Commit**

```bash
git add autotrader/docs/RUNBOOK.md
git commit -m "$(cat <<'EOF'
docs: production runbook for Tier-0 cutover — Task 8 (audit 2026-05-14)

Operator-facing playbook for going live: env-var flip checklist,
broker rejection diagnostics, kill-switch procedure, backup
restore, reconnect-ceiling escalation, stale-feed diagnostics,
IP-sweep, and a pointer to FOLLOWUPS for out-of-scope items.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final verification + acceptance criteria readiness

**Files:** none modified — this is a verification pass.

- [ ] **Step 1: Full pytest run**

From `autotrader/backend/`:
```
.venv/bin/pytest tests/ -q --no-header 2>&1 | tail -40
```

Every test from Tasks 1–7 must PASS. The 31 pyrofork-baseline failures from FOLLOWUPS §C are expected to remain — verify the count is exactly 31 (no new regressions):

```
.venv/bin/pytest tests/ -q --no-header 2>&1 | grep -E "passed|failed" | tail -1
```

If the failure count is > 31, find and fix new regressions before declaring this task done.

- [ ] **Step 2: Acceptance criteria smoke-check**

Verify each of the spec §7 acceptance criteria has a corresponding log event in the codebase by grepping for the event keys:

```bash
for evt in \
  "broker.preflight.ok" \
  "broker.preflight.network_error" \
  "broker.preflight.cloudflare_403" \
  "broker.preflight.upstream_5xx" \
  "broker.connect.rejection_probe" \
  "broker.reconnect_ceiling_reached" \
  "executor.healthgate_blocked" \
  "pipeline.refused" \
  "lifespan.drain.complete" \
  "lifespan.drain.timeout"; do
    echo "=== $evt ==="
    grep -rn "\"$evt\"" autotrader/backend/src/ | head -2
done
```

Every line must show ≥ 1 hit in `src/`. A missing hit means a log line that the acceptance criterion depends on was never wired — fix the corresponding task.

- [ ] **Step 3: Static checks**

```
.venv/bin/ruff check autotrader/backend/src/ autotrader/backend/tests/
.venv/bin/mypy autotrader/backend/src/autotrader/
```

No new ruff or mypy errors (compare against the `master` baseline).

- [ ] **Step 4: Final commit (optional — only if any cleanup happened)**

If steps 2–3 surfaced anything that needs fixing, commit those fixes separately:

```bash
git add <files>
git commit -m "$(cat <<'EOF'
fix(tier0): address ruff/mypy/acceptance-criteria gaps (audit 2026-05-14)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If nothing needs cleanup, skip this step.

- [ ] **Step 5: Final summary**

```bash
git log --oneline master..HEAD
```

Expected output: 8 commits (Tasks 1–8) plus optional cleanup.

```bash
git diff --name-only master..HEAD | sort
```

Expected files:

- `autotrader/backend/src/autotrader/config.py`
- `autotrader/backend/src/autotrader/main.py`
- `autotrader/backend/src/autotrader/services/executor.py`
- `autotrader/backend/src/autotrader/services/pipeline.py`
- `autotrader/backend/src/autotrader/services/quotex_manager.py`
- `autotrader/backend/tests/test_phase_tier0_graceful_drain.py`
- `autotrader/backend/tests/test_phase_tier0_health_gate.py`
- `autotrader/backend/tests/test_phase_tier0_preflight.py`
- `autotrader/backend/tests/test_phase_tier0_reconnect_ceiling.py`
- `autotrader/backend/tests/test_phase_tier0_rejection_probe.py`
- `autotrader/backend/tests/test_phase_tier0_settings.py`
- `autotrader/backend/tests/test_real_money_invariants.py`
- `autotrader/docs/RUNBOOK.md`
- `docs/superpowers/specs/2026-05-14-production-ready-tier0-design.md` (already committed as `2d287e5`)
- `docs/superpowers/plans/2026-05-14-production-ready-tier0.md` (this file)

If any file in the spec's §10 touch list is missing from the diff, the corresponding task is incomplete.

Tier-0 implementation done. Hand off to operator for the 7-day demo-mode soak (engineering work ends here; the soak is patience).

---

## Self-Review Checklist (run by the planner before committing)

- ✅ Every spec section §3.1–§3.6 maps to a Task (Tasks 2/3/4/5/6/7+8).
- ✅ Spec §11 open questions resolved: PR shape = single sequence of commits on `fix/broker-disconnect-blindness`; `_last_tick_age_seconds` reads from pyquotex `client.api.realtime_price`; `curl_cffi_profile` setting added in Task 1.
- ✅ No "TODO" / "TBD" placeholders.
- ✅ Two `pytest.fail(...)` stubs in Tasks 5 and 7 are explicitly flagged as forcing functions; the next step in each task requires replacing them with real test bodies before commit.
- ✅ Type names consistent across tasks: `BrokerPreflightFailed`, `BrokerNotLive` (with `.reason: str`, `.detail: dict`), `_SOFT_DOWNGRADE_AFTER_ATTEMPTS`, `broker_reconnect_hard_ceiling`, `_draining`, `wait_for_pendings(timeout)`.
- ✅ Spec §10 file touch list verified against Task 9 final-summary expected files (matches).
