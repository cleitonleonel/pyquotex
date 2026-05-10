# Writing parsers

> The autotrader pipeline reads each Telegram message you watch through
> a *parser*. The parser turns prose like _"🟢 BUY EUR/USD 1m"_ into a
> structured signal — asset, direction, duration, optional fire-time
> and stake — that the risk gate and broker executor act on.

Four parser types ship today, each tuned to a different channel
posting style.

| Type | One-liner | Use it when |
|------|-----------|-------------|
| **Template** | Click-to-pick layout: `{DIRECTION} {ASSET} {DURATION}` | Channels post one tidy line per signal. |
| **Regex** | Power-user: full Python regex with named groups | The channel's layout drifts; templates can't capture it. |
| **Prep + Trigger** | Two messages: a prep sets up params, a trigger fires | "PAIR / TIME" line followed by a 👍 / 👎 sticker. |
| **Batch** | One message → many scheduled signals | A daily roster: `01:51 USDBDT-OTC PUT`, etc. |

Multiple parsers can live on the same chat — they're independent
subscribers, not alternatives. Lower **priority** values run first;
the editor's priority field defaults to 100.

---

## 1. Template parser

A template is a string with bracketed placeholders. Whitespace in the
template matches any run of whitespace; everything else is regex-
escaped.

```text
{DIRECTION} {ASSET} {DURATION}
```

Will match `BUY EURUSD 1m`, `SELL EUR/USD 5 min`, `🟢 EUR/USD 60s`.

### Placeholders

| Token | What it captures | Example values |
|-------|------------------|----------------|
| `{ASSET}` | Pair / asset code | `EURUSD`, `EUR/USD`, `USD NGN OTC`, `XAUUSD` |
| `{DIRECTION}` | Buy/sell side | `BUY`, `SELL`, `UP`, `DOWN`, 🟢, 🔴, 👍, 👎 |
| `{DURATION}` | Expiry window | `1m`, `60s`, `M5`, `5 minutes`, bare number `60` |
| `{TIME}` | Scheduled fire time (HH:MM in channel TZ) | `14:30`, `09:05:30` |
| `{STAKE}` | Numeric stake override | `25`, `100.5` |

### Built-in templates

The editor offers click-to-pick presets — pick the closest one and
adjust:

- `{DIRECTION} {ASSET} {DURATION}` → `BUY EURUSD 1m`
- `{ASSET} {DIRECTION} {DURATION}` → `EURUSD BUY 1m`
- `{DIRECTION} {ASSET} expiry {DURATION}` → `🟢 EUR/USD expiry 1m`
- `{DIRECTION} {ASSET} {DURATION} at {TIME}` → `BUY EURUSD 1m at 14:30`
- `{DIRECTION} {ASSET} {DURATION} ${STAKE}` → `SELL GBPUSD 5m $25`

---

## 2. Regex parser

Provide a Python regex with **named groups**. Required:

- `direction`
- `asset`

Optional:

- `duration` — normalised to seconds (default unit: minutes).
- `fire_at` (or `time` — synonym) — for scheduled trades.
- `stake` — numeric override of the parser's default stake.

Anything outside those groups is ignored, so emoji / prose / noise in
your pattern doesn't leak into the structured signal.

```python
^(?P<direction>BUY|SELL)\s+(?P<asset>[A-Z/]+)\s+(?P<duration>\d+m)$
```

The full message text (after newline-joining when multi-message
buffering is on) is searched; the first match wins.

---

## 3. Prep + Trigger parser

For channels that post a *prep* message followed by a *trigger*:

```text
Message 1 (prep)    "🌐 PAIR: USD-NGN OTC   ⏱ TIME: 01 Minute"
Message 2 (trigger) [👍 sticker]   ← direction only
```

The parser:

- runs the **prep** template/regex on every incoming message; when it
  matches, the parser stores asset / duration / stake / fire_at
  per-chat;
- runs the **trigger** template/regex on every incoming message; when
  it matches, the parser combines the stored prep with the trigger's
  direction and emits a `ParsedSignal` immediately. No window wait.

