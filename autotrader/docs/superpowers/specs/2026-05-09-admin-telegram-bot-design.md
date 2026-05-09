# Admin Telegram Bot — Design Spec

**Date:** 2026-05-09
**Status:** Approved (brainstorming complete, ready for implementation plan)
**Owner:** autotrader

## 1. Problem & goal

Today the autotrader is operated entirely through its web dashboard. When the
operator is away from their laptop they cannot:

- See trades being placed / settled in real time.
- Pause a noisy or misbehaving channel.
- Disable a parser that started misfiring.
- Hit the kill switch when something looks wrong.

Phone access to the dashboard exists but is awkward (login, navigation,
slow on bad connections). A first-class **admin Telegram bot** turns the
operator's phone into a remote-control surface that mirrors the
dashboard's most-used controls, plus pushes a curated event firehose so
the operator hears about issues without polling.

The bot is **additive**: when it's offline (bad token, blocked, network)
the rest of the trader runs unaffected.

## 2. Non-goals

- **Not** a multi-tenant or multi-user surface. One bot binds to one
  admin Telegram user_id at a time.
- **Not** a replacement for the dashboard. Anything the bot can change,
  the dashboard can also change; anything complex (parser editing,
  template authoring, dialog browsing) stays dashboard-only.
- **Not** a webhook / public bot. We use Pyrogram in long-poll/MTProto
  mode; no public URL, no SSL plumbing, no inbound port.
- **Not** a Telegram-side scheduler. There is no "pause until 9am" — see
  §6 (pause semantics) for why.

## 3. Architecture

### 3.1 Transport

The bot runs as a **separate Pyrogram `Client`** (bot mode, authenticated
with `bot_token`) alongside the existing userbot client managed by
`TelegramManager`. Both are MTProto sessions; they share no state.

We deliberately do **not** reuse the userbot for admin commands —
mixing admin commands with the operator's normal Telegram traffic is a
security and UX hazard (a stolen phone or a careless tap in Saved
Messages could fire `/killswitch off` or `/mode real`).

### 3.2 New module layout

All new code lives under `backend/src/autotrader/`:

```text
services/admin_bot.py          AdminBot class — owns the Pyrogram bot client,
                               attaches handlers, exposes start()/stop().
services/admin_bot_commands.py One async function per command. Pure handlers,
                               no Pyrogram-Client field access — they receive
                               (message, services) and return reply text/markup.
services/admin_bot_notify.py   Subscribes to the existing TradeEventBus,
                               formats events, applies per-class rate limits,
                               sends to the bound admin via the bot client.
models/settings.py             +admin_telegram_user_id and 4 notify-class flags
                               on the existing GlobalSettings singleton row.
config.py                      +TELEGRAM_BOT_TOKEN setting (Pydantic SecretStr).
main.py                        Lifespan starts AdminBot after TelegramManager;
                               stops it cleanly on shutdown.
routers/admin_bot.py           Tiny REST endpoint: GET status + POST /unbind
                               (the dashboard's escape hatch).
tests/test_admin_bot.py        Unit + integration tests with a fake Pyrogram.
docs/admin-bot.md              Operator-facing setup guide.
```

### 3.3 Wiring diagram

```text
                         ┌──────────────────────────┐
                         │  TradeEventBus (existing)│
                         └────────┬─────────────────┘
                                  │ subscribe()
            ┌─────────────────────┴─────────────────┐
            ▼                                       ▼
    feed/ws  (existing)                       AdminBotNotifier
                                                    │ format + dedupe + rate-limit
                                                    ▼
                                         ┌─────────────────────┐
   admin DMs (Telegram) ────────────────►│   AdminBot client   │◄── /command handlers
                                         │  (pyrogram bot mode)│      (single asyncio.Lock)
                                         └─────────────────────┘
```

The notifier subscribes to the same in-process bus that already drives
the dashboard's WebSocket feed. **No producer code changes.** Two new
event types (`risk.rejected` and `system.error`) get added to the
existing publishers — these would be useful even without the bot.

### 3.4 Database changes

One additive migration on the `global_settings` row, following the
existing `_migrate_in_place` pattern in `db.py`:

```sql
ALTER TABLE global_settings ADD COLUMN admin_telegram_user_id INTEGER NULL;
ALTER TABLE global_settings ADD COLUMN admin_notify_placed         BOOLEAN NOT NULL DEFAULT 1;
ALTER TABLE global_settings ADD COLUMN admin_notify_settled        BOOLEAN NOT NULL DEFAULT 1;
ALTER TABLE global_settings ADD COLUMN admin_notify_risk_rejected  BOOLEAN NOT NULL DEFAULT 1;
ALTER TABLE global_settings ADD COLUMN admin_notify_system_error   BOOLEAN NOT NULL DEFAULT 1;
```

No new tables. No new model files. The bot's state is one row.

### 3.5 Configuration

One new env var:

```dotenv
TELEGRAM_BOT_TOKEN=<bot_token from @BotFather>
```

Loaded into `TelegramSettings` (sibling of the existing `api_id` /
`api_hash`). When unset, `AdminBot.start()` becomes a no-op and logs
`admin_bot.disabled` once at startup — the rest of the app runs as
today.

## 4. Auth lifecycle

### 4.1 Binding

```text
                ┌─────────────────────┐
                │ admin_user_id NULL? │
                └────────┬────────────┘
                ┌────────▼────────┐
                │ /start received │
                └────────┬────────┘
            yes ┌───────┴────────┐ no
                ▼                ▼
   bind: write user_id   is sender == bound id?
   reply: "✅ bound"      ┌───────┴───────┐
                       yes│               │no
                          ▼               ▼
                   route to handler  reply: "this bot is bound
                                      to another admin · ask
                                      them to /unbind"
                                      (log + drop, no traceback)
```

The first user to send `/start` wins. We *do not* warn that this is the
admin-binding moment — the operator is the only person who knows the
bot exists at install time, so the binding race is a paper risk.

### 4.2 Unbinding

Two paths:

- **From the bot:** `/unbind` → confirm-keyboard → clears the column.
- **From the dashboard:** `POST /admin-bot/unbind` (button on the
  Telegram settings card). This is the recovery path for "I lost
  access to the original Telegram account".

After unbind, the next `/start` from anyone re-binds.

### 4.3 Authorization on every message

Every incoming `Message` and `CallbackQuery` is gated by:

```python
if settings.admin_telegram_user_id is None:
    # only /start is allowed pre-binding
    ...
elif update.from_user.id != settings.admin_telegram_user_id:
    # silently log + drop. Reply only to /start (so the rejected
    # user knows why), drop everything else without acknowledgement.
    ...
```

## 5. Command surface

Full operator-console scope. ~20 commands; full table in §5.1.

### 5.1 Command list

| Command | Replies with | Implementation note |
| --- | --- | --- |
| `/start` | Bind-confirm or "bound to another admin" | Auto-bind moment |
| `/help` | One-screen command list, grouped | Static text |
| `/status` | Pipeline / kill-switch / broker / Telegram pulse / subscribed channels / balance | Reuses `pipeline.status` + `broker.status` |
| `/balance` | Demo + Real balances | `quotex_manager.balance` |
| `/mode demo\|real` | Switches `account_mode` | Confirm before flipping to **real** |
| `/pipeline on\|off` | Flips `pipeline_active` | |
| `/killswitch on\|off` | Flips `kill_switch_engaged` | |
| `/panic` | `killswitch on` + `pipeline off` in one shot | The "everything stop now" red button |
| `/channels` | Inline list of watched channels with `[⏸/▶] [ℹ]` buttons per row | Tap = toggle `enabled` via callback |
| `/channel <id>` | Title, type, parser count, recent-decision summary | |
| `/parsers [chat_id]` | Inline list of parsers, optional chat filter, with `[⏸/▶] [ℹ]` buttons | |
| `/parser <id>` | Name, type, stake, duration, martingale config, current streak | |
| `/trades [N]` | Last N trades (default 10) | Backed by `pipeline.trades` |
| `/decisions [N]` | Last N parser decisions | Backed by `pipeline.recent_decisions` |
| `/streaks` | Per-parser martingale streak + last stake | Backed by `risk.streaks` |
| `/caps` | Daily-loss / daily-stake / max-concurrent | |
| `/caps loss <amount>` / `/caps stake <amount>` / `/caps concurrent <N>` | Sets the cap; `0` = uncapped | |
| `/stake <amount>` | Sets `default_stake` on `GlobalSettings` | |
| `/notify <class> on\|off` | Mute/unmute one of placed/settled/risk_rejected/system_error | Writes one of the 4 boolean settings columns |
| `/unbind` | Releases the admin binding (must confirm) | |
| `/whoami` | Echoes your user_id | Diagnostic |

