# Autotrader Production Runbook

> Last revised 2026-05-15 (Tier-0 production-ready cutover).
> Companion docs:
> * `docs/superpowers/specs/2026-05-14-production-ready-tier0-design.md`
> * `AUDIT_2026-05-13.md` / `FOLLOWUPS_2026-05-13.md`
> * `docs/DEPLOY.md` — TLS, Caddy/Nginx setup, Sentry, full upgrade procedure

Every command in this runbook is copy-pasteable and was cross-verified against
the code shipped in Tasks 1–7 (branch `fix/broker-disconnect-blindness`,
HEAD `0dfa85e`). If a command here doesn't work, the discrepancy is a bug worth
filing — do not edit the runbook in a hurry.

---

## A. Flipping the env var (going live)

Pre-flight checklist — ALL must be true before setting
`AUTOTRADER_LIVE_TRADING_ENABLED=true`:

- 7+ days of demo-mode soak with no Sentry critical events.
- Preflight probes are clean:
  ```bash
  docker logs autotrader-api 2>&1 | grep broker.preflight.ok | wc -l
  ```
  Result must be > 0 and roughly equal the number of connect attempts
  (i.e. every connect that ran a preflight passed it).
- `tests/test_real_money_invariants.py` is green on master.
- Backup retention is healthy (prod overlay enables hourly backups; verify):
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec api ls /data/backups/ | wc -l
  ```
  Should be >= 24 after the first 24 hours in prod.

Flip the switch:

1. Open `.env` on the VPS:
   ```bash
   nano /opt/autotrader/.env
   ```

2. Set the master gate:
   ```
   AUTOTRADER_LIVE_TRADING_ENABLED=true
   ```
   Set conservative caps via the dashboard (Settings page) or via the
   admin bot:
   ```
   /caps loss 10
   /caps stake 5
   /caps concurrent 1
   ```

3. Apply and restart:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```

4. Watch the admin bot Telegram for the first `broker.connect.ok` log line:
   ```bash
   docker logs autotrader-api 2>&1 | grep broker.connect.ok | tail -3
   ```

5. Wait for the first real signal — verify the risk-gate decision shows
   `allow` in the Decisions feed and the trade settles correctly against
   the broker UI history.

Raise caps only after 7 consecutive days with no daily-loss trigger.

---

## B. Broker rejection diagnostics

### B.0 Reading the probe

If you see `broker.connect.rejection_probe` in logs:

```bash
docker logs autotrader-api 2>&1 | grep broker.connect.rejection_probe | tail -1 | jq .
```

Key fields emitted (from the `broker.connect.rejection_probe` event in `quotex_manager.py`):

| Field | Meaning |
|---|---|
| `error_class` | Exception type name, or `"connect_returned_false"` when `client.connect()` returned False rather than raising |
| `raw_error` | Raw exception string or pyquotex's reject reason |
| `impersonate_profile` | The curl_cffi profile in use (value of `AUTOTRADER_BROKER_CURL_CFFI_PROFILE`) |
| `auth_status` | pyquotex AuthStatus enum name at time of failure |
| `ssid_loaded` | Whether a cached SSID was loaded (stale session suspect if true on repeated failure) |
| `is_authenticated` | Whether pyquotex considers the session authenticated |
| `consecutive_otp_failures` | How many OTP failures have accumulated in this connect window |

Decision tree based on the probe fields:

- `error_class` contains `"connect_returned_false"` and `raw_error` mentions
  "Websocket connection rejected":
  → Soft-flagged IP or account. Open a browser in incognito at
  `https://qxbroker.com/en/sign-in`. If that also fails, wait 30 min and
  retry. If still failing after 2 h, contact broker support.
- `broker.preflight.cloudflare_403` in logs:
  → curl_cffi fingerprint regression. Rotate profile — see §B.1.
- `broker.preflight.upstream_5xx` in logs:
  → Broker maintenance. Check broker status page, retry in 5–15 min.
- `impersonate_profile` shows a Chrome variant and keep failing:
  → As of May 2026 every Chrome variant curl_cffi 0.15 raises on
  Cloudflare's current scoring; switch to a Firefox profile (§B.1).
- Any other `error_class`:
  → File an issue with the full probe log dump. Do not guess.

