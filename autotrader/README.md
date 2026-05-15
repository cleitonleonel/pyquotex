# Autotrader

Self-hosted Telegram-driven autotrader UI on top of [pyquotex](../).
Connects to your Telegram account, parses signals from the channels
you pick, and dispatches them to Quotex through a multi-stage pipeline
with risk gating and a live dashboard.

> **Status:** all seven build phases shipped. Ready to run.

## Highlights

- **Telegram in, Quotex out.** A single warm Pyrogram client tails
  your dialogs; matched signals route through parser → risk gate →
  broker on a per-chat lock.
- **Four parser styles.** Click-to-pick templates, full Python regex,
  prep + sticker (two-message channels), and one-message-many-signals
  batch.
- **Risk module.** Daily P&L cap, daily stake cap, concurrency cap,
  martingale ladder per parser (with explicit reset-on-win), and a
  hard kill switch.
- **Live dashboard.** WebSocket trade feed, per-channel win-rate
  table, and signal→place / place→settle latency p50/p99 tiles.
- **Single-user, self-hosted.** Argon2id passcode + Fernet-encrypted
  bearer tokens. Credentials and Telegram session strings are Fernet-
  encrypted at rest.
- **DEMO by default.** REAL trading is gated behind the
  ``AUTOTRADER_LIVE_TRADING_ENABLED`` env flag *and* the master
  switch — both must be on.
- **Production-ready ops.** Online SQLite backups with retention,
  optional Sentry error reporting, multi-stage Docker images,
  resource limits in the prod compose overlay.

## Stack

| Layer    | Pieces |
|----------|--------|
| Backend  | Python 3.13, FastAPI + uvloop + httptools, SQLModel + aiosqlite, Pyrogram + TgCrypto, structlog, cryptography (Fernet), argon2-cffi, Sentry SDK (optional) |
| Frontend | Next.js 15 (App Router, React 19), Tailwind v4, shadcn/ui, TanStack Query v5, Zustand |
| Tooling  | `uv` for Python, `bun` for JS, Docker compose |

## Quick start (Docker)

```bash
cd autotrader
cp .env.example .env

# Generate AUTOTRADER_FERNET_KEY (stdlib only, no extra installs):
python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

# Generate AUTOTRADER_PASSCODE (long random string):
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Get TELEGRAM_API_ID + TELEGRAM_API_HASH from https://my.telegram.org/apps
# and paste them into .env.

docker compose up --build
```

- Dashboard: <http://localhost:3000>
- API docs:  <http://localhost:8000/docs>

First-time setup inside the dashboard:

1. Sign in with your passcode.
2. **Telegram** tab → enter your phone, type the SMS code, then 2FA
   password if your account has one.
3. **Broker** tab → enter your Quotex email + password. Approve the
   email OTP if Quotex challenges. Stays in DEMO until you flip the
   account toggle *and* set ``AUTOTRADER_LIVE_TRADING_ENABLED=true``.
4. **Parsers** tab → pick one of your dialogs, build a parser config,
   test against recent messages, save.
5. **Pipeline** tab → set daily caps, then flip the master switch.

## Production deploy

The base ``docker-compose.yml`` is fine for local dev. For production
add the overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

What the overlay adds:

| Concern         | Override |
|-----------------|----------|
| Restart policy  | ``always`` (the base uses ``unless-stopped``) |
| Log retention   | json-file driver, 10 MB × 5 files per service |
| Memory ceilings | api: 512 MB, web: 256 MB (warns before OOM-kill) |
| Backups         | Hourly online SQLite copy to ``/data/backups``, retain 24 |
| Sentry          | DSN piped through if ``AUTOTRADER_SENTRY_DSN`` is set |

**TLS:** put the dashboard behind Caddy / Nginx / Traefik and let
Let's Encrypt own the cert. The WebSocket feed needs a ``wss://``
proxy rewrite in production — see the [deployment runbook](docs/DEPLOY.md)
for sample configs.

## Local development

### Backend

```bash
cd autotrader/backend
uv sync                                # creates .venv and installs deps
uv run uvicorn autotrader.main:app --reload --loop uvloop --http httptools
uv run pytest                          # 492 tests (pytest-asyncio)
uv run ruff check src tests            # lint
```

