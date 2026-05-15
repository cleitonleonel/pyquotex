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
from datetime import UTC, datetime, timedelta
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
        bound_user_id: int | None,  # kept for API compatibility; not stored
        max_attempts: int = 3,
    ) -> None:
        self._manager = manager
        self._admin_bot = admin_bot
        # NOTE: bound_user_id is read lazily from admin_bot.status() on
        # every prompt — see _resolve_bound_user_id. The ctor argument
        # is kept for API compatibility but no longer stored.
        # If you must override (tests), pass bound_user_id to AdminBot
        # directly.
        self._max_attempts = max_attempts
        self._active: _ActiveCycle | None = None
        # Latch: when True we've already fired the "gave up after N OTP
        # attempts" alert for this disconnect window. Every subsequent
        # ``on_otp_required`` no-ops until the operator runs /reconnect
        # (which calls :meth:`reset_exhaustion`) or a fresh connect
        # succeeds (which calls :meth:`on_otp_resolved`). Prevents the
        # 03:58-UTC-incident scenario where pyquotex's internal
        # supervisor regenerates a PIN email every 180s forever.
        self._exhausted_window: bool = False

    # ------------------------------------------------------------------
    # Entry points called by QuotexManager
    # ------------------------------------------------------------------

    async def on_otp_required(self, prompt: str, attempt: int) -> None:
        """Broker just challenged. Send (attempt=1) or edit (attempt>1).

        The cap check lives HERE (the entry point) rather than inside
        ``_bump_existing_cycle`` because the timeout path clears
        ``_active`` between attempts — so every fresh prompt was
        previously routing back through ``_start_new_cycle`` and
        bypassing the cap entirely. See production incident
        2026-05-12 (5 PIN emails in 13min).
        """
        if not self._can_relay():
            log.info("otp_relay.skipped.bot_unavailable", attempt=attempt)
            return
        bound_user_id = self._resolve_bound_user_id()
        if bound_user_id is None:
            log.info("otp_relay.skipped.no_bound_user", attempt=attempt)
            return

        if attempt > self._max_attempts:
            await self._handle_exhaustion(
                attempt=attempt, bound_user_id=bound_user_id,
            )
            return

        if attempt <= 1 or self._active is None:
            await self._start_new_cycle(prompt=prompt, bound_user_id=bound_user_id)
        else:
            await self._bump_existing_cycle(prompt=prompt, attempt=attempt)

    async def _handle_exhaustion(
        self, *, attempt: int, bound_user_id: int,
    ) -> None:
        """Fire the terminal 'gave up' Telegram alert exactly once per
        disconnect window. Subsequent calls are silent until
        :meth:`reset_exhaustion` or :meth:`on_otp_resolved` clears
        the latch."""
        if self._exhausted_window:
            log.info(
                "otp_relay.suppressed_after_exhaustion",
                attempt=attempt,
                max_attempts=self._max_attempts,
            )
            return
        # Important #2 (2026-05-13) — close any in-flight prompt BEFORE
        # sending the fresh alert. On the fast over-cap path (no
        # ``on_otp_timeout`` between attempts), ``_active`` is still
        # pointing at a live "reply with the code" prompt — leaving it
        # untouched means the operator sees two contradictory messages:
        # the dying retry prompt and the loud "gave up" alert. Edit
        # the dying prompt to a closing state so the inbox is
        # unambiguous about which one is current.
        if self._active is not None:
            try:
                await self._admin_bot.edit_message_text(
                    self._active.chat_id,
                    self._active.message_id,
                    "⏰ This OTP cycle is closed — see the latest message below.",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("otp_relay.exhaustion_edit_failed", error=str(exc))
            self._active = None
        text = (
            f"❌ Gave up after {self._max_attempts} OTP attempts. "
            f"No reply received. Run /reconnect when you're ready and "
            f"I'll restart the cycle with a fresh code."
        )
        try:
            await self._admin_bot.send(bound_user_id, text)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "otp_relay.exhaustion_alert_send_failed", error=str(exc),
            )
        self._exhausted_window = True
        log.info(
            "otp_relay.exhausted_alert_sent",
            attempt=attempt,
            max_attempts=self._max_attempts,
        )

    def reset_exhaustion(self) -> None:
        """Clear the exhaustion latch. Called by /reconnect (via
        :meth:`QuotexManager.reset_for_manual_reconnect`) so the next
        OTP cycle can relay normally."""
        if self._exhausted_window:
            log.info("otp_relay.exhaustion_reset")
        self._exhausted_window = False

    def owns_reply(self, message: Any) -> bool:
        """Strict predicate: True iff ``message`` is a literal reply-to
        targeting our active OTP prompt's ``message_id``.

        Side-effect-free. Kept for tests + as the strict branch inside
        :meth:`claims_submission`. New routing code should call
        :meth:`claims_submission` instead — it ALSO accepts plain
        digit-only messages, which is what most operators actually send
        on mobile when they forget to use Telegram's Reply gesture.
        """
        if self._active is None:
            return False
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is None:
            return False
        reply_to_id = getattr(reply_to, "id", None)
        return reply_to_id == self._active.message_id

    def claims_submission(self, message: Any) -> bool:
        """Broader predicate: True iff this message looks like an OTP
        submission for our active cycle.

        Routing rules:

        - If ``message.reply_to_message`` is present, the operator made
          an explicit Reply choice. Honor it strictly — only the active
          message_id wins (delegates to :meth:`owns_reply`). A reply to
          the wrong target is a deliberate signal we must NOT override
          with broader text matching.
        - Otherwise (no reply gesture): accept any non-slash-command
          text containing a 4–8 digit run. This is the production fix
          for operators on mobile who just type the code without using
          Telegram's Reply gesture (observed silent-drop, 2026-05-13).

        Sender-id auth is the hook's responsibility — this stays
        side-effect-free. Slash-commands are excluded so ``/reconnect``
        typed mid-cycle still routes to its handler.
        """
        if self._active is None:
            return False
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is not None:
            # Explicit reply gesture: be strict.
            return self.owns_reply(message)
        # No reply gesture: permissive digit fallback.
        text = (getattr(message, "text", "") or "").strip()
        if not text or text.startswith("/"):
            return False
        return bool(_OTP_DIGIT_PATTERN.search(text))

    async def handle_reply(self, message: Any) -> None:
        """Operator submitted an OTP for the active cycle. Extracts
        digits and submits. Idempotent for stale/no-digit messages —
        only the terminal call into ``manager.submit_otp`` advances
        the state."""
        if self._active is None:
            return
        if not self.claims_submission(message):
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

    async def on_otp_resolved(self) -> None:
        """Connect completed successfully. Edit to terminal '✅' and
        clear the cycle. Also clears the exhaustion latch (a successful
        connect is the clean "fresh start" signal — operator may not
        run /reconnect explicitly)."""
        self._exhausted_window = False
        if self._active is None:
            return
        await self._edit_with("✅ Connected.")
        self._active = None

    async def on_otp_timeout(self) -> None:
        """The manager's 180s timer fired. Edit to '⏰ expired' and
        clear. No auto-retry — operator decides via /reconnect."""
        if self._active is None:
            return
        await self._edit_with(
            "⏰ OTP expired. Reply /reconnect to retry.",
        )
        self._active = None

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

    def _resolve_bound_user_id(self) -> int | None:
        """Read the bound admin user_id lazily from the bot.

        The bot's bound_user_id can be set via /start AFTER the relay
        is constructed (first-ever deployment scenario). Caching at
        __init__ would lock us out forever. Resolving on every prompt
        is cheap.
        """
        try:
            return self._admin_bot.status().bound_user_id
        except Exception:  # noqa: BLE001
            return None

    async def _start_new_cycle(self, *, prompt: str, bound_user_id: int) -> None:
        text = self._format_initial_prompt()
        try:
            msg = await self._admin_bot.send(bound_user_id, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("otp_relay.send_failed", error=str(exc))
            self._active = None
            return
        self._active = _ActiveCycle(
            message_id=int(getattr(msg, "id", 0)),
            chat_id=bound_user_id,
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
        """Edit the active prompt in place for a soft re-prompt.

        Cap enforcement is NOT done here — :meth:`on_otp_required` is
        now the sole entry-point check (see commit 6dd5766 / Fix A,
        2026-05-12). Before that, the cap also lived inside this method,
        but the timeout path clears ``_active`` between attempts, which
        meant a fresh prompt would route through ``_start_new_cycle``
        and bypass the cap entirely. The entry-point check closes that
        gap; the per-cycle copy in here became dead code and was
        removed in Important #1 (2026-05-13)."""
        # Defensive guard — replaces a bare ``assert`` so behaviour
        # is identical under ``python -O``. ``_bump_existing_cycle`` is
        # only routed to from ``on_otp_required`` when ``_active`` is
        # truthy, so reaching here without an active cycle indicates
        # a programmer error — log and return rather than crash.
        if self._active is None:
            log.warning("otp_relay.bump_without_cycle", attempt=attempt)
            return
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
            f"🔐 Broker needs OTP — send the code we just emailed you "
            f"(or reply to this message). ({_OTP_WINDOW_SECONDS}s)."
            f"{self._format_stale_suffix()}"
        )

    def _format_retry_prompt(self, *, attempt: int) -> str:
        return (
            f"❌ Wrong code — reply with the new code we just "
            f"emailed you (attempt {attempt}/{self._max_attempts})."
            f"{self._format_stale_suffix()}"
        )

    def _format_stale_suffix(self) -> str:
        """Common suffix appended to every OTP prompt (initial + retry).

        The timestamp + warning tells an operator with multiple unread
        PIN emails which one is current — every retry invalidates the
        previous code, so without this they'd guess. See production
        incident 2026-05-12 (5 PIN emails in 13min, operator asleep)."""
        now = datetime.now(UTC).strftime("%H:%M:%S")
        return (
            f"\n⏰ Issued {now} UTC."
            f"\n⚠️ Only reply to the LATEST OTP message — "
            f"older codes are now invalid."
        )
