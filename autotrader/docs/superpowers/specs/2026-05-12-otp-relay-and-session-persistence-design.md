# OTP relay + broker-session persistence — design spec

**Date:** 2026-05-12
**Status:** Approved (ready for plan)
**Branch target:** new feature branch off `master`
**Related:** [admin-telegram-bot design (2026-05-09)](2026-05-09-admin-telegram-bot-design.md);
broker disconnect-blindness fix (commit `9ce720b` on branch
`fix/broker-disconnect-blindness`)

## Problem

The autotrader is moving from practice-mode to real-money trading. The
broker (Quotex) intermittently issues a one-time PIN challenge on
auto-reconnect — emails the operator a six-digit code and parks the
authorisation flow until the code is submitted. Pyquotex already
exposes this as an `on_otp_callback` parking on an
`asyncio.wait_for(_otp_future, timeout=180)`; `QuotexManager` mirrors
that into a `state="awaiting_otp"` field and a `submit_otp()` method.

**The unresolved gap:** there's no way to submit the code when the
operator is not at the dashboard. Overnight, on a flight, during a
meeting — the 180-second window elapses, the manager state collapses to
`error`, and trades stop firing until the operator next opens the web
UI. For real-money unattended trading this is a hard production
blocker.

A secondary observation from the 2026-05-12 incident logs: the
in-process `session_data` is wiped on every container restart, so
every restart forces a full HTTP login through Cloudflare — which the
broker can elect to challenge with OTP. Persisting the session would
make most restarts skip the OTP path entirely.

## Goals

1. When `_on_otp_callback` fires, push a Telegram message to the bound
   admin user (via the existing `AdminBot` Pyrogram client) prompting
   for the code, accept a reply-to-message reply containing the code,
   submit it to the manager — all within the 180-second window the
   manager already enforces.
2. Persist the broker session (`token`, `cookies`, `user_agent`) to an
   encrypted file on the data volume; rehydrate it on startup so the
   manager's first connect attempt of each restart skips the HTTP
   login when the SSID is still valid.
3. Surface the OTP cycle in the existing event-bus + admin-bot
   notifier so the operator-facing wiring is observable end-to-end.
4. Stay strictly inside the existing decomposition pattern — one new
   module per new responsibility, no expansion of existing files
   beyond surgical hook additions.

## Non-goals

- Reducing the *rate* at which the broker challenges with OTP beyond
  the SSID persistence path. Activity-probe pings to keep the session
  "fresh" are explicitly deferred to a follow-up spec; we will measure
  OTP cadence in production with this spec deployed before deciding
  whether more reduction is needed.
- Multi-account session storage. The autotrader has only ever bound a
  single broker email; YAGNI applies until that changes.
- Falling back to an alternative OTP delivery channel (SMS, TOTP). The
  broker dictates the channel; this spec only relays whatever pyquotex
  surfaces.

## User-visible behaviour

### Happy path

1. Container running unattended at 3 AM. Broker rotates session,
   requires OTP.
2. Operator's phone receives a Telegram message from the admin bot:
   `🔐 Broker needs OTP — reply to this message with the code we
   just emailed you (180s).`
3. Operator long-presses the message → Reply → types the 6-digit code
   → sends.
4. Within ~3 s the bot edits the same message to `✅ Connected.` and
   trades resume.

### Wrong-code path

1. Operator submits a wrong code; broker re-prompts.
2. Bot edits the same message to `❌ Wrong code — reply with the new
   code we just emailed you (attempt 2/3).`
3. Up to `AUTOTRADER_OTP_MAX_ATTEMPTS` (default 3) attempts allowed;
   after the cap, message edited to `❌ OTP failed after 3 attempts.
   Reply /reconnect to retry from scratch.` and manager moves to
   `error` state.

### Timeout path

1. 180-second window elapses with no reply.
2. Bot edits the message to `⏰ OTP expired. Reply /reconnect to
   retry.` Manager state collapses to `error`. **No auto-retry** — the
   supervisor does not hammer the broker. The 02:17–02:55 UTC
   2026-05-12 incident demonstrated that aggressive auto-retry through
   a broker rejection state extends the lockout.

### Startup with persisted SSID (the side-benefit)

