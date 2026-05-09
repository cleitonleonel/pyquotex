# Admin Telegram Bot

A separate Telegram bot that lets the operator remote-control the
autotrader from their phone (status, kill switch, channel/parser
pause, risk caps) and pushes trade/risk/system events to a single
bound admin user.

The bot runs *alongside* the userbot (the one that ingests channel
posts) — it never sees channel traffic, only DMs from the bound admin.
Missing or invalid `TELEGRAM_BOT_TOKEN` = the bot is a no-op; the rest
of the autotrader keeps running.

## Setup checklist

1. **Create the bot** — open Telegram, message `@BotFather`, send
   `/newbot`, follow the prompts. Copy the token (looks like
   `123456:ABCdef...`).
2. **Set the env var** — add to your `.env`:

   ```dotenv
   TELEGRAM_BOT_TOKEN=123456:your-token-here
   ```

3. **Restart the container** — `docker compose restart` (or your local
   `uvicorn` process). On startup you should see one of:
   - `admin_bot.started` — token was good, bot is online
   - `admin_bot.disabled` — no token set; the bot is a no-op
   - `admin_bot.start_failed` — token rejected; check the dashboard
     "Admin bot offline" badge for the error
4. **Bind yourself** — open the bot in Telegram (search for the username
   you set in BotFather), send `/start`. The bot replies
   `Bound as admin`.
5. **Walk the menu** — send `/help` to see every command. Try in this
   order:
   - `/status` — read the pipeline / kill switch state
   - `/channels` — list watched channels
   - `/parsers` — list parser configs
   - `/trades 5` — last 5 trades
6. **Place a demo trade** — make sure your broker is connected on
   PRACTICE, dispatch a signal in a watched channel. You should
   receive a `PLACED` notification, then a `WIN` / `LOSS`
   notification when the watcher resolves it.
7. **Trigger a risk rejection** — set a tiny daily-loss cap
   (`/caps loss 1`), let one trade lose, send another signal —
   you should receive a `REJECTED` notification.

## What if I lose the bot?

If you switch Telegram accounts or block the bot, you can re-pair from
the dashboard:

1. Click *Telegram > Admin bot > Unbind* on the dashboard.
2. Open the bot from the new Telegram account, send `/start`.

You can also `POST /admin-bot/unbind` from any HTTP client with the
dashboard auth token.

## Muting noisy classes

The bot pushes four event classes by default. Mute one with:

```text
/notify placed off
/notify settled off
/notify risk_rejected off
/notify system_error off
```

…and re-enable with `on`. Mutes persist across restarts (stored on the
`global_settings` row).

## Rate limiting

Each event class has a token bucket: capacity 5, refill 1/30s. When a
flood empties a bucket, additional events for that class are coalesced
into a single digest message every 60s:

```text
14 risk_rejected events suppressed in last 60s
```

This protects you from an unreadable chat during a flapping broker
connection or a daily-cap breach.

## Send-failure backoff

If the bot fails to DM you 5 times in a row (you blocked it,
deactivated the account, network glitch) it pauses outbound
notifications until you send any message back. All `system.error`
events still go to the structured log — only DM forwarding pauses.
