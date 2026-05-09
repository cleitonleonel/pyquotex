"""Admin bot command handlers.

Every handler is an ``async`` function with the signature
``async def handle_X(message, services) -> Reply``. Pure functions:
they read state, possibly write to the DB via ``AsyncSessionLocal``,
and return a ``Reply`` describing what the bot should say back.

Handlers never touch the Pyrogram client directly — that's
``admin_bot.py``'s job. This split keeps handlers easy to unit-test
without driving a fake client through a Pyrogram round-trip.

Routing: ``build_message_hook(bot)`` returns the function ``AdminBot``
plugs in via ``set_message_hook``. The hook does the auth gate + lookup
into ``COMMANDS`` then awaits the matching handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from autotrader.db import AsyncSessionLocal
from autotrader.models.base import utc_now
from autotrader.models.settings import GlobalSettings

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Reply:
    """What a handler returns. ``markup`` is an opaque pass-through —
    typically a Pyrogram ``InlineKeyboardMarkup`` but kept ``Any`` so
    handlers can be unit-tested without importing pyrogram."""

    text: str
    markup: Any | None = None


# Handler signature. The second arg is the ``AdminBot`` instance —
# gives handlers access to ``set_bound_user_id`` without dragging app
# state through a global.
Handler = Callable[[Any, Any], Awaitable[Reply]]


# --------------------------------------------------------------------------
# /start — auto-bind + confirm
# --------------------------------------------------------------------------


async def handle_start(message: Any, bot: Any) -> Reply:
    """First ``/start`` binds the admin; subsequent ones reply confirm."""
    sender_id = int(message.from_user.id)
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1)
        if gs is None:
            gs = GlobalSettings(id=1)
            session.add(gs)
        if gs.admin_telegram_user_id is None:
            # First /start ever — bind.
            gs.admin_telegram_user_id = sender_id
            gs.updated_at = utc_now()
            await session.commit()
            bot.set_bound_user_id(sender_id)
            log.info("admin_bot.bound", user_id=sender_id)
            return Reply(
                text=(
                    "Bound as admin.\n"
                    "Send /help to see what I can do."
                ),
            )
        if gs.admin_telegram_user_id == sender_id:
            return Reply(text="Already bound — send /help.")
        # A different user_id is bound. Drop without changing state.
        log.info(
            "admin_bot.bind.rejected",
            sender=sender_id,
            bound=gs.admin_telegram_user_id,
        )
        return Reply(
            text=(
                "This bot is bound to another admin.\n"
                "Ask them to /unbind, or use the dashboard to release it."
            ),
        )


# --------------------------------------------------------------------------
# /help — static command summary (kept in sync by hand)
# --------------------------------------------------------------------------


_HELP_TEXT = (
    "*Admin bot commands*\n"
    "\n"
    "*Read*\n"
    "  /status — pipeline / kill switch / broker / Telegram pulse\n"
    "  /balance — demo + real balances\n"
    "  /trades [N] — last N trades (default 10)\n"
    "  /decisions [N] — last N parser decisions\n"
    "  /streaks — martingale streaks per parser\n"
    "  /channels — list watched channels\n"
    "  /parsers [chat_id] — list parsers (optionally filtered)\n"
    "  /caps — current daily-loss / stake / concurrency caps\n"
    "  /whoami — your Telegram user_id\n"
    "\n"
    "*Write*\n"
    "  /killswitch on|off\n"
    "  /pipeline on|off\n"
    "  /panic — kill switch + pipeline off in one shot\n"
    "  /mode demo|real — switch broker account mode\n"
    "  /stake <amount> — set default stake\n"
    "  /caps loss|stake|concurrent <value>\n"
    "  /notify placed|settled|risk_rejected|system_error on|off\n"
    "  /channel <id> | /parser <id> — details + pause/resume buttons\n"
    "  /unbind — release admin binding (with confirm)\n"
)


async def handle_help(_message: Any, _bot: Any) -> Reply:
    return Reply(text=_HELP_TEXT)


# --------------------------------------------------------------------------
# /whoami
# --------------------------------------------------------------------------


async def handle_whoami(message: Any, _bot: Any) -> Reply:
    sender_id = int(message.from_user.id)
    return Reply(text=f"You are user_id `{sender_id}`.")


# --------------------------------------------------------------------------
# /status — composes a one-screen health summary
# --------------------------------------------------------------------------


async def handle_status(_message: Any, _bot: Any) -> Reply:
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)

    pipeline_label = "ON" if gs.pipeline_active else "OFF"
    kill_label = "ENGAGED" if gs.kill_switch_engaged else "off"

    text = (
        "*Status*\n"
        f"Pipeline: *{pipeline_label}*\n"
        f"Kill switch: *{kill_label}*\n"
        f"Broker: see dashboard /pipeline/status\n"
        f"Default stake: ${gs.default_stake:.2f}\n"
        f"Caps: loss=${gs.daily_max_loss:.2f}, "
        f"stake=${gs.daily_max_stake:.2f}, "
        f"concurrent={gs.max_concurrent_trades}"
    )
    return Reply(text=text)


# --------------------------------------------------------------------------
# /balance — read-only broker balance (best-effort)
# --------------------------------------------------------------------------


async def handle_balance(_message: Any, _bot: Any) -> Reply:
    return Reply(
        text=(
            "*Balance*\n"
            "Live balances are on the dashboard (/balance is wired in "
            "v2 once QuotexManager exposes a cached snapshot)."
        ),
    )


# --------------------------------------------------------------------------
# /trades [N]
# --------------------------------------------------------------------------


def _parse_int_arg(text: str, default: int, min_v: int, max_v: int) -> int:
    """Parse the second token of a command body as an int with bounds.
    Falls back to ``default`` on missing or unparseable input — handlers
    are forgiving about whitespace and stray characters."""
    parts = text.split()
    if len(parts) < 2:
        return default
    try:
        n = int(parts[1])
    except ValueError:
        return default
    return max(min_v, min(max_v, n))


async def handle_trades(message: Any, _bot: Any) -> Reply:
    from autotrader.models.trade_attempt import list_recent  # noqa: PLC0415

    n = _parse_int_arg(message.text, default=10, min_v=1, max_v=50)
    async with AsyncSessionLocal() as session:
        rows = await list_recent(session, limit=n)

    if not rows:
        return Reply(text="No trades yet.")

    lines = ["*Recent trades*"]
    for r in rows:
        marker = {"won": "+", "lost": "-", "pending": "?",
                  "rejected": "x", "refund": "="}.get(r.status, "•")
        pnl = f"{r.profit:+.2f}" if r.profit is not None else "—"
        lines.append(
            f"{marker} {r.asset} {r.direction.upper()} "
            f"{r.duration_seconds}s ${r.stake:.2f} -> {r.status} ({pnl})"
        )
    return Reply(text="\n".join(lines))


# --------------------------------------------------------------------------
# /decisions [N]
# --------------------------------------------------------------------------


def _recent_decisions_snapshot() -> list[dict[str, Any]]:
    """Resolver indirection — tests monkeypatch this. In production
    pulls from ``app.state.pipeline.recent_decisions`` via the
    fastapi-state stash set up in main.py."""
    from autotrader.services.admin_bot_state import get_pipeline  # noqa: PLC0415
    pipeline = get_pipeline()
    if pipeline is None:
        return []
    return pipeline.recent_decisions


async def handle_decisions(message: Any, _bot: Any) -> Reply:
    n = _parse_int_arg(message.text, default=10, min_v=1, max_v=50)
    snapshot = _recent_decisions_snapshot()[:n]
    if not snapshot:
        return Reply(text="No decisions in the ring buffer yet.")
    lines = ["*Recent decisions*"]
    for d in snapshot:
        outcome = d.get("outcome", "?")
        chat_id = d.get("chat_id", "?")
        parser = d.get("parser_name") or "—"
        reasons = "; ".join(d.get("reasons") or [])
        suffix = f" — {reasons}" if reasons else ""
        lines.append(f"{outcome} · chat {chat_id} · {parser}{suffix}")
    return Reply(text="\n".join(lines))


# --------------------------------------------------------------------------
# /streaks
# --------------------------------------------------------------------------


async def handle_streaks(_message: Any, _bot: Any) -> Reply:
    from sqlmodel import select  # noqa: PLC0415

    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        configs_q = await session.exec(
            select(ParserConfig).where(ParserConfig.martingale_enabled == True),  # noqa: E712
        )
        configs = list(configs_q.all())
        states_q = await session.exec(select(MartingaleState))
        states = {s.parser_config_id: s for s in states_q.all()}

    if not configs:
        return Reply(text="No martingale-enabled parsers.")

    lines = ["*Martingale streaks*"]
    for c in configs:
        st = states.get(c.id or 0)
        step = st.current_streak if st else 0
        last = f"${st.last_stake:.2f}" if st and st.last_stake else "—"
        lines.append(
            f"{c.name or f'cfg-{c.id}'} step {step} x{c.martingale_multiplier} "
            f"max={c.martingale_max_streak} last={last}"
        )
    return Reply(text="\n".join(lines))


# --------------------------------------------------------------------------
# Toggle helpers
# --------------------------------------------------------------------------


def _parse_on_off(text: str) -> bool | None:
    """Parse ``on`` / ``off`` (case-insensitive) from the LAST token of
    the command body. Looking at the last token (rather than the second)
    lets the same helper serve both two-arg commands ("/killswitch on")
    and three-arg commands ("/notify settled off"). Returns None when
    neither token matches — caller replies with usage."""
    parts = text.lower().split()
    if len(parts) < 2:
        return None
    last = parts[-1]
    if last in ("on", "true", "1", "engage"):
        return True
    if last in ("off", "false", "0", "disengage"):
        return False
    return None


async def _set_settings_flag(field: str, value: Any) -> GlobalSettings:
    """Mutate one column on the GlobalSettings singleton row."""
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)
        setattr(gs, field, value)
        gs.updated_at = utc_now()
        session.add(gs)
        await session.commit()
        await session.refresh(gs)
        return gs


async def handle_killswitch(message: Any, _bot: Any) -> Reply:
    state = _parse_on_off(message.text)
    if state is None:
        return Reply(text="Usage: /killswitch on | off")
    await _set_settings_flag("kill_switch_engaged", state)
    return Reply(text=f"Kill switch is now *{'ENGAGED' if state else 'off'}*.")


async def handle_pipeline(message: Any, _bot: Any) -> Reply:
    state = _parse_on_off(message.text)
    if state is None:
        return Reply(text="Usage: /pipeline on | off")
    await _set_settings_flag("pipeline_active", state)
    return Reply(text=f"Pipeline is now *{'ON' if state else 'OFF'}*.")


async def handle_panic(_message: Any, _bot: Any) -> Reply:
    """Sets kill_switch=True AND pipeline_active=False in one transaction."""
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)
        gs.kill_switch_engaged = True
        gs.pipeline_active = False
        gs.updated_at = utc_now()
        session.add(gs)
        await session.commit()
    log.warning("admin_bot.panic.engaged")
    return Reply(text="PANIC: kill switch engaged and pipeline turned OFF.")


# --------------------------------------------------------------------------
# /mode demo|real — REAL requires inline-keyboard confirm
# --------------------------------------------------------------------------


def _confirm_keyboard(action: str) -> Any:
    """Build a 2-button inline keyboard. Lazy-imports pyrogram.types
    so unit tests with FakePyrogramBot don't need pyrogram installed."""
    from pyrogram.types import (  # noqa: PLC0415
        InlineKeyboardButton,
        InlineKeyboardMarkup,
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, do it",
                                 callback_data=f"confirm:{action}"),
            InlineKeyboardButton("Cancel", callback_data="cancel"),
        ],
    ])


