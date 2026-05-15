# Autotrader Architecture

This document explains how the autotrader is built: the moving parts, the
path a Telegram signal takes to become a broker trade, and the invariants
that keep it safe. It is the map; the [README](README.md) is the quick
start, and the [RUNBOOK](docs/RUNBOOK.md) is what you read at 2 a.m.

For the exact HTTP contract, run the backend and open
<http://localhost:8000/docs> — the OpenAPI schema is generated from the
code and never drifts. This doc deliberately describes *structure and
invariants*, not endpoint signatures, so it stays correct as the API
evolves.

---

## 1. The big picture

The autotrader is two processes plus a library:

```
                        ┌───────────────────────────────┐
   Telegram (MTProto)   │  pyquotex  (../)               │
   your account ───┐    │  Quotex WebSocket client +     │
                   │    │  REST + auto-reconnect          │
                   │    └───────────────┬───────────────┘
                   │                    │ (only quotex_manager.py
                   ▼                    ▼  ever imports this)
   ┌───────────────────────────────────────────────────┐
   │  backend/   FastAPI + uvloop  (Python 3.13+)        │
   │                                                     │
   │  Telegram userbot → Pipeline → RiskGate → Executor  │
   │                          │                  │       │
   │                       SQLite (aiosqlite, Fernet)    │
   │                          │                  │       │
   │                       EventBus ──► /feed/ws (WS)     │
   │  Admin bot (optional Telegram bot, remote control)  │
   └────────────────┬─────────────────────────┬─────────┘
            REST (Bearer)              WebSocket (token on querystring)
                    │                          │
   ┌────────────────▼──────────────────────────▼─────────┐
   │  frontend/  Next.js 15 (App Router, React 19)        │
   │  login → dashboard (overview, analytics, trades,     │
   │  decisions, pipeline, telegram, broker, parsers)     │
   └──────────────────────────────────────────────────────┘
```

| Layer | Tech | Lives in |
|-------|------|----------|
| Library | `pyquotex` — Quotex WS/REST client, auto-reconnect, OTP | `../pyquotex/` |
| Backend | FastAPI, uvloop, SQLModel + aiosqlite, Pyrogram (pyrofork), structlog, cryptography (Fernet), argon2-cffi | `backend/src/autotrader/` |
| Frontend | Next.js 15, React 19, Tailwind v4, shadcn/ui, TanStack Query v5, Recharts | `frontend/` |
| Tooling | `uv` (Python), `bun` (JS), Docker Compose | repo root |

**The single most important boundary:** the entire app talks to the
broker through exactly one module — `services/quotex_manager.py`. Nothing
else imports `pyquotex`. If broker behaviour changes, there is one place
to look. Treat this as an invariant, not a coincidence.

---

## 2. Backend subsystem map

All paths are under `backend/src/autotrader/`.

### Infrastructure

| Module | Responsibility |
|--------|----------------|
| `main.py` | FastAPI app factory + `lifespan()` — wires every subsystem on startup, drains them on shutdown. |
| `config.py` | Pydantic `Settings` (env-driven, prefix `AUTOTRADER_`) and `TelegramSettings` (prefix `TELEGRAM_`). The app refuses to start if required vars are missing. |
| `db.py` | Async SQLModel engine, session factory, lightweight `ALTER` migrations. |
| `auth.py` | Single-user auth: Argon2id passcode hashed at boot, Fernet-encrypted bearer tokens with a TTL. |
| `crypto.py` | Fernet symmetric encryption helpers used for at-rest secrets. |
| `logging_setup.py` | structlog configuration. |
| `dependencies.py` | FastAPI dependency injectors (manager, pipeline, db session, current user). |

### Services (the business logic)