On every successful auth, the manager writes the session dict to
`/data/quotex_session.json` (Fernet-encrypted). On container restart,
the manager loads that file before calling `client.connect()`.
Pyquotex's `_connect_unlocked` skips `authenticate()` when
`session_data["token"]` is set, so the manager proceeds straight to
the WS handshake and `send_ssid()`. If the SSID is still valid, no OTP
is needed; if it's expired, pyquotex's existing reconnect supervisor
clears it and falls back through the full login → OTP relay path.

## Component architecture

### New files

#### `autotrader/backend/src/autotrader/services/admin_bot_otp_relay.py`

The heart of the spec. Owns a single object — `AdminBotOTPRelay` —
that:

- Is constructed in the FastAPI lifespan with refs to the
  `QuotexManager`, `AdminBot`, and `TradeEventBus`.
- Receives direct calls from the manager (`on_otp_required`,
  `on_otp_timeout`, `on_otp_resolved`) — direct, not via the event
  bus, so the relay completes its Telegram round-trip *before* the
  manager parks on the 180-second future. See "Wiring" below.
- Tracks one `_ActiveCycle` at a time (`message_id`, `chat_id`,
  `attempt`, `expires_at`). The cycle is replaced on every new
  attempt=1 invocation.
- Exposes `handle_reply(message)` — invoked by the existing
  admin-bot message hook when a Telegram message is a reply to the
  active cycle's `message_id`.
- Edits the same Telegram message across re-prompts; sends a new one
  only at the start of a fresh cycle.

#### `autotrader/backend/src/autotrader/services/session_store.py`

A small isolated module — three functions and no business logic:

```python
class SessionStore:
    def __init__(self, path: Path, fernet: Fernet): ...
    def load(self) -> dict | None
    def save(self, session_data: dict) -> None
    def clear(self) -> None
```

`save()` is atomic (`write tmp → os.replace → final path`). `load()`
returns `None` on a missing file, corrupt file, or wrong key —
never raises. Schema is the pyquotex-native
`{"token": str, "cookies": str, "user_agent": str}`; no schema version
field (YAGNI; we control both sides).

#### `autotrader/backend/tests/test_admin_bot_otp_relay.py`

~12 unit tests for the relay module (listed under Testing strategy).

#### `autotrader/backend/tests/test_session_store.py`

~6 unit tests for the session store.

### Files edited (surgical changes only)

#### `autotrader/backend/src/autotrader/services/quotex_manager.py`

- New optional `_otp_relay` attribute + `set_otp_relay(relay)` setter,
  wired from lifespan.
- `_on_otp_callback` calls `await relay.on_otp_required(prompt,
  attempt)` *before* parking on `asyncio.wait_for`. Also publishes a
  `broker.otp_required` event for observers (notifier may no-op
  since the relay owns the user-facing surface).
- New `_handle_otp_timeout` hook called from the `TimeoutError`
  branch of `_on_otp_callback` that invokes `relay.on_otp_timeout()`.
- In `_do_connect` `if ok:` branch (next to `_start_status_watcher`):
  `session_store.save(client.session_data)`.
- New `_load_persisted_session(client)` called *before* `client.
  connect()` in `_do_connect`: if the store returns a dict, sets
  `client.session_data` so pyquotex skips the HTTP login.
- New `_session_store` attribute + constructor parameter so tests
  can inject a fake.
- ~40 LoC changed total.

#### `autotrader/backend/src/autotrader/services/admin_bot_commands.py`

- At the very top of `_hook` (inside `build_message_hook`), before
  the `if not text.startswith("/")` drop: check
  `message.reply_to_message`; if it exists and the relay accepts the
  target message_id as its active cycle, await
  `relay.handle_reply(message)` and return. ~10 LoC added.
- Add `/reconnect` to `COMMANDS` — handler calls
  `manager.begin_connect()`. ~10 LoC.

#### `autotrader/backend/src/autotrader/services/admin_bot_state.py`

Add `_otp_relay` slot and `get_otp_relay()` accessor, matching the
existing pattern. ~5 LoC.

#### `autotrader/backend/src/autotrader/main.py`

Lifespan startup: construct `SessionStore` and `AdminBotOTPRelay`,
attach via `manager.set_otp_relay(relay)` and
`admin_bot_state.attach(..., otp_relay=relay)`. Lifespan shutdown:
no-op (no tasks owned by relay). ~10 LoC.

#### `autotrader/backend/src/autotrader/config.py`

Add `otp_max_attempts: int = Field(default=3, ge=1, le=10)` setting,
env `AUTOTRADER_OTP_MAX_ATTEMPTS`. ~2 LoC.

### File responsibility summary