async def handle_mode(message: Any, _bot: Any) -> Reply:
    parts = message.text.lower().split()
    if len(parts) < 2 or parts[1] not in ("demo", "real", "practice"):
        return Reply(text="Usage: /mode demo | real")
    target = "REAL" if parts[1] == "real" else "PRACTICE"
    if target == "REAL":
        return Reply(
            text="Switch broker to *REAL* money?",
            markup=_confirm_keyboard("mode:real"),
        )
    # Demo flips immediately — no confirmation needed.
    from autotrader.services.admin_bot_state import get_quotex  # noqa: PLC0415
    qx = get_quotex()
    if qx is None:
        return Reply(text="Broker manager not attached.")
    await qx.set_account_mode("PRACTICE")
    return Reply(text="Broker mode set to *PRACTICE*.")


# --------------------------------------------------------------------------
# /channels and /parsers — list with detail-drilldown for inline-keyboard toggles
# --------------------------------------------------------------------------


def _row_keyboard(callback_data_prefix: str, target_id: int, enabled: bool) -> Any:
    """One-row inline keyboard with a single button whose label flips
    with state. Showing both Pause and Resume as separate buttons would
    clutter the chat at scale (tens of channels)."""
    from pyrogram.types import (  # noqa: PLC0415
        InlineKeyboardButton,
        InlineKeyboardMarkup,
    )
    label = "Pause" if enabled else "Resume"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            label, callback_data=f"{callback_data_prefix}:{target_id}:toggle",
        ),
    ]])