| Module | Responsibility |
|--------|----------------|
| `services/quotex_manager.py` | The **only** code that talks to `pyquotex`. Owns one long-lived `Quotex` client, the connection lifecycle, and the OTP relay. |
| `services/telegram_manager.py` | One long-lived Pyrogram userbot client. Tails your dialogs and hands raw messages to the pipeline. |
| `services/session_store.py` | Fernet-encrypted persistence of the broker SSID/cookies so a restart can skip the OTP challenge. |
| `services/pipeline.py` | Signal dispatch router. Looks up the parser configs for a chat, caches parser instances, and routes message → parser → signal. Holds a per-chat lock. |
| `services/risk_gate.py` | Pre-trade decision engine. Resolves stake (martingale / Paroli) and enforces every cap. The last gate before money moves. |
| `services/executor.py` | The **only** code that issues broker `buy()` / `open_pending()` calls. Non-blocking: places the order, spawns a watcher task, returns. |
| `services/event_bus.py` | In-process, lossy fan-out for live dashboard events. SQLite is the durable record; the bus is the fast path. |
| `services/parsers/` | Pluggable signal extractors (see §4). |
| `services/admin_bot*.py` | Optional Telegram **bot** for remote control (see §8). |
| `services/backups.py` | Online SQLite backup scheduler (off by default). |
| `services/broker_wire_trace.py` | Debug tap on pyquotex socket frames (off by default). |

### Routers and models

`routers/` exposes the REST + WebSocket surface (auth, broker, telegram,
parsers, pipeline, risk, stats, stats_v2, feed, admin_bot, health).
`models/` holds the SQLModel tables: `trade_attempt` (every trade, the
audit trail), `parser_config` (per-chat parsing + trade shaping +
martingale config), `martingale_state` (live streak counters),
`broker_credentials` and `telegram_session` (both Fernet-encrypted),
`watched_channel`, and a singleton `settings` row (admin bind, kill
switch).

---

## 3. The signal data flow

This is the spine of the system. Follow one signal end to end:

```
Telegram message in a watched channel
   │  TelegramManager._on_message()
   ▼
Pipeline.dispatch(RawMessage)            ── per-chat asyncio.Lock
   │  load ParserConfig rows by priority
   │  reuse cached parser, or rebuild if config changed
   ▼
Parser.parse() → ParsedSignal | ParseError
   │  (Template / Regex / PrepTrigger / Batch — §4)
   ▼
RiskGate.evaluate(signal)
   ├── master switch on?  kill switch off?  parser enabled?
   ├── REAL gate: AUTOTRADER_LIVE_TRADING_ENABLED AND account = REAL
   ├── trade-mode pin: live | scheduled | auto (infer from signal)
   ├── stake = base × multiplier^loss_streak   (martingale)
   │            or Paroli bump on a win streak
   └── daily caps: max_loss · max_stake · max_concurrent
   ▼
RiskDecision(allow, sized_stake, reason)
   │  blocked → log on EventBus, emit `risk_rejected`, stop
   ▼
TradeExecutor.execute()
   ├── insert TradeAttempt(status=pending)        ← audit row first
   ├── publish trade.upserted on EventBus ─► /feed/ws ─► dashboard
   ├── quotex_manager.buy()  OR  open_pending()   ← scheduled
   └── spawn _watch_result task  ─────────────────┐  (non-blocking;
                                                   │   returns now)
   ┌───────────────────────────────────────────────┘
   ▼  async watcher (one task per pending trade, capped)
poll broker until settled or expiry
   ├── update TradeAttempt → won / lost + profit
   ├── martingale_state.record_outcome()  ← ticks the ladder
   ├── publish trade.upserted (settled) on EventBus
   └── if martingale auto-recovery + lost:
        synthesize a recovery signal → re-enter Pipeline
```

Three subtleties worth internalising:

- **The executor never blocks.** It places the order, spawns a watcher,
  and returns immediately. Many trades settle concurrently; concurrency
  is bounded by `max_concurrent_trades`. This is why the dashboard feels
  live and why a slow broker can't stall the pipeline.
- **The restart reconciler never guesses.** On boot, pending
  `TradeAttempt` rows are bucketed: rows that never reached the broker
  (`placed_at = None`) expire immediately; rows past their settle window
  (`placed_at + duration + 60s`) expire with a "check broker history"
  note; rows still inside their window stay `pending` and a deferred
  task closes them later. **The martingale ladder is never ticked from
  this path** — outcome unknown means we do not move the ladder.