### 5.2 UX conventions

- All write commands reply with the *new* state after the change.
- Destructive commands (`/panic`, `/mode real`, `/killswitch off`,
  `/unbind`) require a `[Yes, do it] [Cancel]` inline confirm.
- Long replies (`/trades 50`, `/decisions 50`) are paginated — Telegram
  caps a message at 4096 chars; the bot splits at row boundaries.
- All replies use MarkdownV2; helper escapes user-supplied strings
  (asset names, parser names) at format-time.

### 5.3 Concurrency

All command handlers run inside a single `asyncio.Lock` on the bot
service — sequential processing. Writes are infrequent (operator
typing); serialising eliminates a class of race conditions (two
`/killswitch on` taps, two `/parser pause 5` taps). The notifier path
does **not** take this lock — sending DMs is independent of command
processing.

## 6. Pause semantics

**Pause == toggle the existing `enabled` flag.** No new column, no
scheduler, no auto-resume.

Rationale: the existing `WatchedChannel.enabled` and `ParserConfig.enabled`
already drive the pipeline's filter logic. Adding a `paused_until`
timestamp would mean: a new column on two tables, a periodic check on
dispatch, a way to cancel an in-flight pause, and dashboard UI to
expose all of the above. That's a feature in its own right; ship it
later if operators ask for it.

The bot's `/channel pause <id>` and `/parser pause <id>` are pure
remote controls over the existing flag. `/resume` is the inverse.

## 7. Notification system

### 7.1 Event classes

Each event class corresponds to one notify-class boolean on `GlobalSettings`:

| Class | Source | Default |
| --- | --- | --- |
| `trade.placed` | `executor.placed` log point — every order submitted | on |
| `trade.settled` | settlement watcher — win / loss / refund | on |
| `risk.rejected` | risk gate rejecting a parsed signal | on |
| `system.error` | broker disconnect, telegram pulse stale, login fail | on |

### 7.2 Format examples

**`trade.placed`:**

```text
🟢 PLACED  EURUSD_otc · CALL · 60s
parser: DreamVIP (regex)
stake : $20.00 (step 1, ×2 from base)
mode  : scheduled @ 14:32:00 UTC
```

**`trade.settled`:**

```text
✅ WIN   EURUSD_otc · CALL · 60s   +$18.40
parser: DreamVIP   |   day P&L: +$42.10
```

(Loss uses ❌ ; refund uses ⚪ ; line two always carries the running
day P&L so the operator has a constant scoreboard.)

**`risk.rejected`:**

```text
🚫 REJECTED  EURUSD_otc · CALL  (DreamVIP)
reason: daily-loss cap hit ($-100.00 / $100.00)
```

**`system.error`:**

```text
⚠️ SYSTEM  Quotex disconnect
error: WebSocket closed (1006)
auto-reconnect: in progress (attempt 3/∞)
```

### 7.3 Rate limiting

Per-class **token bucket** (capacity 5, refill 1/30s). When the bucket
empties for a class, subsequent events for that class are coalesced
into a single digest message every 60s:

```text
ℹ️ 14 trade.placed events suppressed in last 60s
   (rate limit hit — see dashboard for details)
```

Rationale: `system.error` from a flapping broker connection or
`risk.rejected` after a daily-cap breach can each generate hundreds of
events per minute. Without coalescing, the admin chat becomes
unreadable. The bucket lives in-memory on the notifier — restarted on
each app start, not persisted.

### 7.4 Producer changes

Two new event types must be published on the existing `TradeEventBus`:

- `risk.rejected` — published from the risk gate when it rejects a
  parsed signal. Payload: `{chat_id, parser_config_id, parser_name,
  asset, direction, reason, cap_value, cap_used}`.
- `system.error` — published from the broker manager and telegram
  manager on relevant degradation events. Payload: `{component, kind,
  detail, recoverable}`.

The dashboard does not consume these today; the bot is the first
consumer. Adding them to the bus is a one-line `event_bus.publish` at
each existing log point — no behavioural change.

## 8. Error handling & degradation