async def handle_channels(_message: Any, _bot: Any) -> Reply:
    from sqlmodel import select  # noqa: PLC0415

    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(WatchedChannel).order_by(WatchedChannel.title),  # type: ignore[arg-type]
        )
        rows = list(result.all())

    if not rows:
        return Reply(text="No watched channels.")

    lines = ["*Watched channels*"]
    for r in rows:
        flag = "[on]" if r.enabled else "[paused]"
        lines.append(f"{flag} `{r.chat_id}` {r.title}")
    lines.append("\nTap /channel <id> for per-channel actions.")
    return Reply(text="\n".join(lines))


async def handle_channel_detail(message: Any, _bot: Any) -> Reply:
    """``/channel <id>`` shows one row with the inline pause/resume
    button. The id can be negative — split on whitespace and parse
    the second token; bail if missing."""
    parts = message.text.split()
    if len(parts) < 2:
        return Reply(text="Usage: /channel <chat_id>")
    try:
        chat_id = int(parts[1])
    except ValueError:
        return Reply(text="chat_id must be an integer.")

    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(WatchedChannel, chat_id)

    if row is None:
        return Reply(text=f"No watched channel with chat_id `{chat_id}`.")

    flag = "active" if row.enabled else "paused"
    text = (
        f"*Channel `{chat_id}`*\n"
        f"Title: {row.title}\n"
        f"Type: {row.chat_type}\n"
        f"State: {flag}"
    )
    return Reply(text=text, markup=_row_keyboard("chan", chat_id, row.enabled))


