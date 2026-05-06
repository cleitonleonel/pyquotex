# Changelog

All notable changes to **pyquotex** are documented in this file. Format follows
the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) convention; the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Two fixes on top of the v1.4.0 web API: the Docker build was broken and
the OTP / 2FA flow couldn't surface the broker's email PIN. Pick either
1.4.1 (since it's strictly additive on the webapi) or 1.5.0 (since the
new `/auth/otp` endpoint is genuinely new functionality) when tagging
— Keep a Changelog leaves the choice to the release.

### Added

- **`POST /auth/otp` endpoint** for headless OTP / 2FA login.
  - `POST /auth/connect` no longer awaits `Quotex.connect()` directly;
    it spawns the connect as a background task. If the broker prompts
    for a PIN within `PYQUOTEX_CONNECT_OTP_GRACE` seconds (default 8),
    the response is `202` with `otp_required: true` and an
    `otp_prompt` carrying the broker-localised prompt text.
  - The client then `POST /auth/otp` `{"code": "123456"}`, the
    library's `on_otp_callback` is resolved with the code, and the
    background connect task completes. Final status returned from the
    OTP endpoint: `200` (success) / `502` (broker rejected) / `400`
    (re-prompted on bad format) / `504` (deadline exceeded).
  - `OtpManager` (in `pyquotex.webapi.otp`) bridges the broker
    callback and the HTTP route through a single `asyncio.Future`.
  - New env vars: `PYQUOTEX_OTP_TIMEOUT` (default 300 s),
    `PYQUOTEX_CONNECT_OTP_GRACE` (default 8 s).
  - New fields on `ConnectResponse`: `otp_required: bool`,
    `otp_prompt: str | None` (default `false` / `null` so older
    clients keep parsing).
- 9 new OTP-flow regression tests in `tests/test_webapi/test_otp.py`.
  Full webapi suite: **38 / 38 green**.

### Fixed