Also check preflight:
```bash
docker logs autotrader-api 2>&1 | grep "broker.preflight\." | tail -5 | jq .
```

### B.1 Rotating the curl_cffi profile

The profile is controlled by `AUTOTRADER_BROKER_CURL_CFFI_PROFILE` (default:
`firefox144`). Sweep candidates by running a one-shot probe against the
broker sign-in URL (same URL the preflight uses: `https://qxbroker.com/en/sign-in`):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm api python -c "
import curl_cffi.requests as r

profiles = [
    'firefox133', 'firefox135', 'firefox136', 'firefox137',
    'firefox144',
    'chrome124', 'chrome131',
]
url = 'https://qxbroker.com/en/sign-in'
for p in profiles:
    try:
        resp = r.get(url, impersonate=p, timeout=8)
        print(p, resp.status_code)
    except Exception as exc:
        print(p, 'ERROR', type(exc).__name__, str(exc)[:60])
"
```

Pick the first profile that returns `200`. Then update `.env`:

```
AUTOTRADER_BROKER_CURL_CFFI_PROFILE=firefox137
```

Restart:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api
```

Verify the preflight passes on the next connect:
```bash
docker logs autotrader-api 2>&1 | grep broker.preflight | tail -3
```
Expect `broker.preflight.ok`.

---

## C. Halt the bot (kill switch)

### C.1 Immediate halt — kill switch only

Send via the admin bot:
```
/killswitch on
```

Registered command: `handle_killswitch` in `admin_bot_commands.py` (`COMMANDS["/killswitch"]` →
`handle_killswitch`). This sets `GlobalSettings.kill_switch_engaged = True`.

Verify it worked:
```
/status
```

Expected reply includes `Kill switch: *ENGAGED*`. Under the hood the risk gate in
`risk_gate.py` blocks every signal with
`reason="kill switch engaged"`, so you will see these in the Decisions feed.

### C.2 Full emergency stop — kill switch + pipeline off

Send via the admin bot:
```
/panic
```

Registered command: `handle_panic` in `admin_bot_commands.py` (`COMMANDS["/panic"]` →
`handle_panic`). This atomically sets `kill_switch_engaged = True` AND
`pipeline_active = False` in a single DB transaction.

Expected reply: `PANIC: kill switch engaged and pipeline turned OFF.`

### C.3 Resume after kill switch

```
/killswitch off
```

To also re-enable the pipeline:
```
/pipeline on
```

Verify:
```
/status
```

Expected: `Kill switch: *off*` and `Pipeline: *ON*`.

---

## D. Restore from backup

Backups are enabled in the prod overlay (hourly, 24 retained) via
`AUTOTRADER_BACKUP_INTERVAL_SECONDS=3600` and `AUTOTRADER_BACKUP_DIR=/data/backups`
(`docker-compose.prod.yml:27–29`). The SQLite volume mounts at `/data` inside
the container, mapping to the Docker-managed volume `autotrader_autotrader-data`
on the host.

Steps:

1. Stop the API container:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml stop api
   ```

2. List available backups (sorted oldest-first; pick the newest safe one):
   ```bash
   sudo ls -lh /var/lib/docker/volumes/autotrader_autotrader-data/_data/backups/
   ```
   Filename pattern: `autotrader-YYYYMMDDTHHMMSSZ.db`

3. Belt-and-braces snapshot of the broken DB:
   ```bash
   sudo cp /var/lib/docker/volumes/autotrader_autotrader-data/_data/autotrader.db \
           /tmp/autotrader.db.broken
   ```

4. Restore the chosen backup:
   ```bash
   sudo cp /var/lib/docker/volumes/autotrader_autotrader-data/_data/backups/autotrader-20260515T030000Z.db \
           /var/lib/docker/volumes/autotrader_autotrader-data/_data/autotrader.db
   ```

5. Restart:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml start api
   ```

6. The reconciler marks every `pending` trade `expired` on restart — that is
   expected. Check the broker UI directly for their real outcomes.
   Reset martingale streaks in the dashboard if the recovery sequence looks off.

> If backups are not present: confirm the prod overlay was used
> (`docker-compose.prod.yml`) and `AUTOTRADER_BACKUP_INTERVAL_SECONDS` is
> non-zero. A stock install with only the base compose file has backups off
> by default (field `backup_interval_seconds` in `config.py`).

