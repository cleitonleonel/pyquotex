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
# Command registry
# --------------------------------------------------------------------------


COMMANDS: dict[str, Handler] = {
    "/start": handle_start,
    "/help": handle_help,
    "/whoami": handle_whoami,
    "/status": handle_status,
    "/balance": handle_balance,
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