async def handle_parsers(message: Any, _bot: Any) -> Reply:
    from sqlmodel import select  # noqa: PLC0415

    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    parts = message.text.split()
    chat_filter: int | None = None
    if len(parts) >= 2:
        try:
            chat_filter = int(parts[1])
        except ValueError:
            return Reply(text="Usage: /parsers [chat_id]")

    async with AsyncSessionLocal() as session:
        stmt = select(ParserConfig)
        if chat_filter is not None:
            stmt = stmt.where(ParserConfig.chat_id == chat_filter)
        stmt = stmt.order_by(ParserConfig.chat_id, ParserConfig.priority, ParserConfig.id)  # type: ignore[arg-type]
        rows = list((await session.exec(stmt)).all())

    if not rows:
        return Reply(text="No parser configs.")

    lines = ["*Parsers*"]
    for r in rows:
        flag = "[on]" if r.enabled else "[paused]"
        lines.append(
            f"{flag} `{r.id}` chat=`{r.chat_id}` *{r.name or '(unnamed)'}* "
            f"({r.parser_type})"
        )
    lines.append("\nTap /parser <id> for per-parser actions.")
    return Reply(text="\n".join(lines))


async def handle_parser_detail(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) < 2:
        return Reply(text="Usage: /parser <id>")
    try:
        parser_id = int(parts[1])
    except ValueError:
        return Reply(text="parser id must be an integer.")

    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(ParserConfig, parser_id)

    if row is None:
        return Reply(text=f"No parser with id `{parser_id}`.")

    flag = "active" if row.enabled else "paused"
    text = (
        f"*Parser `{parser_id}`*\n"
        f"Name: {row.name or '(unnamed)'}\n"
        f"Chat: `{row.chat_id}`\n"
        f"Type: {row.parser_type}\n"
        f"Stake: ${row.default_stake:.2f}, "
        f"duration: {row.default_duration_seconds}s, mode: {row.trade_mode}\n"
        f"Martingale: enabled={row.martingale_enabled} "
        f"x{row.martingale_multiplier} max={row.martingale_max_streak} "
        f"auto_recovery={row.martingale_auto_recovery}\n"
        f"State: {flag}"
    )
    return Reply(
        text=text,
        markup=_row_keyboard("parser", parser_id, row.enabled),
    )