Three failure surfaces, each with an explicit response:

### 8.1 Bot client fails to start

(Bad token, network down, `Unauthorized`.)

The bot stops itself, logs `admin_bot.start_failed`, sets
`state="error"` exposed via `GET /admin-bot/status`. The rest of the app
keeps trading — the bot is *additive*, never load-bearing. The
dashboard shows an "Admin bot offline" badge.

### 8.2 Send to admin fails

(Admin blocked the bot, deactivated account, network glitch.)

The notifier catches `Forbidden` / `RPCError` per-send, increments a
consecutive-failure counter on the admin user_id, and after **5
consecutive failures** pauses outbound notifications until the admin
sends *any* message back to the bot (which proves the channel is
healthy again). All `system.error` events still go to the structured
log — only the DM forwarding pauses.

### 8.3 Command handler raises

Caught at the Pyrogram handler boundary. Replies:

```text
❌ command failed: <ExceptionClassName>
```

…and logs the full traceback at ERROR level. We do **not** echo internal
exception messages to the chat — those can leak file paths or DB
internals. The bound user is supposed to be the operator, but defence
in depth is free here.

## 9. Testing strategy

Pyrogram is hard to integration-test against the real network, so we
layer the testing:

### 9.1 Unit tests (`tests/test_admin_bot.py`)

Monkey-patch `pyrogram.Client` with a fake that records `send_message`
calls and replays canned `Message` / `CallbackQuery` updates. Coverage:

- First `/start` binds and persists the user_id.
- Second `/start` from a different user_id is rejected with the
  "bound to another admin" message; the bound user_id does not change.
- Each command translates to the expected service-layer call (mocked)
  and replies with the expected text.
- Rate-limit bucket coalesces a flood of identical-class events into a
  digest after the bucket empties.
- `Forbidden` on `send_message` flips the notifier into "paused
  outbound" state after 5 consecutive failures.
- An incoming message from a non-admin user is silently dropped (no
  reply, no service call).
- Destructive commands (`/panic`, `/mode real`, `/unbind`) require a
  callback-confirm before they take effect.

### 9.2 Integration tests

Existing FastAPI test client + a `FakeAdminBot` that captures sent
messages. Assertions:

- Flipping the kill switch via `POST /risk/kill-switch` causes the
  same effect as a `/killswitch on` command from the bot.
- Toggling `WatchedChannel.enabled` via `/channel pause` produces the
  same DB state as `PUT /telegram/watched/<id>`.
- `risk.rejected` published on the event bus produces a notification
  with the expected format.

This pins the two surfaces (REST + bot) as behaviourally equivalent —
a refactor that breaks one without the other will fail the test.

### 9.3 Manual smoke test

Documented in `docs/admin-bot.md` (one-page checklist):

1. Create a bot via @BotFather, copy the token.
2. Set `TELEGRAM_BOT_TOKEN=<token>` in `.env`, restart the container.
3. Open the bot in Telegram, send `/start`, expect bind confirm.
4. Walk through each command group: status, channels, parsers, trades,
   risk, mode.
5. Place a trade on demo, expect a `trade.placed` then `trade.settled`
   notification.
6. Trigger a risk rejection (set `daily_max_loss=1`, lose a trade),
   expect a `risk.rejected` notification.

We deliberately do **not** test against the real Telegram network in
CI — flaky, requires a bot token in CI secrets, and the Pyrogram
`Client` boundary is narrow enough to fake completely.

## 10. Out of scope (for this spec)

These have been considered and intentionally deferred:

- **Multi-admin support.** One bot, one bound admin. If the operator
  has co-traders, they can each run their own bot pointed at the same
  backend — or this gets a follow-up spec.
- **Timed pauses (`/pause channel <id> 30m`).** The user explicitly
  picked the simpler "toggle enabled" semantic. Re-add later if the
  workflow demands it.
- **Inline parser editing from the bot.** The dashboard remains the
  source of truth for parser authoring. The bot can pause/resume but
  not edit configs.
- **Admin-controlled webhook to a third-party (Discord, Slack, email).**
  The event bus is already the right seam if someone wants this; not
  in this scope.
- **Encryption of notification payloads.** Telegram bot DMs are
  end-to-end encrypted between client and Telegram; treating them as
  the trust boundary is consistent with how the dashboard treats its
  own login session.
