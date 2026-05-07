# Autotrader

Telegram-driven autotrader UI built on top of [pyquotex](../).

> **Phase 0 — scaffold only.** This is the foundation: FastAPI backend,
> Next.js frontend, single-user passcode auth, Docker compose. No
> trading logic yet — those land in Phases 1–7. See the project plan
> in the parent README or the issue tracker.

## Stack

- **Backend** — Python 3.13, FastAPI + uvloop + httptools, SQLModel +
  aiosqlite, Pyrogram + TgCrypto (Phase 2), msgspec, structlog,
  cryptography (Fernet), argon2-cffi
- **Frontend** — Next.js 15 (App Router, React 19), Tailwind v4,
  shadcn/ui, TanStack Query v5, Zustand
- **Tooling** — `uv` for Python, `bun` for JS, Docker compose

## Quick start (Docker)

```bash
cd autotrader
cp .env.example .env

# Generate a Fernet key (stdlib only, no extra installs) and paste it
# into AUTOTRADER_FERNET_KEY:
python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

# Set AUTOTRADER_PASSCODE to a long random string. A safe one-liner:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose up --build
```

- Dashboard: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>

## Local development

### Backend

```bash
cd autotrader/backend
uv sync                       # creates .venv and installs deps
uv run uvicorn autotrader.main:app --reload --loop uvloop --http httptools
uv run pytest                 # run tests
```

### Frontend

```bash
cd autotrader/frontend
bun install
bun run dev                   # http://localhost:3000
bun run type-check
```

## Layout

```
autotrader/
├── backend/                   # FastAPI app (autotrader package)
│   └── src/autotrader/
│       ├── main.py            # FastAPI entry, lifespan, middleware
│       ├── config.py          # Pydantic Settings
│       ├── db.py              # async SQLModel engine
│       ├── crypto.py          # Fernet helpers
│       ├── auth.py            # passcode + bearer-token auth
│       ├── logging_setup.py   # structlog
│       ├── models/            # SQLModel tables
│       ├── routers/           # FastAPI routers (health, auth, …)
│       └── services/          # parsers, pipeline, quotex, telegram (later phases)
├── frontend/                  # Next.js 15 app
│   ├── app/                   # App Router pages
│   ├── components/ui/         # shadcn primitives
│   └── lib/                   # api client, utils
├── docker-compose.yml
└── .env.example
```

## Roadmap

| Phase | Scope |
|---|---|
| 0 ✅ | Scaffold (this commit) |
| 1   | Quotex client manager + broker login UI |
| 2   | Pyrogram login flow + channel browser |
| 3   | Parser engine (templates + regex + multi-message aggregator) |
| 4   | Execution pipeline (live + scheduled trades) |
| 5   | Risk module (limits, position sizing, kill switch) |
| 6   | Live dashboard (WebSocket trade feed, stats) |
| 7   | Hardening (logging, backups, docs) |