# --------------------------------------------------------------------------
# /caps and /stake — numeric setters
# --------------------------------------------------------------------------


def _format_caps(gs: GlobalSettings) -> str:
    return (
        "*Caps*\n"
        f"Daily-loss: ${gs.daily_max_loss:.2f}\n"
        f"Daily-stake: ${gs.daily_max_stake:.2f}\n"
        f"Max concurrent: {gs.max_concurrent_trades}\n"
        "(0 = uncapped)"
    )


async def handle_caps(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) == 1:
        async with AsyncSessionLocal() as session:
            gs = await session.get(GlobalSettings, 1) or GlobalSettings(id=1)
        return Reply(text=_format_caps(gs))

    if len(parts) < 3:
        return Reply(text="Usage: /caps loss|stake|concurrent <value>")
    sub = parts[1].lower()
    raw = parts[2]
    field_map = {
        "loss": ("daily_max_loss", float),
        "stake": ("daily_max_stake", float),
        "concurrent": ("max_concurrent_trades", int),
    }
    spec = field_map.get(sub)
    if spec is None:
        return Reply(text="Usage: /caps loss|stake|concurrent <value>")
    field, parser = spec
    try:
        value = parser(raw)
    except ValueError:
        return Reply(text=f"'{raw}' is not a valid {parser.__name__}.")
    if value < 0:
        return Reply(text="value must be >= 0 (0 = uncapped).")
    gs = await _set_settings_flag(field, value)
    return Reply(text=_format_caps(gs))


async def handle_stake(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) < 2:
        return Reply(text="Usage: /stake <amount>")
    try:
        amount = float(parts[1])
    except ValueError:
        return Reply(text=f"'{parts[1]}' is not a number.")
    if amount <= 0:
        return Reply(text="amount must be > 0.")
    gs = await _set_settings_flag("default_stake", amount)
    return Reply(text=f"Default stake set to ${gs.default_stake:.2f}.")


# --------------------------------------------------------------------------
# /notify <class> on|off
# --------------------------------------------------------------------------


_NOTIFY_FIELDS = {
    "placed": "admin_notify_placed",
    "settled": "admin_notify_settled",
    "risk_rejected": "admin_notify_risk_rejected",
    "system_error": "admin_notify_system_error",
}


async def handle_notify(message: Any, _bot: Any) -> Reply:
    parts = message.text.split()
    if len(parts) < 3:
        return Reply(
            text=(
                "Usage: /notify <class> on|off\n"
                "Classes: placed, settled, risk_rejected, system_error"
            ),
        )
    cls = parts[1].lower()
    field = _NOTIFY_FIELDS.get(cls)
    if field is None:
        return Reply(
            text=(
                f"Unknown class '{cls}'. "
                "Use: placed, settled, risk_rejected, system_error"
            ),
        )
    state = _parse_on_off(message.text)
    if state is None:
        return Reply(text="Usage: /notify <class> on|off")
    await _set_settings_flag(field, state)
    return Reply(
        text=f"Notify *{cls}* is now *{'on' if state else 'off'}*.",
    )