### Frontend

```bash
cd autotrader/frontend
bun install
bun run dev                            # http://localhost:3000
bun run type-check
bun run build                          # production bundle
```

## Configuration

All knobs are env vars (or `.env`). The app refuses to start when
required ones are missing.

### Required

| Var | Notes |
|-----|-------|
| `AUTOTRADER_PASSCODE`   | Single-user dashboard login. Long random string. |
| `AUTOTRADER_FERNET_KEY` | 32-byte url-safe base64. Encrypts every credential and session string at rest. **Losing this key invalidates every saved credential.** Back it up. |
| `TELEGRAM_API_ID`       | From <https://my.telegram.org/apps>. |
| `TELEGRAM_API_HASH`     | From <https://my.telegram.org/apps>. |

### Safety

| Var | Default | Notes |
|-----|---------|-------|
| `AUTOTRADER_LIVE_TRADING_ENABLED` | `false` | Hard env-gate. Even when `true`, the master switch and per-channel `enabled` flag still apply. |

### Tuning

| Var | Default | Notes |
|-----|---------|-------|
| `AUTOTRADER_DB_URL`     | `sqlite+aiosqlite:///./data/autotrader.db` | aiosqlite URL. |
| `AUTOTRADER_LOG_LEVEL`  | `INFO`  | structlog level. |
| `AUTOTRADER_API_HOST`   | `0.0.0.0` | |
| `AUTOTRADER_API_PORT`   | `8000`  | |
| `AUTOTRADER_WEB_PORT`   | `3000`  | docker-compose host-side mapping. |
| `AUTOTRADER_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated, or `*`. |

### Ops (Phase 7)

| Var | Default | Notes |
|-----|---------|-------|
| `AUTOTRADER_BACKUP_INTERVAL_SECONDS` | `0` (off) | Online SQLite backup cadence. Production: `3600`. |
| `AUTOTRADER_BACKUP_RETAIN`           | `24`      | Keep the last N timestamped files. |
| `AUTOTRADER_BACKUP_DIR`              | (next to db) | Override the destination. |
| `AUTOTRADER_SENTRY_DSN`              | (empty)   | Set to enable Sentry. SDK ships in the image; no rebuild needed. |
| `AUTOTRADER_SENTRY_ENVIRONMENT`      | `production` | Sentry tag. |
| `AUTOTRADER_SENTRY_TRACES_SAMPLE_RATE` | `0.0`   | Performance tracing — keep at 0 unless you need it. |

### Frontend build

| Var | Default | Notes |
|-----|---------|-------|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | Baked into the bundle at build time. |

## How it works (high level)

```
Telegram (Pyrogram MTProto)
   │
   ▼
Pipeline.dispatch (per-chat lock)
   │
   ▼
ParserCache  ── parser_type → Template / Regex / PrepTrigger / Batch
   │
   ▼  ParsedSignal
RiskGate.evaluate
   ├── master switch / kill switch / parser enabled?
   ├── REAL gate (env flag)
   ├── trade-mode pin (live / scheduled / auto)
   ├── stake = base × multiplier^streak (martingale)
   └── daily caps (max_loss / max_stake / max_concurrent)
   │
   ▼  RiskDecision (allow + sized stake, or block + reason)
TradeExecutor
   ├── insert TradeAttempt (audit row)
   ├── publish trade.upserted on EventBus  ──► WebSocket /feed/ws ──► dashboard
   ├── pyquotex.buy() / open_pending()
   └── _watch_result task
        ├── wait_for_order_close
        ├── update TradeAttempt (won / lost / profit)
        └── martingale_state.record_outcome
