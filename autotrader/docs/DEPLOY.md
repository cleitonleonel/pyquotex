# Deployment runbook

Companion to the [autotrader README](../README.md). Covers the stuff
the README only points at: TLS termination, backup recovery, common
failure modes.

## Minimum viable production host

- One VPS (1 vCPU, 1 GB RAM, 10 GB disk is enough).
- Docker + Docker Compose v2.
- A DNS name pointing at the host.
- A reverse proxy (Caddy is the lowest-friction).
- Outbound internet access to `api.telegram.org` and `qxbroker.com`.

## TLS + reverse proxy

The dashboard talks HTTP to the API and WebSocket (`/feed/ws`) to the
trade feed. In production both must ride on `https` / `wss`. Run the
proxy on the host (not in a container) so it owns ports 80 / 443.

### Caddy

```caddy
autotrader.example.com {
    encode zstd gzip

    # Frontend
    reverse_proxy http://127.0.0.1:3000

    # API (REST + WebSocket on /feed/ws)
    handle_path /api/* {
        reverse_proxy http://127.0.0.1:8000
    }
}
```

Caddy auto-handles ACME, HTTP→HTTPS, and the `Upgrade: websocket`
header. Set `NEXT_PUBLIC_API_BASE=https://autotrader.example.com/api`
in `.env` so the bundle hits the proxy instead of the raw API port.

### Nginx (alternative)

If you already run Nginx, the WebSocket bits need explicit proxying:

```nginx
location /feed/ws {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "Upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 1d;     # WS streams stay open
}
```

Without `proxy_read_timeout` Nginx kills the WS after 60s and the
dashboard flips to its 15s polling fall-back.

## Backups

Online SQLite backups are off by default. Turn them on with
`AUTOTRADER_BACKUP_INTERVAL_SECONDS=3600` (hourly is plenty). Files
land in `<db_dir>/backups/autotrader-YYYYMMDDTHHMMSSZ.db` and the
oldest get pruned past `AUTOTRADER_BACKUP_RETAIN` (default 24).

The backup file is a complete SQLite DB. To restore:

```bash
docker compose stop api
sudo cp /var/lib/docker/volumes/autotrader_autotrader-data/_data/autotrader.db \
        /tmp/autotrader.db.broken                     # belt + braces
sudo cp /var/lib/docker/volumes/autotrader_autotrader-data/_data/backups/autotrader-20260108T030000Z.db \
        /var/lib/docker/volumes/autotrader_autotrader-data/_data/autotrader.db
docker compose start api
```

The reconciler will mark every `pending` trade `expired` on the
restart — that's expected. Read the [Reconciler note](#reconciler) for
why. Manual martingale resets may be needed in the dashboard if you
want clean step-0 sequences.

For off-host snapshots, rsync the `/data/backups/` directory to your
preferred backup target on a cron. Files are inert and cheap to copy.

## Sentry / error reporting

Set `AUTOTRADER_SENTRY_DSN=...` to opt in. The SDK ships with the
image so toggling Sentry on never requires a rebuild.

Defaults:

- `AUTOTRADER_SENTRY_ENVIRONMENT=production`
- `AUTOTRADER_SENTRY_TRACES_SAMPLE_RATE=0.0` (errors only — no
  performance traces)

The init scrubs PII (`send_default_pii=False`); broker credentials
and Telegram session strings never reach a stack frame anyway because
they're decrypted only inside the manager classes and held in
`SecretStr`. Errors thrown elsewhere stop at the stack-trace boundary
of those calls.

## Reconciler

In-memory result-watchers don't survive a restart, and pyquotex
resets its `_active_pending` map on each connect, so even a real
ticket from the previous run can't be tied back to the broker's close
event. On startup the executor expires every `pending` row with a
clear note explaining this.

In practice that means:

- After a restart, the dashboard shows yesterday's still-open trades
  as `expired`. Check the broker UI directly if you need their
  outcomes — they really did fire / settle, we just can't tag them.
- The martingale ladder may now be one step ahead of reality. Use
  the **Reset** button on the Streaks card if the recovery sequence
  looks off.

## Common failure modes

### "broker is on REAL but AUTOTRADER_LIVE_TRADING_ENABLED is false"

The dashboard rejects every signal with this exact reason. Open
`.env`, set `AUTOTRADER_LIVE_TRADING_ENABLED=true`, restart the api
container. The flag is intentionally env-only so a corrupted DB row
can't enable real trading.

### "concurrency cap N reached (open=N)"

Pending trades are pinning the cap. Two cases:

1. The reconciler hasn't run because the api container is still
   booting — wait 5–10s.
2. A scheduled trade hasn't fired or hasn't settled yet. That's
   normal until `fire_at + duration` has passed.

If neither applies, look for `pending` rows in the Trades feed older
than the duration; they're ghost rows from a watcher that died
unexpectedly. A restart re-runs the reconciler and clears them.

### Pyrogram "Peer id invalid: -100…"

Pyrogram 2.0.106 hard-codes `MIN_CHANNEL_ID = -1_002_147_483_647`,
which Telegram has long since exceeded. The autotrader patches it at
import time (`telegram_manager._pyro_utils.MIN_CHANNEL_ID`). If you
fork pyrogram or pin a different version and see this error, replicate
the patch or migrate to `hydrogram` / `pyrofork`.

### WebSocket flips to "offline"

The reverse proxy is dropping idle WS connections. Bump
`proxy_read_timeout` (Nginx) or check that Caddy didn't get
overridden by a `proxy_idle_timeout` directive. The dashboard auto-
reconnects with exponential backoff (1→2→4→8s) and falls back to
15s polling, so this is degraded-but-working rather than broken.

### Telegram code expired / invalid

Pyrogram lets you resubmit codes without re-issuing an SMS. If the
code is genuinely expired, click **Cancel** and start the login flow
over.

## Upgrading

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
       up -d --build api web
```

The api container runs `init_db()` on startup which applies in-place
SQLite migrations (ALTER TABLE ADD COLUMN for additive schema
changes). Destructive shape changes drop the affected table — read
the commit message before upgrading if you've got production data.

## Healthcheck

The api container ships with a 30s `/health` healthcheck. The compose
file gates `web → api` on `condition: service_healthy`, so the
dashboard never tries to render against a half-booted backend.
Inspect with:

```bash
docker compose ps
docker inspect autotrader-api --format='{{json .State.Health}}' | jq
```