# --------------------------------------------------------------------------
# /unbind — requires confirm
# --------------------------------------------------------------------------


async def handle_unbind(_message: Any, _bot: Any) -> Reply:
    return Reply(
        text=(
            "Release admin binding?\n"
            "After unbind, the next /start from any user re-binds."
        ),
        markup=_confirm_keyboard("unbind"),
    )


async def _confirm_unbind() -> str:
    async with AsyncSessionLocal() as session:
        gs = await session.get(GlobalSettings, 1)
        if gs is not None:
            gs.admin_telegram_user_id = None
            gs.updated_at = utc_now()
            await session.commit()
    # Clear in-memory binding too — both sources of truth must move
    # together so the next /start isn't rejected by stale in-memory state.
    from autotrader.services.admin_bot_state import get_admin_bot  # noqa: PLC0415
    bot = get_admin_bot()
    if bot is not None:
        bot.set_bound_user_id(None)
    return "Unbound."


# --------------------------------------------------------------------------
# Command registry
# --------------------------------------------------------------------------


COMMANDS: dict[str, Handler] = {
    "/start": handle_start,
    "/help": handle_help,
    "/whoami": handle_whoami,
    "/status": handle_status,
    "/balance": handle_balance,
    "/trades": handle_trades,
    "/decisions": handle_decisions,
    "/streaks": handle_streaks,
    "/killswitch": handle_killswitch,
    "/pipeline": handle_pipeline,
    "/panic": handle_panic,
    "/mode": handle_mode,
    "/caps": handle_caps,
    "/stake": handle_stake,
    "/notify": handle_notify,
    "/unbind": handle_unbind,
    "/channels": handle_channels,
    "/channel": handle_channel_detail,
    "/parsers": handle_parsers,
    "/parser": handle_parser_detail,
}


# --------------------------------------------------------------------------
# Hook builder — what AdminBot plugs in
# --------------------------------------------------------------------------


# A single asyncio.Lock serialises command execution. Two simultaneous
# /killswitch on taps from a hyperactive operator must not race.
_dispatch_lock = asyncio.Lock()


def build_message_hook(bot: Any) -> Callable[[Any, Any], Awaitable[None]]:
    """Returns the coroutine ``AdminBot.set_message_hook`` expects.

    Behaviour:
    * Reads the bound user_id from the *bot* (in-memory). Falls back to
      the settings row if unset (covers race: lifespan started before
      the row was migrated).
    * If unbound, only ``/start`` is allowed; everything else is dropped.
    * If bound, only the bound user_id is allowed; everything else is
      dropped silently.
    * Looks up the command in ``COMMANDS`` and awaits the handler under
      ``_dispatch_lock``.
    * Catches handler exceptions and replies with a generic error.
    """

    async def _hook(_client: Any, message: Any) -> None:
        text = (getattr(message, "text", "") or "").strip()
        if not text.startswith("/"):
            return
        # Telegram appends ``@botname`` for group commands; strip it.
        head = text.split(" ", 1)[0]
        if "@" in head:
            head = head.split("@", 1)[0]

        sender_id = int(getattr(message.from_user, "id", 0))
        bound = bot.status().bound_user_id

        if bound is None and head != "/start":
            log.info("admin_bot.dropped.pre_bind", sender=sender_id, command=head)
            return
        if bound is not None and sender_id != bound and head != "/start":
            log.info("admin_bot.dropped.unauthorised", sender=sender_id, command=head)
            return

        # Any accepted command from the bound admin proves the channel
        # is healthy — clear the notifier backoff if it was engaged.
        from autotrader.services.admin_bot_state import get_notifier  # noqa: PLC0415
        notifier = get_notifier()
        if notifier is not None:
            notifier.reset_failures()

        handler = COMMANDS.get(head)
        if handler is None:
            await message.reply_text(
                f"Unknown command: {head}\nSend /help for the list.",
            )
            return

        async with _dispatch_lock:
            try:
                reply = await handler(message, bot)
            except Exception as exc:  # noqa: BLE001  (handler-boundary catch)
                log.exception(
                    "admin_bot.handler_failed", command=head, sender=sender_id,
                )
                await message.reply_text(
                    f"command failed: {type(exc).__name__}",
                )
                return
            await message.reply_text(reply.text, reply_markup=reply.markup)

    return _hook