```

A couple of subtleties worth knowing:

- **Reconciler.** On every restart, pending trade rows are bucketed.
  Rows whose broker order never landed (`placed_at=None`) expire
  immediately. Rows past their settle window
  (`placed_at + duration + 60s`) expire immediately with a "settle
  window passed; check broker history" note. Rows still inside their
  window stay `pending` and a deferred task expires them when the
  window closes. The martingale ladder is never ticked from this path
  — we don't know the outcome, so we don't guess.
- **Asset resolution.** Trailing `OTC` token is detected first
  (`USD NGN OTC` → `USDNGN_otc`), then exact match against the
  broker's catalogue, then `_otc` cross-probe, then fallback. Manual
  aliases override everything.
- **Schedule semantics.** `trade_mode=auto` infers from the parsed
  signal: if a fire time was extracted, use `open_pending`; otherwise
  `buy` immediately. `live` strips the schedule, `scheduled` requires
  one.

**Writing parsers:** see [`docs/PARSERS.md`](docs/PARSERS.md) for the
template / regex / prep+trigger / batch reference, the direction-token
table, and the "why isn't my parser firing?" troubleshooting checklist.

## Documentation

| Doc | What it covers |
|-----|----------------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | How it all fits: subsystem map, signal data flow, risk logic, the invariants that keep it safe. **Start here to understand the system.** |
| [`backend/README.md`](backend/README.md) | Backend dev setup, run/test commands, code map. |
| [`frontend/README.md`](frontend/README.md) | Frontend dev setup, structure, backend integration. |
| [`docs/PARSERS.md`](docs/PARSERS.md) | Parser authoring reference (template / regex / prep+trigger / batch). |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Production deployment: TLS/`wss` proxy configs, SQLite backup + restore. |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Failure-mode triage: OTP timeouts, broker reconnect, cache tuning, Sentry. |
| [`docs/admin-bot.md`](docs/admin-bot.md) | The optional Telegram remote-control bot: setup, commands, recovery. |

The exact HTTP contract is generated from the code — run the backend and
open <http://localhost:8000/docs>.

## Project layout

```
autotrader/
├── backend/                       # FastAPI app (autotrader package)
│   ├── src/autotrader/
│   │   ├── main.py                # lifespan + middleware + router wiring
│   │   ├── config.py              # Pydantic Settings (env-driven)
│   │   ├── auth.py                # Argon2id passcode + Fernet bearer tokens
│   │   ├── crypto.py              # Fernet helpers
│   │   ├── db.py                  # async SQLModel engine + ALTER migrations
│   │   ├── models/                # SQLModel tables (parser_config, settings, trade_attempt, …)
│   │   ├── routers/               # FastAPI routers (auth, broker, telegram, parsers, pipeline, risk, stats, feed)
│   │   └── services/              # quotex_manager, telegram_manager, parsers/, pipeline, risk_gate, executor, event_bus, backups
│   ├── tests/                     # 492 tests — pytest-asyncio
│   ├── pyproject.toml             # uv-managed; depends on sibling pyquotex via path
│   ├── Dockerfile                 # multi-stage, non-root, healthcheck
│   └── README.md                  # backend dev guide
├── frontend/                      # Next.js 15 app
│   ├── app/dashboard/             # overview, analytics, trades, decisions, pipeline, telegram, broker, parsers
│   ├── components/ui/             # shadcn primitives
│   ├── lib/                       # api client, use-trade-feed hook
│   ├── Dockerfile                 # Next standalone output, non-root Node
│   └── README.md                  # frontend dev guide
├── docs/
│   ├── PARSERS.md                 # parser authoring reference
│   ├── DEPLOY.md                  # production deployment runbook
│   ├── RUNBOOK.md                 # failure-mode triage
│   └── admin-bot.md               # remote-control Telegram bot
├── ARCHITECTURE.md                # system architecture + invariants
├── docker-compose.yml             # base — local dev / single-host prod
├── docker-compose.prod.yml        # production overlay (logs, memory, backups)
└── .env.example
```

## Roadmap

| Phase | Scope |
|---|---|
| 0 ✅ | Scaffold (FastAPI + Next.js + auth) |
| 1 ✅ | Quotex client manager + broker login UI |
| 2 ✅ | Pyrogram login flow + channel browser |
| 3 ✅ | Parser engine — templates + regex + prep+trigger + batch |
| 4 ✅ | Execution pipeline (live + scheduled trades) |
| 5 ✅ | Risk module (caps, martingale, kill switch) |
| 6 ✅ | Live dashboard (WebSocket feed, channel stats, latency p50/p99) |
| 7 ✅ | Hardening (online backups, Sentry, prod docker, full docs) |
