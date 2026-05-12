"""Admin-bot OTP relay — handles the broker-PIN challenge from Telegram.

Lifecycle (managed by direct calls from :class:`QuotexManager`):

1. ``on_otp_required(prompt, attempt=1)`` — broker just challenged.
   Relay sends a fresh Telegram message to the bound admin user, asks
   them to reply with the code.
2. ``on_otp_required(prompt, attempt=N>1)`` — broker re-challenged
   (wrong code). Relay EDITS the existing message in place so the
   operator's reply-target stays valid; the message text updates to
   show the new attempt count.
3. ``handle_reply(message)`` — operator sent a Telegram reply
   targeting the active OTP message. Relay extracts digits and
   forwards via ``manager.submit_otp``.
4. ``on_otp_resolved()`` — connect completed successfully. Relay
   edits the message to a terminal "✅ Connected." and clears state.
5. ``on_otp_timeout()`` — 180s window elapsed without resolution.
   Relay edits to "⏰ OTP expired. Reply /reconnect to retry."

The relay owns no durable state — a container restart loses the
in-flight cycle, which is correct (an OTP code in flight isn't
durable data).

Telegram formatting: all messages are plain text. The broker's
prompt string is never interpolated raw into a markdown context, so
we don't need an escaper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from autotrader.models.base import utc_now

log = structlog.get_logger(__name__)

# Total relay-side budget. The manager's _on_otp_callback already
# parks on ``asyncio.wait_for(_otp_future, timeout=180)``; we use the
# same value here only to format the user-visible message.
_OTP_WINDOW_SECONDS = 180

# Operator's reply payload — anywhere from 4 to 8 digits, depending
# on broker. Quotex emits 6 today.
_OTP_DIGIT_PATTERN = re.compile(r"\b(\d{4,8})\b")


@dataclass
class _ActiveCycle:
    """One in-flight OTP cycle. Replaced on every fresh
    ``on_otp_required(attempt=1)``."""

    message_id: int
    chat_id: int
    attempt: int
    expires_at: datetime
    broker_prompt: str


class AdminBotOTPRelay:
    """Translates broker OTP challenges into a Telegram reply UX."""

    def __init__(
        self,
        *,
        manager: Any,
        admin_bot: Any,
        bound_user_id: int | None,
        max_attempts: int = 3,
    ) -> None:
        self._manager = manager
        self._admin_bot = admin_bot
        self._bound_user_id = bound_user_id
        self._max_attempts = max_attempts
        self._active: _ActiveCycle | None = None

    # ------------------------------------------------------------------
    # Entry points called by QuotexManager
    # ------------------------------------------------------------------

    async def on_otp_required(self, prompt: str, attempt: int) -> None:
        """Broker just challenged. Send (attempt=1) or edit (attempt>1)."""
        if not self._can_relay():
            log.info("otp_relay.skipped.bot_unavailable", attempt=attempt)
            return
        if self._bound_user_id is None:
            log.info("otp_relay.skipped.no_bound_user", attempt=attempt)
            return

        if attempt <= 1 or self._active is None:
            await self._start_new_cycle(prompt=prompt)
        else:
            await self._bump_existing_cycle(prompt=prompt, attempt=attempt)

    def owns_reply(self, message: Any) -> bool:
        """Returns True iff this message is a reply targeting the
        relay's active OTP message. Cheap-and-side-effect-free so the
        commands hook can call it on every inbound message.
        """
        if self._active is None:
            return False
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is None:
            return False
        reply_to_id = getattr(reply_to, "id", None)
        return reply_to_id == self._active.message_id

    async def handle_reply(self, message: Any) -> None:
        """Operator replied to the active OTP message. Extract digits
        and submit. Idempotent for stale/no-digit replies — only the
        terminal call into ``manager.submit_otp`` advances the state."""
        if self._active is None:
            return
        if not self.owns_reply(message):
            log.info(
                "otp_relay.reply.stale_target",
                got=getattr(getattr(message, "reply_to_message", None), "id", None),
                active=self._active.message_id,
            )
            return
        text = getattr(message, "text", "") or ""
        match = _OTP_DIGIT_PATTERN.search(text)
        if not match:
            await self._edit_with(
                "❌ No digits found in your reply — reply with just "
                "the code (4–8 digits).",
            )
            return
        code = match.group(1)
        log.info(
            "otp_relay.reply.submitting",
            attempt=self._active.attempt,
            digits=len(code),
        )
        try:
            await self._manager.submit_otp(code)
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.submit_failed", error=str(exc))
            await self._edit_with(
                f"❌ Internal error submitting OTP ({type(exc).__name__}). "
                "Reply /reconnect to retry.",
            )

    async def _edit_with(self, text: str) -> None:
        if self._active is None:
            return
        try:
            await self._admin_bot.edit_message_text(
                self._active.chat_id,
                self._active.message_id,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.edit_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _can_relay(self) -> bool:
        try:
            return self._admin_bot.status().state == "running"
        except Exception:  # noqa: BLE001
            return False

    async def _start_new_cycle(self, *, prompt: str) -> None:
        text = self._format_initial_prompt()
        try:
            msg = await self._admin_bot.send(self._bound_user_id, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.send_failed", error=str(exc))
            self._active = None
            return
        self._active = _ActiveCycle(
            message_id=int(getattr(msg, "id", 0)),
            chat_id=int(self._bound_user_id or 0),
            attempt=1,
            expires_at=utc_now() + timedelta(seconds=_OTP_WINDOW_SECONDS),
            broker_prompt=prompt,
        )
        log.info(
            "otp_relay.prompt_sent",
            message_id=self._active.message_id,
            attempt=1,
        )

    async def _bump_existing_cycle(self, *, prompt: str, attempt: int) -> None:
        assert self._active is not None
        self._active.attempt = attempt
        self._active.broker_prompt = prompt
        text = self._format_retry_prompt(attempt=attempt)
        try:
            await self._admin_bot.edit_message_text(
                self._active.chat_id,
                self._active.message_id,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.edit_failed", error=str(exc))
            return
        log.info(
            "otp_relay.prompt_edited",
            message_id=self._active.message_id,
            attempt=attempt,
        )

    # ------------------------------------------------------------------
    # Message formatting (plain text — no parse_mode)
    # ------------------------------------------------------------------

    def _format_initial_prompt(self) -> str:
        return (
            f"🔐 Broker needs OTP — reply to this message with the "
            f"code we just emailed you ({_OTP_WINDOW_SECONDS}s)."
        )

    def _format_retry_prompt(self, *, attempt: int) -> str:
        return (
            f"❌ Wrong code — reply with the new code we just "
            f"emailed you (attempt {attempt}/{self._max_attempts})."
        )