- **Asset resolution is layered.** A trailing `OTC` token wins first
  (`USD NGN OTC → USDNGN_otc`), then exact match against the broker's
  live catalogue, then an `_otc` cross-probe, then fallback. Manual
  aliases override everything. See [PARSERS.md](docs/PARSERS.md).

---

## 4. The parser layer

Parsers turn a free-text Telegram message into a structured
`ParsedSignal`. Four types ship, all behind a common `Parser` ABC
(`services/parsers/base.py`):

| Type | When to use |
|------|-------------|
| `template` | Click-to-pick placeholders (`{DIRECTION} {ASSET} {DURATION}`) compiled to a regex. The friendly default. |
| `regex` | Full Python regex with named groups. Maximum control. |
| `prep_trigger` | Two-message channels: a prep message arms the trade, a trigger message fires it. |
| `batch` | One message that contains many signals → N scheduled trades. |

Supporting pieces: `aggregator.py` (buffers multi-message signals per
chat/sender and retries the inner parser), `asset_resolver.py` (the
layered resolution above), `normalize.py` (direction/duration tokens),
and `factory.py` (builds a parser from a config dict). The full
authoring reference, the direction-token table, and a "why isn't my
parser firing?" checklist live in [docs/PARSERS.md](docs/PARSERS.md).

---

## 5. Risk gate: the logic that protects the account

`risk_gate.py` is evaluated before *every* trade and is the only thing
standing between a parsed signal and real money. It checks, in order:

1. **Master switch / kill switch / per-parser enabled.** Any one off → block.
2. **REAL gate (double lock).** Real-money trades require *both*
   `AUTOTRADER_LIVE_TRADING_ENABLED=true` (env, needs a restart) *and*
   the account toggled to REAL in the UI. Default state is DEMO. This is
   intentionally two independent gates so a UI misclick cannot move real
   money and an env flag alone cannot either.
3. **Trade-mode pin.** `live` strips any schedule and fires now;
   `scheduled` requires a fire time; `auto` infers — fire time present →
   `open_pending`, else `buy`.
4. **Stake sizing.** Martingale: `stake = base × multiplier^loss_streak`,
   reset on win. Optional Paroli: bump stake while on a winning streak.
   Per-parser config; streak counters live in `martingale_state`.
5. **Daily caps.** Realised-loss cap, committed-stake cap
   (placed + pending), and concurrency cap. Any breach → block with a
   reason that surfaces on the dashboard and to the admin bot.

The output is a `RiskDecision`: allow with a resolved stake, or block
with a human-readable reason.

---

## 6. Persistence and secrets

- **Store:** SQLite via `aiosqlite`, modelled with SQLModel. Default
  `sqlite+aiosqlite:///./data/autotrader.db`. Schema changes apply as
  lightweight `ALTER`s on startup (`db.py`).
- **At-rest encryption:** broker credentials and the Telegram session
  string are Fernet-encrypted with `AUTOTRADER_FERNET_KEY`. **Losing
  that key invalidates every saved credential** — back it up out of
  band. This is the single highest-stakes operational fact in the
  system.
- **Passcode:** `AUTOTRADER_PASSCODE` is Argon2id-hashed at boot; the UI
  exchanges it for a Fernet bearer token with a TTL.
- **SSID reuse:** `session_store.py` persists the encrypted broker
  SSID/cookies so a restart resumes the broker session instead of
  re-running the email-OTP dance. The matching reconnect/SSID hardening
  lives in `pyquotex` itself.

---

## 7. Event bus and the live feed