If no trigger arrives within `aggregate_window_seconds` (default
120s, edited as "Prep-to-trigger gap" in the editor), the stored
prep is dropped silently.

### Required groups

| Phase | Required | Optional |
|-------|----------|----------|
| Prep | `asset` | `duration`, `fire_at`, `time`, `stake` |
| Trigger | `direction` | — |

### Sticker tip

Telegram stickers carry an emoji; we surface that emoji as the
message text. So `(?P<direction>👍|👎)` is enough for a "thumbs"
trigger:

```text
prep:    PAIR: {ASSET} TIME: {DURATION} Minute
trigger: {DIRECTION}            ← the placeholder matches 👍 / 👎
```

### Restart caveat

In-memory pending preps don't survive an API restart — half-built
signals straddling a bounce are dropped. The cache also evicts
expired preps automatically.

---

## 4. Batch parser

For one message containing many scheduled signals:

```text
DATE: 07.05.2026
TIMEZONE : UTC/GMT (+06:00)
FUTURE SIGNALS 🕯
📏📏📏📏📏📏📏📏📏📏

01:51 USDBDT-OTC PUT
01:53 USDBDT-OTC PUT
01:55 USDBDT-OTC PUT
01:58 USDBDT-OTC CALL

📏📏📏📏📏📏📏📏📏📏
```

### Header (optional)

Captures DATE + tz_offset that apply to every row. Without a header
the parser uses today's date in the editor's timezone offset.

| Group | Notes |
|-------|-------|
| `date` | `DD.MM.YYYY`, `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`, `DD.MM.YY` |
| `tz_offset` | `+06:00`, `-0500`, `+6`, `+06` — sign + HH(:MM) |

### Row (required)

| Group | Required | Notes |
|-------|----------|-------|
| `time` | yes | `HH:MM` or `HH:MM:SS` in the row, channel TZ |
| `asset` | yes | resolved against the broker catalogue |
| `direction` | yes | matched against the direction normaliser |
| `duration` | optional | per-row override |
| `stake` | optional | per-row override |

The row regex is run with `finditer` over the joined text, so each
match becomes a pending order at the resolved UTC time.

---

## Direction tokens (full table)

The direction normaliser is generous about how channels write the
side. All these resolve to `call`:

```text
buy  up    call    long    bull   bullish   green
high  higher  rise   rising   above
🟢 🟩 📈 ⬆ ⬆️ ↑ 🔼 🔝   👍 👍🏻 👍🏼 👍🏽 👍🏾 👍🏿 ✅ 💚
```

And these to `put`:

```text
sell  down  put  short  bear  bearish  red
low  lower  fall  falling  below
🔴 🟥 📉 ⬇ ⬇️ ↓ 🔽   👎 👎🏻 👎🏼 👎🏽 👎🏾 👎🏿 ❌ ❤
```

If a channel uses a token not on this list, the parser will reject
it as "unrecognised direction". The cleanest fix is to add the
missing word to the regex / template (e.g. include `LONG|SHORT` as
alternation in your direction regex group).

---

## Duration units

Parser durations accept any of these:

| Form | Examples | Resolved |
|------|----------|----------|
| Numeric + unit | `1m`, `60s`, `5 minutes`, `2h` | direct |
| `M`-prefix | `M1`, `M5`, `M15` | minutes (TradingView shorthand) |
| Bare number | `60`, `5` | uses **Default duration unit** (minutes by default) |

Configure the **Default duration (seconds)** field on the editor for
the fallback when nothing is captured at all. Many channels also put
the expiry in a header line — the parser does a best-effort scan
for `<N> minute(s)` / `<N> seconds` / etc. when the row regex doesn't
capture a duration directly.

---

## Asset resolution

Channels write asset names a thousand different ways. The resolver
walks each raw asset through this strategy, in order:

1. **Manual alias** (case-insensitive) from the editor's asset-aliases
   box — explicit override, returns immediately on hit.