| File | Owns | Does NOT own |
|---|---|---|
| `admin_bot.py` | Pyrogram client lifecycle | Anything OTP-specific |
| `admin_bot_commands.py` | Slash command routing + auth gate | OTP reply parsing (forwards) |
| `admin_bot_notify.py` | Trade/risk/system events → Telegram | OTP (different lifecycle) |
| `admin_bot_otp_relay.py` | OTP message lifecycle | Client; auth gate; commands |
| `session_store.py` | Atomic encrypted dict I/O | Anything else; pure I/O |
| `quotex_manager.py` | Connection state machine | Telegram; OTP UX |

## Data flow

### Wiring decision: direct call, not event-bus

The relay registers via `manager.set_otp_relay(relay)` and the manager
calls `await relay.on_otp_required(prompt, attempt)` directly inside
`_on_otp_callback`. **Direct, not via the event bus.** Reason: the
relay needs to complete its Telegram round-trip (send message → get
`message_id`) *before* the manager parks on its 180-second future.
Event-bus consumers run on separate tasks; there's a race window where
the manager's timer would start while the relay is still constructing
the message.

The manager *also* publishes a `broker.otp_required` event for
observers (notifier, future dashboards) — but those observers are off
the critical path.

### Happy-path sequence

1. Manager invokes `_on_otp_callback(prompt)`.
2. Manager publishes `broker.otp_required` event (fire-and-forget).
3. Manager awaits `relay.on_otp_required(prompt, attempt=1)`.
4. Relay invokes `admin_bot.send(chat_id, text)` → receives
   `message_id`. Stores cycle state.
5. Relay returns. Manager parks on `asyncio.wait_for(_otp_future,
   timeout=180)`.
6. Operator replies on Telegram. The admin-bot message hook receives
   the inbound message, sees `reply_to_message`, asks the relay if
   this targets its active cycle, forwards `handle_reply(message)`.
7. Relay extracts digits via `\d{4,8}` regex, calls
   `manager.submit_otp(code)`. Manager resolves `_otp_future`;
   `_on_otp_callback` returns `code` to pyquotex.
8. Pyquotex completes the HTTP login. `connect()` returns ok.
9. Manager invokes `relay.on_otp_resolved()`. Relay edits the
   tracked message to `✅ Connected.` and clears the cycle.
10. Manager writes `client.session_data` to `session_store.save()`.

### Wrong-code sequence (attempt > 1)

Pyquotex re-invokes `_on_otp_callback` after the broker rejects the
first SSID. Manager calls `relay.on_otp_required(prompt, attempt=2)`.
The relay observes an existing `_ActiveCycle` (any cycle in `active`
state) and **edits** its stored `message_id` rather than sending a
new message, bumping the attempt counter in the text. Same flow
continues.

### Timeout sequence

The manager's `asyncio.wait_for(_otp_future, 180)` raises
`TimeoutError`. The existing `except` block in `_on_otp_callback` runs;
the new addition calls `relay.on_otp_timeout()`. Relay edits the
message to `⏰ OTP expired. Reply /reconnect to retry.` Manager
collapses to `error` state. No auto-retry.

### Startup-with-persisted-SSID sequence

1. Lifespan calls `manager.begin_connect()`.
2. Manager's `_do_connect` calls `_load_persisted_session(client)`
   which reads `session_store.load()`. If non-None, sets
   `client.session_data`.
3. `await client.connect()` → pyquotex's `_connect_unlocked` checks
   `self.session_data.get("token")`, skips `authenticate()`, proceeds
   to WS + `send_ssid()`.
4. If broker accepts SSID → state="connected" → `session_store.save()`
   refreshes the file.
5. If broker rejects → pyquotex's reconnect supervisor clears
   `state.SSID`, retries with full HTTP login → triggers OTP relay
   path → normal recovery.

## State management

`AdminBotOTPRelay` owns a single per-cycle state:

```python
@dataclass
class _ActiveCycle:
    message_id: int           # Telegram message we're editing
    chat_id: int              # bound admin's chat
    attempt: int              # 1..max
    expires_at: datetime      # safety re-check; manager owns the real timer
    broker_prompt: str        # original prompt for forensic logging
```

Three lifecycle states:

| State | Reached via | Allowed transitions |
|---|---|---|
| `idle` | initial / `on_resolved` / `on_timeout` / `on_exhausted` | → `active` (via `on_otp_required` attempt=1) |
| `active` | `on_otp_required` attempt=1 | → `active` (re-prompt, attempt++); → `idle` (resolved/timeout/exhausted) |
| `disabled` | `admin_bot.status().state != "running"` | → `idle` (re-checked lazily on `on_otp_required`) |

`handle_reply(message)` is only valid in `active` state and only for
the current `message_id`. Anything else: log at info, drop.

The relay holds **no persistent state**. A container restart mid-cycle
loses the old cycle; `manager.begin_connect()` restarts fresh.

## Session persistence

`SessionStore` is constructed in the lifespan with:
- `path = settings.data_dir / "quotex_session.json"` (defaults to
  `/data/quotex_session.json` in the container).
- `fernet = Fernet(settings.fernet_key)` — reuses
  `AUTOTRADER_FERNET_KEY`, the same key already protecting the
  `broker_credentials` table.

**Atomic write:** `save()` writes to `${path}.tmp`, then
`os.replace()` to `${path}`. Without this, a crash mid-write corrupts
the file and the next `load()` fails to decrypt → returns `None` →
fresh OTP cycle. Safe-by-default but wasteful.

**Schema:**
`{"token": str, "cookies": str, "user_agent": str}`. No version field
(YAGNI; we control both ends).

**Hooks in `quotex_manager.py`:**
- Before `client.connect()`: `if (session := store.load()): client.
  session_data = session`.
- After successful connect: `store.save(client.session_data)`.
- On `disconnect()`: **do not clear** — we want the SSID for next
  startup. The reconnect supervisor's "drop SSID" path (when broker
  rejects an SSID it issued) is already handled by pyquotex; we
  mirror that to disk on the next save() naturally (since
  `session_data["token"]` will be cleared by then).

**Logging hygiene:** never log the cleartext token. The structured
log line is `log.info("broker.session.persisted", token_present=bool
(session.get("token")))` and nothing more.