# --------------------------------------------------------------------------
# Callback routing — InlineKeyboard taps land here
# --------------------------------------------------------------------------


async def _toggle_channel_enabled(chat_id: int) -> bool | None:
    """Flip the WatchedChannel.enabled flag for ``chat_id``. Returns
    the *new* state, or None if the row no longer exists."""
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(WatchedChannel, chat_id)
        if row is None:
            return None
        row.enabled = not row.enabled
        row.updated_at = utc_now()
        await session.commit()
        return row.enabled


async def _toggle_parser_enabled(parser_id: int) -> bool | None:
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    async with AsyncSessionLocal() as session:
        row = await session.get(ParserConfig, parser_id)
        if row is None:
            return None
        row.enabled = not row.enabled
        row.updated_at = utc_now()
        await session.commit()
        return row.enabled


# Confirm-action registry. Mode:real handler defined further below;
# /unbind handler is added in Task 13.
ConfirmHandler = Callable[[], Awaitable[str]]


async def _confirm_mode_real() -> str:
    from autotrader.services.admin_bot_state import get_quotex  # noqa: PLC0415
    qx = get_quotex()
    if qx is None:
        return "Broker manager not attached."
    await qx.set_account_mode("REAL")
    return "Broker mode set to REAL."


CONFIRM_HANDLERS: dict[str, ConfirmHandler] = {
    "mode:real": _confirm_mode_real,
    "unbind": _confirm_unbind,
}


def build_callback_hook(bot: Any) -> Callable[[Any, Any], Awaitable[None]]:
    """Returns the coroutine ``AdminBot.set_callback_hook`` expects.

    Same auth model as ``build_message_hook``: drop callbacks from any
    user_id other than the bound admin (silently — Telegram already
    debounces the button press, and an unauthorised tap shouldn't even
    show an 'answer' toast).
    """

    async def _hook(_client: Any, query: Any) -> None:
        sender_id = int(getattr(query.from_user, "id", 0))
        bound = bot.status().bound_user_id
        if bound is None or sender_id != bound:
            log.info("admin_bot.callback.dropped", sender=sender_id)
            return

        data = (getattr(query, "data", "") or "").strip()
        # Format: ``<kind>:<id>:<action>`` (e.g. ``chan:-1001:toggle``)
        # plus shorter sentinels: ``cancel`` / ``confirm:<action>``.
        if data == "cancel":
            await query.answer("Cancelled.")
            return

        parts = data.split(":")
        async with _dispatch_lock:
            try:
                if parts[0] == "chan" and len(parts) == 3 and parts[2] == "toggle":
                    new_state = await _toggle_channel_enabled(int(parts[1]))
                    if new_state is None:
                        await query.answer("Channel no longer exists.")
                    else:
                        await query.answer(
                            f"Channel {'active' if new_state else 'paused'}",
                        )
                elif parts[0] == "parser" and len(parts) == 3 and parts[2] == "toggle":
                    new_state = await _toggle_parser_enabled(int(parts[1]))
                    if new_state is None:
                        await query.answer("Parser no longer exists.")
                    else:
                        await query.answer(
                            f"Parser {'active' if new_state else 'paused'}",
                        )
                elif parts[0] == "confirm":
                    action = ":".join(parts[1:])
                    handler = CONFIRM_HANDLERS.get(action)
                    if handler is None:
                        await query.answer("Unknown confirm action.")
                        return
                    text = await handler()
                    await query.answer(text)
                else:
                    await query.answer("Unknown action.")
            except Exception as exc:  # noqa: BLE001
                log.exception("admin_bot.callback_failed", data=data)
                await query.answer(f"failed: {type(exc).__name__}")

    return _hook