- **Stale-SSID reconnect lockup**: After a few hours the broker
  expires the cached SSID server-side. On the next WS drop the TCP +
  WS handshake still succeeds, but `s_authorization` is rejected, so
  `send_ssid()` returns `False` and `auth_status` flips to `FAILED`.
  The reconnect supervisor was *ignoring* that bool — it counted the
  attempt as successful, replayed subscriptions, fired callbacks, and
  exited the loop. Result (matches the user's `/health` output):
  `connected=false, auth_status=FAILED, reconnect_attempts=1,
  successful_reconnects=1`, with no further retry ever. Fix:
  `ReconnectSupervisor._attempt_reconnect()` now treats a `False`
  `send_ssid()` as a failed attempt, clears `state.SSID`, and lets
  the next backoff-iteration's `start_websocket()` re-run the full
  HTTP login via `authenticate()` to obtain a fresh token. New
  regression test:
  `tests/test_reconnect.py::test_supervisor_treats_failed_send_ssid_as_failed_attempt`.
- **Production-quiet logs by default**: the webapi container ran at
  INFO with uvicorn access logging on, producing one log line per WS
  frame and per HTTP request. Defaults are now `WARNING` + access log
  off; two new env vars (`PYQUOTEX_LOG_LEVEL`, `PYQUOTEX_ACCESS_LOG`)
  let operators opt back in when troubleshooting. Chatty third-party
  loggers (`websockets`, `httpx`, `httpcore`, `asyncio`) are pinned at
  least one notch quieter than the user-selected root level so they
  never dominate stdout.
- **Auth-race regression from v1.3.0**: `Quotex.connect()` was
  falsely declaring "Websocket connection rejected." when in fact
  the broker had authenticated successfully ~800 ms after the SSID
  send. v1.2.x had hidden the race behind a 2-second `asyncio.sleep`
  in `_check_connect`; v1.3.0 dropped that sleep for latency without
  replacing it with a proper auth wait.
  Fix: `QuotexAPI.send_ssid()` now clears any stale `auth_changed`
  event, sends the SSID, and waits on `auth_changed` (default 10 s
  timeout). Returns `True` only when `state.auth_status ==
  AUTHENTICATED`. `QuotexAPI.connect()` propagates the failure with
  the broker's `websocket_error_reason` (when set) or
  `"Authorization timeout"`. Reproduced from the live trace — the
  only externally-visible symptom was `/auth/otp` returning
  `502 Broker connect failed after OTP: Websocket connection
  rejected.` immediately after a *correct* PIN had been accepted by
  the broker.
- **`Dockerfile` build failed with permission denied on `rm -rf
  /tmp/wheels`.** The previous Dockerfile switched to the non-root
  user before the `COPY --from=builder /wheels …` step. `COPY` lands
  files as root regardless of the active `USER`, so the unprivileged
  user couldn't clean up the staging directory after `pip install`.
  Reordered: install wheels system-wide while still root, then drop
  privileges before `CMD`. Also dropped the `--user` install flag and
  the `PATH` munging since system-wide is the conventional pattern in
  a container.
- **`POST /auth/connect` returned `500 Internal Server Error` if the
  broker login raised** (e.g. when running in a container where the
  library's `input()` for an emailed PIN can't read from stdin). The
  endpoint now wraps `client.connect()` so any exception is mapped to
  a clean `502 Bad Gateway` with the broker error message — and the
  OTP flow above eliminates the underlying cause for the most common
  case.

### Migration notes

None. Both changes are strictly additive — existing clients see the
same `200 / 502` shape they always did. The `otp_required` field on
`ConnectResponse` defaults to `false`, so dropping it from a parser
is also backward-compatible.

## [1.4.0] — 2026-05-05

Bundled REST + WebSocket API server. Single-tenant: one shared
`Quotex` client lives for the lifetime of the FastAPI app and serves
every request. Optional install via `pip install pyquotex[webapi]` —
the core library has no FastAPI dependency.

### Added

- **`pyquotex.webapi`** — a complete FastAPI application:
  - 9 REST endpoints — `POST /auth/connect`, `GET /account/balance`,
    `GET /account/profile`, `GET /market/candles`,
    `GET /market/historical-candles`, `GET /market/sentiment/{asset}`,
    `POST /trades/buy`, `POST /trades/pending`,
    `POST /trades/pending-at-price`, `GET /trades/{id}/result`,
    `DELETE /trades/{id}`.
  - 2 WebSocket relays — `WS /stream/prices?asset=…` and
    `WS /stream/sentiment?asset=…`. A single broker subscription is
    multiplexed across all WS clients listening to the same asset;
    backpressure handled per-subscriber (oldest-tick-dropped on full
    queue).
  - Public `GET /health` (no auth) for readiness probes — reports
    broker connection state, auth status, reconnect stats, and
    in-flight pending count.
  - `GET /docs` — Swagger UI; `GET /openapi.json` — schema.
  - Bearer-token auth via `X-API-Key` header, `Authorization: Bearer
    …`, or query string (`?api_key=…`) on WS upgrades. **Refuses to
    start without `PYQUOTEX_API_KEY`.**
  - All configuration env-var driven (see `pyquotex/webapi/config.py`).
- **Entrypoint** — `python -m pyquotex.webapi`.
- **`Dockerfile`** — multi-stage non-root image with built-in
  `HEALTHCHECK` against `/health`.
- **`docker-compose.yml`** — single-service deployment; the broker
  credentials and API key come from env or a `.env` file.
- **`docs/en/13. Web API.md`** — endpoint reference, install + run
  instructions, configuration matrix, operational notes, curl + Python
  client snippets.
- **`examples/webapi_demo.py`** — end-to-end runnable demo (REST +
  WebSocket via `httpx` + `websockets`).
- New optional extra `pyquotex[webapi]` (FastAPI + uvicorn[standard] +
  pydantic).

### Tests

- `tests/test_webapi/` — 28 tests using `app.dependency_overrides` to
  inject a fully mocked Quotex client; covers auth (header + bearer +
  query-string), all REST endpoints (success / validation / broker-
  error mapping), the WebSocket relay (key check, initial deltas, mid-
  stream broadcasts, sentiment shape).
- Full unit + webapi suite: **166 / 166 green** (138 prior + 28 new).

### Backward compatibility

100 % additive. No public-API changes to the core library.

## [1.3.0] — 2026-05-05

A focused robustness + speed audit on top of v1.2.1. Nine fixes; 12 new
regression tests; full suite **138/138 green**. All changes are additive — no
public-API breakage.

### Performance

- **`check_connect()` no longer sleeps 2 s on every call.** The unconditional
  `await asyncio.sleep(2)` in `Quotex._check_connect` was a leftover; the
  `auth_status` read is synchronous. Every public method that calls
  `check_connect()` (~9 of them: `get_balance`, `get_candles`, `open_pending`,
  `start_candles_all_size_stream`, …) returns ~2 s sooner.
- **`realtime_price` is `deque(maxlen=1000)`** instead of `defaultdict(list)`
  with `list.pop(0)` capping. O(n) → O(1) per tick. Compounds badly on bursty
  multi-asset price streams.
- **Profile UTC offset cached per session.** `Quotex.open_pending` no longer
  makes an HTTP call to `/api/v1/cabinets/digest` on every pending order. Cache
  is reset on reconnect via the supervisor's `on_reconnect` hook.
- **`start_realtime_sentiment`, `start_candles_one_stream`,
  `start_candles_all_size_stream` are event-driven** (was 200 ms polling
  loops). New events fired by `_on_message`: `sentiment_ready_<asset>`,
  `candle_generated_<asset>_<size>`, `candle_generated_all_<asset>`.
  `start_candles_all_size_stream` also stopped re-issuing `subscribe_all_size`
  on every poll iteration — a known ban-risk pattern.
- **`pending_ticket_map` close-mirror lookup is O(1)** via a new
  `_exec_to_pending` reverse index. Previous code scanned the whole map on
  every close (O(n) per close, O(n²) over a session under high pending
  volume). Defensive fallback scan kept for callers that mutate the forward
  map directly.

### Reliability

- **Concurrent `connect()` calls are serialised via `asyncio.Lock`.** Two
  callers in the same loop used to create duplicate `QuotexAPI` instances and
  authenticate twice — account-lock risk.
- **Heartbeat task signals `status_changed=ERROR` on send failure** instead of
  silently breaking. The reconnect supervisor now wakes immediately on a dead
  heartbeat.
- **`stop_candles_stream(asset)` cleans up the reconnect-replay lists**
  (`subscribe_candle`, `subscribe_candle_all_size`) and the subscribe-once
  cache (`_subscribed_assets`). Long sessions that rotate through assets no
  longer accumulate dead entries that get re-subscribed on every reconnect.
- **Belt-and-suspenders cleanup in the reconnect supervisor.** `_on_close`
  and `ReconnectSupervisor._attempt_reconnect` both clear
  `_active_pending` / `pending_ticket_map` / `_exec_to_pending`, so a hard-
  error path that bypasses `_on_close` can't leak bridge state.

### Tests

- `tests/test_v13_robustness_speed.py` — 12 new regression tests covering
  every fix.

### Documentation

- New "What's new in v1.3.0" section in `docs/en/12. Advanced Features.md`.
- `docs/en/2. Connection and Authentication.md` flags the
  `check_connect()` latency improvement and the `connect()` lock.
- `docs/en/API_REFERENCE.md` signatures refreshed for `open_pending`,
  `open_pending_at_price`, `check_win`, `wait_for_order_close`,
  `stop_candles_stream`, `start_realtime_sentiment`.
- `README.md` adds a v1.3.0 highlights block and refreshes the version
  comparison table.

---

## [1.2.1] — 2026-05-05

### Documentation

- New "Historical Data Depth" section in `docs/en/12. Advanced Features.md`
  explaining the broker's ~200-candle cap and how the existing
  `get_historical_candles()` helper paginates and stitches batches across
  parallel workers. Includes sizing formula, supported periods, progress
  callback, ban-avoidance guidelines, and a SQLite caching recipe.
- `docs/en/4. Market Data Retrieval.md` — callout pointing readers from the
  natural search path to the new section.

No code changes.

---

## [1.2.0] — 2026-05-05

End-to-end-verified pending orders, lifecycle bridge for `check_win`, and
cumulative work since v1.1.0.

### Added

- **OTC pending orders that actually fire.** Wire-spec rewrite verified live
  against `ws2.qxbroker.com`: `openType=0`, `openTime` as ISO 8601 UTC string,
  `timeframe`, `command` as the string `"call"`/`"put"`, `amount` as float.
  Integer `command` values are stored as `null` in the broker DB and the
  trade never executes — fixed.
- **`Quotex.open_pending_at_price()`** — quote-triggered pending mode (fires
  when the asset price hits a level). Marked experimental until live-verified.
- **Pending lifecycle bridge.** `_active_pending`, `pending_ticket_map`, and
  the `_on_message` handlers correlate `s_pending/create` →
  `f_pending/opened` (pre-fire vs. hard-fail distinction) → `s_pending/opened`
  → `s_orders/close`, mirroring the executed-trade close back onto the
  pending ticket so `check_win(pending_id)` returns the broker-settled
  outcome instead of timing out.
- **`Quotex.wait_for_order_close()`** — public alias for `check_win` with a
  clearer name for callers that aren't guessing at win/loss.
- **`buy(... confirm_timeout=10.0)`** — silent failures surface in 10 s
  instead of `duration + 5` (was 305 s on a 5-minute trade).
- **Faster buy + result tracking.** `start_realtime_price`, `check_win`, and
  the buy-confirm wait are event-driven. Subscribe-once cache for repeated
  buys on the same asset. Dropped duplicate `settings/apply` and pre-order
  `tick` heartbeat. Dropped `get_server_time` HTTP round-trip on the buy hot
  path. Typical hot-asset `buy()` round-trip went from ~300–600 ms to
  ~50–150 ms.

### Fixed

- `pending_id` is now extracted from `data["pending"]["ticket"]` (the actual
  broker shape) instead of always-None `data.get("id")`.
- `instruments_follow` no longer duplicates the pending-create payload on
  the wire; emits the real `instruments/follow` event.
- `Quotex.open_pending` accepts ISO 8601 strings without corruption (the
  wrapper used to feed any non-None `open_time` through
  `expiration.get_next_timeframe`, which only knows `"DD/MM HH:MM"`).
- Pending bridge state cleared on every disconnect/reconnect path so stale
  in-flight tickets from a dropped socket can't cross-wire the next pending
  on the same asset.
- Asset-only fallback in the lifecycle handler removed — two pendings on the
  same asset no longer cross-wire.
- Same-UUID closes (the typical `ws2.qxbroker` case where the executed-trade
  UUID equals the pending ticket UUID) no longer fire `order_closed_<id>`
  twice.

### Documentation

- `docs/en/12. Advanced Features.md` — new sections for "Faster Buy + Result
  Tracking" and "Pending Orders (time-based and quote-triggered)".
- `docs/en/3. Trading Operations.md` — pending-orders example rewritten;
  result-tracking section added.
- PT/ES copies of section 12 deleted; localised indexes link back to the
  English advanced-features page (English-only docs going forward).

---

## [1.1.0] — 2026-05-04

First "private features in OSS" release. All additions are opt-in; existing
code keeps working without changes.

### Added

- **`ProxyConfig`** — HTTP/SOCKS proxy + per-host DNS overrides + extra
  headers + browser-impersonating TLS toggle. Plumbed through both the httpx
  HTTP client and the websockets transport.
- **Multilogin profile bootstrap** — `MultiloginConfig` + async
  `MultiloginClient` for the v1 local agent and v3 cloud API. On
  `Quotex.connect()` the profile is started and its proxy + UA inherited
  automatically. Profile is stopped on `close()`.
- **`SentimentMonitor`** — rolling per-asset history, z-score spike
  detection, extreme-bias thresholds, Pearson divergence vs. price,
  cooldown-debounced async/sync callback dispatch.
- **`SentimentStore`** + **`SentimentCorrelationAnalyzer`** — SQLite-backed
  persistence and cross-asset correlation matrix / divergence finder.
- **`ReconnectPolicy` + `ReconnectSupervisor`** — exponential backoff with
  jitter, captured account mode + subscription replay, observability stats,
  custom `on_reconnect` rehydration hook. On by default
  (`auto_reconnect=True`).
- **Optional `curl_cffi` HTTP backend** — install via
  `pip install pyquotex[stealth]`. `ProxyConfig(use_browser_tls=True)`
  switches the HTTP transport to emit a real Chrome/Firefox TLS (JA3/JA4) +
  HTTP/2 fingerprint.
- **Optional `socks` extra** — `pip install pyquotex[socks]` for `socks5://`
  proxy URLs.
- Top-level imports for all new types: `from pyquotex import Quotex,
  ProxyConfig, MultiloginConfig, MultiloginProfile, MultiloginClient,
  SentimentMonitor, SentimentThresholds, SentimentSignal,
  SentimentSnapshot, SentimentStore, SentimentCorrelationAnalyzer,
  ReconnectPolicy, ReconnectStats, ReconnectSupervisor`.

### Documentation

- New `docs/en/12. Advanced Features.md` covering every new capability with
  worked examples.
- `examples/private_features.py` — end-to-end runnable demo of every
  feature.

---

## [1.0.x] — historical

See `git log master -- pyquotex/` for the prior history (`get_historical_candles`,
deep-history audit, optimisation mixin wiring, strategy rewrite).