---

## E. Reconnect ceiling escalation

Trigger: you see `broker.reconnect_ceiling_reached` in logs (or the admin bot
DMs you `SYSTEM broker broker.reconnect_ceiling_reached`):

```bash
docker logs autotrader-api 2>&1 | grep broker.reconnect_ceiling_reached | tail -1 | jq .
```

Fields emitted by `broker.reconnect_ceiling_reached` in `quotex_manager.py`: `failed_attempts`, `ceiling`.

What this means: the pyquotex reconnect supervisor has been **stopped** and the
manager state machine is now `awaiting_manual_recovery`. The bot is not trading.
The last error string tells the operator:
> "auto reconnect ceiling reached after N attempts (limit M); check account + IP,
> then run /reconnect"

Steps:

1. Check account state — open `https://qxbroker.com/en/sign-in` in a browser.
   If the account is suspended or requires 2FA re-auth, resolve that first.

2. Check VPS IP state (§F.1).

3. Once account and IP are confirmed clean, trigger a fresh connect:
   ```
   /reconnect
   ```
   Registered command: `handle_reconnect` in `admin_bot_commands.py` (`COMMANDS["/reconnect"]` →
   `handle_reconnect`). This calls `QuotexManager.reset_for_manual_reconnect()`
   to clear the OTP-failure gate and reconnect counter, then calls
   `qx.begin_connect()`.

4. Watch for the OTP message in Telegram (if the account requires it), enter
   the code, and confirm `broker.connect.ok`:
   ```bash
   docker logs autotrader-api 2>&1 | grep "broker.connect.ok\|broker.connect.rejection" | tail -3
   ```

5. If `/reconnect` reports "Broker is already connected", the ceiling task
   resolved but state flipped back — check `/status` and proceed normally.

Tune the ceiling if 20 attempts is too aggressive for your network:
```
AUTOTRADER_BROKER_RECONNECT_HARD_CEILING=30   # in .env, then restart api
```
Minimum allowed: 11 (must be above the soft-downgrade threshold of 10).
Maximum allowed: 200.

---

## F. Stale-feed diagnostics

Trigger: `executor.healthgate_blocked reason=stale_feed` in logs
(or Decisions feed shows `healthgate:stale_feed`):

```bash
docker logs autotrader-api 2>&1 | grep executor.healthgate_blocked | tail -5 | jq .
```

Possible `reason` values (emitted by health-gate checks in `quotex_manager.py`):

| reason | Meaning |
|---|---|
| `not_connected` | Manager state machine is not in `"connected"` state |
| `ws_not_authed` | State is `"connected"` but pyquotex's WS auth flag is not set |
| `no_tick_seen` | No realtime-price tick for this asset has arrived since connect |
| `stale_feed` | A tick arrived but it is older than `AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS` |

For `stale_feed` specifically:
- Wait 10–30 s and re-check — a brief WS hiccup is self-healing.
- If it persists for the same asset: the broker may have paused that asset.
  Try a different asset in the dashboard's manual trade panel to test the WS
  generally.