The resolver then tokenises the raw asset and checks whether the
trailing token is `OTC` (`USD NGN OTC`, `GOLD OTC`). The result of
that detection feeds every step below:

2. **Exact match** against the broker's known asset catalogue
   (the unaltered raw form, then the cleaned `<base>` or `<base>_otc`).
3. **`_otc` cross-probe** — the broker may only have the OTC variant
   even if the channel didn't say OTC (or vice versa).
4. **Fallback** — preserve the channel's intent: OTC-marked names
   become `<base>_otc`, bare names stay `<base>`.

The live tester shows which path resolved — look for the `asset:
exact / alias / otc / fallback` badge on a successful match.

---

## Trade-mode pin

| Mode | Behaviour |
|------|-----------|
| `live` | Always fires immediately; any `fire_at` extracted from the signal is stripped. |
| `scheduled` | Requires a parsed `fire_at`; rejects live-only signals. |
| `auto` | Default. Uses `fire_at` when present, otherwise live. |

Mix and match per parser when one channel posts both styles.

---

## Martingale recovery

| Field | Notes |
|-------|-------|
| **Enable** | Required to do anything. |
| **Multiplier** | Stake = `base × multiplier^streak`. 2.0 doubles each loss. |
| **Max streak level** | Cap the recovery ladder; 0 = uncapped. |
| **Reset on win** | A win resets `current_streak` to 0. |
| **Auto-recovery** | When ON, a *losing* trade fires an immediate same-asset / same-direction recovery trade with the multiplied stake — without waiting for the channel to send another signal. Mirrors how channels phrase their gale rules ("IF LOSS TAKE 1 STEP MTG (Same Direction Double Amount)"). |

The runtime streak counter lives on a separate row per parser — the
risk module's "Reset streak" button in the dashboard zeroes it.

---

## Why isn't my parser firing? — checklist

If signals from a channel aren't reaching trades, walk this list
top-to-bottom:

1. **Is the chat watched?** `/dashboard/telegram` → confirm the chat
   is in the watched list and toggled on.
2. **Did Pyrogram subscribe the chat?** `/dashboard/pipeline` →
   "Channels subscribed" gauge should equal "Channels watched".
   If they differ, log out + re-login (forces `_prime_peer_cache`)
   or DELETE+POST the watch row (the watch endpoint subscribes on
   each enabled POST).
3. **Is at least one parser enabled on the chat?** `/dashboard/parsers/<chat>`
   → "Enabled" toggle on the parser row.
4. **Master switch on?** `/dashboard/pipeline` → "Pipeline active"
   toggle should be green. Kill switch should NOT be engaged.
5. **What does `/dashboard/decisions` show?** Every dispatch lands
   here — `matched`, `no_match`, `build_failed`, `no_configs`, or
   `pipeline_inactive`. The reason column tells you which step the
   message stopped at.
6. **Live tester** — paste a real channel message into
   `/dashboard/parsers/<chat>/<parser>` and click Test. If the
   tester says "no match" but `/decisions` shows the message
   arriving, your regex/template needs work.
7. **Trade-mode mismatch** — `trade_mode=scheduled` rejects
   signals without a `fire_at`; `trade_mode=live` strips any
   `fire_at` extracted. Check the parser's pin matches the
   channel's posting style.
8. **Risk gate blocked it** — `/dashboard/trades` lists rejected
   attempts with the reason. Daily loss / stake caps, max-concurrent,
   broker-not-connected, REAL gate (env flag), kill switch all show
   here.

---

## Live tester

The editor's right-hand pane is a self-contained replay:

- Each block is one Telegram message — blank lines inside a block
  are kept (real prep messages have them); only a *new block* is a
  message boundary.
- "Add to tester" buttons on the recent-messages panel push real
  channel messages into the pane; click twice to push a prep + a
  sticker as two separate blocks.
- The tester applies asset auto-resolution so you see the same
  `raw → broker` mapping the live executor will use at trade time.
