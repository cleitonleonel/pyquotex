# Autotrader backend

The FastAPI service that drives the autotrader: tails Telegram, parses
signals, gates them through risk, and places trades on Quotex via the
sibling [`pyquotex`](../../) library.

This README is the **developer entry point**. For how the pieces fit
together and the invariants that keep it safe, read
[`../ARCHITECTURE.md`](../ARCHITECTURE.md). For operations and
failure-mode triage, read [`../docs/RUNBOOK.md`](../docs/RUNBOOK.md).

## Prerequisites

- Python **3.13+**
- [`uv`](https://docs.astral.sh/uv/) (manages the venv and deps)
- The sibling `pyquotex` checkout at `../../` (a path dependency in
  `pyproject.toml` — no separate install)

## Setup & run

```bash
cd autotrader/backend
uv sync                                # creates .venv, installs deps

# Required env (see ../.env.example for the full list):
export AUTOTRADER_PASSCODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AUTOTRADER_FERNET_KEY="$(python3 -c 'import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
export TELEGRAM_API_ID=...             # from https://my.telegram.org/apps
export TELEGRAM_API_HASH=...

uv run uvicorn autotrader.main:app --reload --loop uvloop --http httptools
```

- API: <http://localhost:8000>
- OpenAPI docs (authoritative request/response contract):
  <http://localhost:8000/docs>

> **Keep `AUTOTRADER_FERNET_KEY` safe.** It encrypts every stored broker
> credential and the Telegram session at rest. Lose it and every saved
> secret is unrecoverable.

The full env-var reference (safety gate, tuning, ops) is the
**Configuration** section of [`../README.md`](../README.md).

## Tests & checks

```bash
uv run pytest                          # 492 tests, pytest-asyncio
uv run pytest tests/test_pipeline.py   # one module
uv run ruff check src tests            # lint
uv run mypy src                        # type-check
```

Tests run against a temp SQLite DB and a `FakeQuotex` — no broker or
Telegram credentials are needed, and no real orders are placed.

## Code map

Source lives in `src/autotrader/`. One-liners here; the full subsystem
map, data flow, and risk logic are in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

| Path | What it is |
|------|------------|
| `main.py` | App factory + `lifespan()` — wires and drains every subsystem. |
| `config.py` | Env-driven Pydantic settings (won't start if required vars are missing). |
| `db.py` · `crypto.py` · `auth.py` | SQLModel engine, Fernet at-rest encryption, Argon2id passcode auth. |
| `models/` | SQLModel tables: `trade_attempt`, `parser_config`, `martingale_state`, encrypted credential/session rows. |
| `routers/` | REST + WebSocket surface (auth, broker, telegram, parsers, pipeline, risk, stats, feed, admin_bot, health). |
| `services/quotex_manager.py` | **The only module that imports `pyquotex`.** Broker lifecycle + OTP relay. |
| `services/telegram_manager.py` | The Pyrogram userbot that tails your dialogs. |
| `services/pipeline.py` | Message → parser cache → dispatch (per-chat lock). |
| `services/risk_gate.py` | Caps, martingale/Paroli sizing, the REAL double-gate — the last check before money moves. |
| `services/executor.py` | **The only module that places broker orders.** Non-blocking; spawns a settlement watcher. |
| `services/parsers/` | Template / regex / prep-trigger / batch parsers — see [`../docs/PARSERS.md`](../docs/PARSERS.md). |
| `services/event_bus.py` | Lossy in-process fan-out behind the dashboard WebSocket. |
| `services/admin_bot*.py` | Optional Telegram bot for remote control — see [`../docs/admin-bot.md`](../docs/admin-bot.md). |

## Gotchas

- **Two Telegram identities.** `telegram_manager` is your *user* account
  (signal ingestion); `admin_bot` is a separate *bot* (remote control,
  optional, off without `TELEGRAM_BOT_TOKEN`).
- **DEMO by default.** Real-money trading needs both
  `AUTOTRADER_LIVE_TRADING_ENABLED=true` *and* the UI account toggle.
- **Pyrogram is `pyrofork`.** A maintained fork is pinned on purpose
  (vanilla Pyrogram has an `UpdateChannelTooLong` bug). Don't swap it.
- **First broker connect may need an OTP.** Quotex emails a PIN; submit
  it via the dashboard or the admin bot. The session is then persisted
  so restarts skip it.
