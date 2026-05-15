# Autotrader frontend

The dashboard: a Next.js 15 single-page app that signs in with a
passcode, then drives and observes the [backend](../backend/README.md) —
broker/Telegram setup, parser editing, pipeline controls, and live
trade analytics.

For how the frontend fits the whole system (auth model, the live feed,
URL-driven filters), see [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §9.

## Stack

| Concern | Choice |
|---------|--------|
| Framework | Next.js 15 (App Router) + React 19 |
| Package manager | **`bun`** (there is a `bun.lock` — not npm/pnpm) |
| Styling | Tailwind CSS v4 + shadcn/ui (new-york), dark mode via `next-themes` |
| Data | TanStack Query v5 (30s stale), Zustand for local state |
| Charts | Recharts |

## Prerequisites

- [`bun`](https://bun.sh/) (the only toolchain you need)
- A running backend (see [backend/README.md](../backend/README.md)), or
  set `NEXT_PUBLIC_API_BASE` to point at one

## Commands

```bash
cd autotrader/frontend
bun install

bun run dev            # dev server, http://localhost:3000 (Turbo)
bun run build          # production bundle (output: standalone)
bun run start          # serve the production build
bun run type-check     # tsc --noEmit
bun run lint           # next lint
bun run test:e2e       # Playwright smoke tests
bun run test:e2e:ui    # Playwright, interactive
```

## Environment

| Var | Default | Notes |
|-----|---------|-------|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | Backend origin. **Baked into the bundle at build time** — rebuild to change it. |

The WebSocket URL is derived from `NEXT_PUBLIC_API_BASE`
(`http`→`ws`, `https`→`wss`), so a single var configures both REST and
the live feed.

## Structure

```
frontend/
├── app/
│   ├── login/                  # the only public route (passcode)
│   ├── dashboard/              # auth-gated; layout enforces the gate
│   │   ├── page.tsx            # overview (KPIs, equity, recent activity)
│   │   ├── analytics/          # performance / parsers / execution / risk tabs
│   │   ├── trades/             # live trade table (WebSocket-fed)
│   │   ├── decisions/          # parser dispatch decision feed
│   │   ├── pipeline/           # master + kill switch, daily caps
│   │   ├── telegram/           # Telegram login + watched channels
│   │   ├── broker/             # broker credentials + OTP + account mode
│   │   └── parsers/            # per-chat / per-config parser editor + tester
│   └── _components/ (or dashboard/_components)  # dashboard panels & charts
├── components/ui/              # shadcn primitives
├── lib/
│   ├── api.ts                  # fetch wrapper, bearer auth, API namespaces
│   ├── api-stats-v2.ts         # typed analytics client (mirrors backend models)
│   ├── use-trade-feed.ts       # /feed/ws hook → merges into Query cache
│   └── use-filters.ts          # filter state, single-sourced from the URL
├── e2e/                        # Playwright smoke specs
└── next.config.ts              # output: standalone, strict mode
```

## How it talks to the backend

- **Auth:** `POST /auth/login` with the passcode → bearer token stored
  in `localStorage["autotrader.token"]`, attached to every request. No
  cookies.
- **Reads:** REST through `lib/api.ts`; analytics through the typed
  `lib/api-stats-v2.ts`. All analytics respect the global filter bar.
- **Live:** `use-trade-feed` opens `GET /feed/ws?token=...` (the token
  rides the querystring — browsers can't set WebSocket headers), merges
  `trade.upserted` / decision frames into the TanStack Query cache, and
  auto-reconnects with exponential backoff.
- **Shareable views:** filters live in the URL, so copying the address
  bar reproduces the exact analytics slice.

## E2E tests

Playwright specs in `e2e/` (smoke level: overview loads clean, analytics
renders, a filter pill opens, sign-out works). `playwright.config.ts`
auto-starts `bun run dev` if nothing is on port 3000. Run with
`bun run test:e2e`.