- If all assets are stale but the broker shows connected: lower the threshold
  to rule out a slow feed (restart required):
  ```
  AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS=30   # default is 10
  ```
  Restart:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api
  ```
- If `not_connected` or `ws_not_authed`: the broker is not actually connected;
  run `/reconnect` and follow §E.

### F.1 IP-sweep diagnostic

```bash
curl -sI https://qxbroker.com/en/sign-in | head -1
```

- `HTTP/2 200` — IP is clean.
- `HTTP/2 403` — VPS IP is flagged by Cloudflare. Options: wait 30–60 min for
  the block to expire; use a different egress IP; contact broker support if
  persistent. While the IP is blocked, `broker.preflight.cloudflare_403` will
  fire on every connect attempt, halting the connect before pyquotex burns OTP
  budget.

---

## G. Reference: env vars and admin-bot commands

### G.1 Tier-0 tunable env vars

All three live in `config.py` under the `AUTOTRADER_` prefix (pydantic
`env_prefix="AUTOTRADER_"` + field name uppercased):

| Env var | Default | Range | Purpose |
|---|---|---|---|
| `AUTOTRADER_BROKER_CURL_CFFI_PROFILE` | `firefox144` | any non-empty string | curl_cffi impersonate profile for HTTP login preflight |
| `AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS` | `10` | 1–300 s | Max age of last tick before health gate blocks a trade |
| `AUTOTRADER_BROKER_RECONNECT_HARD_CEILING` | `20` | 11–200 | Failed reconnect attempts before supervisor stops + manual recovery required |
| `AUTOTRADER_LIVE_TRADING_ENABLED` | `false` | `true`/`false` | Master real-money gate |

### G.2 Admin-bot command quick reference

All commands registered in the `COMMANDS` dict in `admin_bot_commands.py`:

| Command | Action |
|---|---|
| `/killswitch on\|off` | Engage / release the kill switch (`GlobalSettings.kill_switch_engaged`) |
| `/panic` | Kill switch **and** pipeline off atomically — fastest emergency stop |
| `/pipeline on\|off` | Enable / disable the signal pipeline |
| `/reconnect` | Reset OTP counters + trigger fresh broker connect cycle |
| `/status` | One-screen pipeline / kill-switch / caps summary |
| `/caps loss\|stake\|concurrent <value>` | Set daily-loss / stake / concurrency caps |
| `/stake <amount>` | Set default stake |
| `/mode demo\|real` | Switch broker account mode (real requires inline confirm) |
| `/trades [N]` | Last N trades (default 10) |
| `/decisions [N]` | Last N parser decisions |
| `/streaks` | Martingale streaks per parser |
| `/channels` | List watched channels |
| `/parsers [chat_id]` | List parsers |

### G.3 Key structured-log events

| Event | Level | Source | What it means |
|---|---|---|---|
| `broker.preflight.ok` | INFO | `quotex_manager.py` | HTTP probe to broker sign-in returned 2xx — safe to proceed with connect |
| `broker.preflight.network_error` | WARNING | `quotex_manager.py` | Network-level failure during preflight — connect proceeds anyway |
| `broker.preflight.cloudflare_403` | ERROR | `quotex_manager.py` | 403 from Cloudflare — rotate curl_cffi profile (§B.1) |
| `broker.preflight.upstream_5xx` | ERROR | `quotex_manager.py` | 5xx from broker — wait for maintenance window |
| `broker.connect.ok` | INFO | `quotex_manager.py` | Broker connected successfully |
| `broker.connect.rejection_probe` | WARNING | `quotex_manager.py` | Connect failed — forensic probe fields attached |
| `broker.reconnect_ceiling_reached` | ERROR | `quotex_manager.py` | Hard ceiling hit — supervisor stopped, manual `/reconnect` required |
| `executor.healthgate_blocked` | WARNING | `executor.py` | Trade blocked by WS health gate; `reason` field is one of `not_connected` / `ws_not_authed` / `no_tick_seen` / `stale_feed` |
| `executor.draining` | INFO | `executor.py` | Executor entered drain mode (shutdown in progress) |
| `lifespan.drain.complete` | INFO | `executor.py` | All in-flight trades drained before shutdown |
| `lifespan.drain.timeout` | WARNING | `executor.py` | Drain timed out; `remaining` shows how many trades were abandoned |
| `pipeline.draining` | INFO | `pipeline.py` | Pipeline drain started |
| `pipeline.refused` | INFO | `pipeline.py` | Signal rejected because pipeline is draining |
| `executor.auto_recovery.skipped` | INFO | `executor.py` | Martingale auto-recovery skipped; `reason` field explains why |

---

## H. Out-of-scope items (see FOLLOWUPS_2026-05-13.md)

These are deferred to future phases. Trigger conditions are the signals to act:

- **Decimal money columns (§A1)** — trigger: $X.99 / $X.01 drift recurs in
  settled trade P&L.
- **Alembic adoption (§A2)** — trigger: a non-additive schema migration is
  needed (column rename, table drop).
- **Event-bus persistence (§A3)** — trigger: a P0 incident requires replay of
  missed events for forensic purposes.
- **Broker auth error taxonomy M6 (§B3)** — trigger: 2 weeks of
  `broker.connect.rejection_probe` data accumulated to justify classification.
- **Frontend retry/backoff (§A4)** — trigger: deploy-window WS disconnects
  cause visible flashes in the dashboard that users complain about.