The dashboard is driven by `event_bus.py`: a bounded, **lossy**
in-process fan-out. Trades, settlements, risk rejections, and system
errors are published as events; `routers/feed.py` exposes them over
`GET /feed/ws` (the token rides on the querystring because browsers
can't set custom headers on a WebSocket).

Lossy is a deliberate choice: if a slow client can't keep up, events are
dropped, not buffered unboundedly. **Nothing important is lost** — every
trade is a durable `TradeAttempt` row, and the frontend reconciles the
live stream against `GET /pipeline/trades`. The bus is the fast path;
SQLite is the truth.

---

## 8. The admin bot (optional)

Set `TELEGRAM_BOT_TOKEN` and a Telegram **bot** comes up alongside the
userbot for remote control. States: disabled (no token) → stopped →
running → error (a failed start is logged and never takes the app down).

First caller of `/start` becomes the bound admin. Commands cover
`/status`, `/channels`, `/parsers`, `/trades N`, `/caps`, `/kill on|off`,
`/notify <class> on|off`, and `/reconnect` (resume the broker after an
OTP timeout). It also relays broker OTP challenges to the admin's DM so
you can complete 2FA without the web UI. Notifications are token-bucket
rate-limited and floods coalesce into a digest. Full setup and command
reference: [docs/admin-bot.md](docs/admin-bot.md).

---

## 9. Frontend architecture

Next.js 15 App Router, React 19, built and run with **`bun`** (not
npm/pnpm — there is a `bun.lock`).

- **Auth model:** passcode → `POST /auth/login` → bearer token in
  `localStorage["autotrader.token"]`. No session cookies. The
  `/dashboard` layout is an auth gate; `/login` is the only public page.
- **API client:** `lib/api.ts` is a thin fetch wrapper that attaches the
  bearer token and targets `NEXT_PUBLIC_API_BASE` (default
  `http://localhost:8000`, **baked in at build time**).
  `lib/api-stats-v2.ts` mirrors the backend's analytics Pydantic models.
- **Live updates:** the `use-trade-feed` hook opens the `/feed/ws`
  WebSocket, merges `trade.upserted` / decision frames into the
  TanStack Query cache, and auto-reconnects with exponential backoff.
- **Filter state lives in the URL.** `use-filters` reads/writes the
  query string (range, channels, parsers, assets, direction, statuses),
  so a filtered analytics view is shareable by copying the link.
- **Pages:** overview, analytics (performance / parsers / execution /
  risk tabs), trades, decisions, pipeline, telegram, broker, parsers
  (with per-chat and per-config editors). Charts are Recharts; UI
  primitives are shadcn/ui on Tailwind v4 with a dark-mode toggle.

Build/run/test commands and env vars are in
[frontend/README.md](frontend/README.md).

---

## 10. Deployment topology

`docker-compose.yml` is the base (local dev or single-host prod);
`docker-compose.prod.yml` is the production overlay (restart `always`,
log rotation, memory ceilings, hourly online SQLite backups, Sentry DSN
pass-through). The dashboard belongs behind a TLS reverse proxy
(Caddy/Nginx/Traefik) and the WebSocket feed needs a `wss://` rewrite in
production. Sample proxy configs and the backup/restore procedure are in
[docs/DEPLOY.md](docs/DEPLOY.md); failure-mode triage is in
[docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## 11. Invariants — do not break these

These are the load-bearing assumptions. Changing one means changing the
safety story:

1. **Only `quotex_manager.py` imports `pyquotex`.** One broker boundary.
2. **Only `executor.py` places broker orders.** One money path.
3. **The executor never blocks** the pipeline; settlement is a separate
   watcher task.
4. **The restart reconciler never ticks martingale** — unknown outcomes
   are never guessed.
5. **REAL trading needs two independent gates** (env flag + UI toggle);
   DEMO is the default.
6. **The event bus is lossy; SQLite is durable.** Never make a
   correctness decision from the bus alone.
7. **`AUTOTRADER_FERNET_KEY` is irreplaceable.** Lose it and every
   stored credential is dead.

---

## Where to go next

| You want to… | Read |
|--------------|------|
| Run it | [README.md](README.md) |
| Work on the backend | [backend/README.md](backend/README.md) |
| Work on the frontend | [frontend/README.md](frontend/README.md) |
| Write a parser | [docs/PARSERS.md](docs/PARSERS.md) |
| Deploy to production | [docs/DEPLOY.md](docs/DEPLOY.md) |
| Diagnose a failure | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| Set up remote control | [docs/admin-bot.md](docs/admin-bot.md) |
| Know the exact API | run the backend, open `/docs` (OpenAPI) |