**Telegram formatting:** all relay messages are sent and edited as
**plain text** — no `parse_mode`. The broker's prompt string is
included verbatim. This eliminates any markdown-injection risk if
the broker ever emits special characters (`*`, `_`, `` ` ``,
brackets) in its prompt; we don't have to write or maintain an
escaper. Emojis (🔐 ✅ ❌ ⏰) are inline Unicode and are unaffected
by parse mode.

## Error handling

| Failure | Detection | Recovery |
|---|---|---|
| Telegram `send_message` raises | `AdminBot.send` re-raises (existing) | Relay catches, logs at warning, leaves no `_ActiveCycle`. Manager's 180s timer still parks, times out via existing path. Operator sees stale `awaiting_otp` in the dashboard. |
| Reply payload has no digits | `handle_reply` regex `\d{4,8}` | Edit message to `❌ No digits found — reply with the code`. No attempt increment. |
| Reply targets wrong/stale `message_id` | `handle_reply` compares against `active_cycle.message_id` | Silently ignore + log at info. |
| `manager.submit_otp` raises | Caught in `handle_reply` | Edit message to `❌ Internal error — reply /reconnect`. Log at warning. |
| Encrypted session file corrupt or key rotated | `SessionStore.load` catches Fernet error | Return `None`, log at warning. Manager falls back to full HTTP login → OTP relay path. |
| Bot is disabled (no token) when OTP fires | `on_otp_required` checks `admin_bot.status().state == "running"` | Return without sending. Manager's `awaiting_otp` state is still visible in the dashboard for manual submission. |
| Two `_on_otp_callback` invocations race | `manager._lock` + `_otp_future is None or done()` checks (existing) | Second call sees `attempt > 1`; relay edits instead of sends. |
| Container crashes mid-cycle | n/a — durable state is only the persisted session file | Old Telegram message remains in chat as orphan; harmless. New container restarts fresh. |

## Configuration & deployment

**New env var** (added to `.env.example` + `docker-compose.yml`):

```
# Maximum OTP attempts per cycle before the relay gives up and edits
# the message to the terminal '/reconnect to retry' state.
AUTOTRADER_OTP_MAX_ATTEMPTS=3
```

**No new secrets.** The existing `AUTOTRADER_FERNET_KEY` is reused for
session-file encryption. The existing `TELEGRAM_BOT_TOKEN` already
gates the admin bot.

**Data-volume contract:** `/data/quotex_session.json` joins
`/data/autotrader.db` as the persistent state. The Docker volume named
`autotrader-data` survives container restart but not volume deletion;
operators wiping the volume accept that the next start will require
an OTP.

## Testing strategy

### `test_admin_bot_otp_relay.py` (~12 tests)

- `test_on_otp_required_sends_telegram_message` — relay calls
  `admin_bot.send` with the correct text shape, stores returned
  message_id in its `_ActiveCycle`.
- `test_on_otp_required_attempt_2_edits_existing` — re-prompt with
  attempt=2 invokes `client.edit_message_text` on the stored
  message_id; does not call `send` again.
- `test_handle_reply_extracts_digits_and_submits` — reply with
  "code: 123456" yields exactly one `manager.submit_otp("123456")`
  call.
- `test_handle_reply_ignores_wrong_message_id` — reply with
  `reply_to_message.id` not equal to active cycle is dropped.
- `test_handle_reply_ignores_when_idle` — reply with no active cycle
  is dropped.
- `test_attempts_cap_exhausted_edits_terminal_message` — after
  `_MAX_OTP_ATTEMPTS` wrong codes, relay edits to terminal `❌
  failed — /reconnect`; further replies dropped.
- `test_on_timeout_edits_message` — manager invokes `on_timeout`;
  relay edits the message to the `⏰ expired` text.
- `test_disabled_bot_short_circuits` — `admin_bot.status().state !=
  "running"` → `on_otp_required` returns silently, no cycle stored.
- `test_handle_reply_rejects_non_digit_payload` — "hello" → no
  submit, edit shows the "no digits" hint.
- `test_max_attempts_env_var` — `AUTOTRADER_OTP_MAX_ATTEMPTS=5`
  overrides the default; the 4th attempt is allowed.
- `test_on_otp_resolved_edits_to_connected` — success path edits to
  `✅ Connected.` and clears the cycle.
- `test_concurrent_on_otp_required_replaces_cycle` — calling
  `on_otp_required(attempt=1)` while a cycle is active replaces the
  old cycle with the new one (rare; should not happen in normal
  flow but the manager `_lock` guarantee can lapse if a developer
  removes it later).

### `test_session_store.py` (~6 tests)

- `test_save_load_roundtrip` — write a dict, read it back, identical.
- `test_load_missing_file_returns_none`.
- `test_load_corrupt_file_returns_none` — write garbage bytes, load
  returns None.
- `test_load_wrong_fernet_key_returns_none` — encrypted with key A,
  decrypted with key B, returns None.
- `test_save_is_atomic` — monkeypatch `os.replace` to raise; verify
  the original file is untouched (or absent if first save).
- `test_save_does_not_log_token_value` — capture structlog output,
  assert the cleartext token string never appears.

### Integration test (extends `test_broker.py`)

- `test_persisted_ssid_skips_otp_on_restart` — first
  `QuotexManager` instance runs through `FakeQuotex.behavior=
  "needs_otp"` with a fake relay that supplies the code. After
  `disconnect()`, construct a second manager with the same
  `root_path`. The second manager finds the persisted session and
  `connect()` succeeds without ever invoking the OTP callback.

## Out of scope (explicit deferrals)

- **Activity-probe pings** to keep the broker session "fresh" between
  natural reconnects. Will measure OTP cadence post-deployment of
  this spec and revisit only if needed.
- **Multi-account session persistence**. Single broker email
  assumption holds; revisit when multi-account is on the roadmap.
- **Telegram bot's own OTP MFA**. We trust the admin's already-bound
  Telegram user_id as the sole reply authority (matches existing
  command auth model — `admin_bot_commands.py:729`).
- **Alternative OTP channels** (SMS, TOTP). Pyquotex surfaces
  whatever the broker emits; we relay it.

## Acceptance criteria

1. Unit-test count: 18 new tests pass (12 relay + 6 store) plus the
   integration test; total of 19 net-new passing tests.
2. No regression in `test_broker.py` (currently 18 tests + 6 from
   the resilience spec = 24, all passing post-change).
3. End-to-end smoke test in production:
   - Restart the container with a fresh image. First connect skips
     OTP (uses persisted SSID from previous session).
   - Force an OTP cycle (admin manually clears the session file +
     restarts). Operator receives the Telegram message within ~2
     seconds of `broker.otp.prompted`. Reply with the code, see
     `✅ Connected.` edit within ~5 seconds. `broker.connect.ok`
     appears in logs.
4. Telegram message hygiene: no logged token leakage; messages are
   minimal text only (no markdown injection vectors from the
   broker's prompt string).
