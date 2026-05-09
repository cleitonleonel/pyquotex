"""Admin Telegram bot — Pyrogram client lifecycle.

Sibling to :class:`TelegramManager` but for the *admin* bot, not the
ingestion userbot. Owns one Pyrogram bot-mode client; everything
command-related lives in :mod:`admin_bot_commands` and everything
notification-related in :mod:`admin_bot_notify`.

Lifecycle:

    disabled -> (no token at all)
    stopped  -> (token present, start() not yet called or stop() called)
    running  -> (start() succeeded; client is connected)
    error    -> (start() raised; ``last_error`` carries why)

The error state is *recoverable* — the rest of the app keeps running.
The dashboard surfaces the state via ``GET /admin-bot/status``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

State = Literal["disabled", "stopped", "running", "error"]


@dataclass(frozen=True, slots=True)
class AdminBotStatus:
    """Public snapshot — safe to serialise to the dashboard."""

    state: State
    bound_user_id: int | None
    last_error: str | None


# Factory signature: ``(bot_token: str) -> Pyrogram-like Client``. Tests
# inject a ``FakePyrogramBot`` factory; production wires the real
# ``pyrogram.Client`` constructor (see ``_default_client_factory``).
ClientFactory = Callable[[str], Any]

# Hook called for every accepted ``/command`` text. Receives the
# Pyrogram client + Message; returns nothing. Set externally so this
# module stays handler-agnostic.
MessageHook = Callable[[Any, Any], Awaitable[None]]
CallbackHook = Callable[[Any, Any], Awaitable[None]]


def _default_client_factory(token: str) -> Any:
    """Production factory — imported lazily so tests don't need pyrogram."""
    from pyrogram import Client  # noqa: PLC0415
    return Client(
        name="autotrader_admin_bot",
        bot_token=token,
        # In-memory session: the bot token *is* the credential, no
        # session string to persist. Restarts re-auth instantly.
        in_memory=True,
    )


class AdminBot:
    """Single warm Pyrogram bot client, async-safe."""

    def __init__(
        self,
        *,
        bot_token: str | None,
        client_factory: ClientFactory | None = None,
        bound_user_id: int | None = None,
    ) -> None:
        self._token = bot_token
        self._factory = client_factory or _default_client_factory
        self._client: Any | None = None
        self._state: State = "disabled" if bot_token is None else "stopped"
        self._last_error: str | None = None
        self._bound_user_id = bound_user_id
        self._on_message: MessageHook | None = None
        self._on_callback: CallbackHook | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def client(self) -> Any | None:
        return self._client

    def status(self) -> AdminBotStatus:
        return AdminBotStatus(
            state=self._state,
            bound_user_id=self._bound_user_id,
            last_error=self._last_error,
        )

    def set_bound_user_id(self, user_id: int | None) -> None:
        """Called from the binding handler when /start succeeds."""
        self._bound_user_id = user_id

    def set_message_hook(self, hook: MessageHook | None) -> None:
        self._on_message = hook

    def set_callback_hook(self, hook: CallbackHook | None) -> None:
        self._on_callback = hook

    async def start(self) -> None:
        """Construct + start the underlying client. Idempotent.

        ``state="disabled"`` (no token) is a no-op success. A start
        failure transitions to ``state="error"`` and is logged but
        never re-raised — the rest of the app must keep running.
        """
        if self._state == "disabled":
            log.info("admin_bot.disabled", reason="no TELEGRAM_BOT_TOKEN set")
            return
        if self._state == "running":
            return
        try:
            self._client = self._factory(self._token or "")
            self._attach_handlers()
            await self._client.start()
            self._state = "running"
            self._last_error = None
            log.info("admin_bot.started", bound_user_id=self._bound_user_id)
        except Exception as exc:  # noqa: BLE001  (we deliberately swallow)
            self._state = "error"
            self._last_error = str(exc)
            log.error("admin_bot.start_failed", error=str(exc))

    async def stop(self) -> None:
        """Stop the client if running. Idempotent across all states."""
        if self._client is None:
            self._state = "stopped" if self._state != "disabled" else "disabled"
            return
        try:
            await self._client.stop()
        except Exception as exc:  # pragma: no cover  (best-effort teardown)
            log.warning("admin_bot.stop_failed", error=str(exc))
        finally:
            self._client = None
            if self._state != "disabled":
                self._state = "stopped"

    async def send(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any | None = None,
    ) -> None:
        """Send a message via the bot client. Raises whatever the
        underlying client raises — the notifier catches Forbidden /
        RPCError to drive its 5-failure backoff."""
        if self._client is None or self._state != "running":
            raise RuntimeError(f"admin bot not running (state={self._state})")
        await self._client.send_message(chat_id, text, reply_markup=reply_markup)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _attach_handlers(self) -> None:
        """Wire MessageHandler + CallbackQueryHandler. Lazy-imports
        pyrogram so tests with a fake client never need it installed."""
        if self._client is None:
            return
        from pyrogram.handlers import (  # noqa: PLC0415
            CallbackQueryHandler,
            MessageHandler,
        )

        async def _on_message(client: Any, message: Any) -> None:
            if self._on_message is not None:
                await self._on_message(client, message)

        async def _on_callback(client: Any, query: Any) -> None:
            if self._on_callback is not None:
                await self._on_callback(client, query)

        self._client.add_handler(MessageHandler(_on_message))
        self._client.add_handler(CallbackQueryHandler(_on_callback))
